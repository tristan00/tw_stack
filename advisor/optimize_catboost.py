from __future__ import annotations


import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.DECISIONS)

import features as F
from base_model import CB_ITERATIONS, CB_LOSS, grouped_split

STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_DIR = os.path.join(common.native(common.LOGS_SERVICES), "optuna_catboost")

FIT_CAP_S = {"main": 120.0, "interrupt": 60.0}
CV_FOLDS = 3
CV_TOP = 5
PATIENCE = 10
TUNE_EARLY_STOPPING = 20
SAMPLER_SEED = int(time.time()) % 100000


def _patience_cb(patience=PATIENCE):
    def cb(study, trial):
        try:
            best = study.best_trial.number
        except ValueError:
            return
        stale = sum(1 for t in study.trials
                    if t.state.is_finished() and t.number > best)
        if stale >= patience:
            _log("PATIENCE: no improvement in %d trials since best trial %d -- "
                 "stopping the study" % (stale, best))
            study.stop()
    return cb


def _log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg), flush=True)


def _storage():
    from decisions import pg
    admin = pg.connect(dbname="postgres", autocommit=True)
    try:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname='optuna'").fetchone():
            admin.execute("CREATE DATABASE optuna")
    finally:
        admin.close()
    return "postgresql+psycopg://%s@%s:%d/optuna" % (pg.USER, pg.HOST, pg.PORT)


class _TimeCap:

    def __init__(self, cap_s):
        self.deadline = time.time() + cap_s
        self.hit = False

    def after_iteration(self, info):
        if time.time() >= self.deadline:
            self.hit = True
            return False
        return True


def _space(trial):
    return {"depth": trial.suggest_int("depth", 3, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.3, log=True),
            "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 0.5, 60.0, log=True),
            "grow_policy": "SymmetricTree",
            "bootstrap_type": "Bernoulli",
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "random_strength": trial.suggest_float("random_strength", 0.1, 20.0, log=True),
            "border_count": trial.suggest_categorical("border_count", [32, 64, 128]),
            "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 1, 64, log=True),
            "one_hot_max_size": trial.suggest_categorical("one_hot_max_size", [16, 64, 255]),
            "leaf_estimation_iterations": trial.suggest_int(
                "leaf_estimation_iterations", 1, 4)}


def _fit_val(X, y, cat_idx, val, trn, params, cap_s):
    from catboost import CatBoostRegressor, Pool
    m = CatBoostRegressor(iterations=CB_ITERATIONS, loss_function=CB_LOSS, verbose=0,
                          allow_writing_files=False, **params)
    cap = _TimeCap(cap_s)
    t0 = time.time()
    yv = [y[i] for i in val]
    vpool = Pool([X[i] for i in val], yv, cat_features=cat_idx)
    m.fit(Pool([X[i] for i in trn], [y[i] for i in trn], cat_features=cat_idx),
          eval_set=vpool, early_stopping_rounds=TUNE_EARLY_STOPPING,
          use_best_model=True, verbose=0, callbacks=[cap])
    best = m.get_best_score() or {}
    rmse = float((best.get("validation") or {}).get("RMSE", 0.0))
    pv = list(m.predict(vpool))
    ybar = sum(yv) / len(yv)
    sst = sum((v - ybar) ** 2 for v in yv)
    sse = sum((a - b) ** 2 for a, b in zip(yv, pv))
    return rmse, {"seconds": round(time.time() - t0, 1),
                  "iterations": int(m.tree_count_),
                  "best_iteration": int(m.get_best_iteration() or 0),
                  "val_r2": round(1.0 - sse / sst, 5) if sst > 0 else None,
                  "hit_cap": cap.hit}


def _folds(groups, k=CV_FOLDS):
    import hashlib
    out = []
    for f in range(k):
        held = {g for g in set(groups)
                if int.from_bytes(hashlib.sha1(("cbcv|%s" % g).encode()).digest()[:4],
                                  "big") % k == f}
        val = [i for i, g in enumerate(groups) if g in held]
        trn = [i for i, g in enumerate(groups) if g not in held]
        out.append((val, trn))
    return out


def _matrices_main():
    from advisor import model as AM
    data = AM.gather()
    rows, y, groups = data["full"], data["y"], data["groups"]
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    _log("main corpus: %d rows, %d campaigns, %d features"
         % (len(X), data["campaigns"], len(num) + len(cat)))
    return [("model", X, y, cat_idx, groups)]


def _matrices_interrupt():
    from advisor import interrupt_model as IM
    data = IM.gather()
    rows, y, groups = data["rows"], data["y"], data["groups"]
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    _log("interrupt corpus: %d rows, %d features" % (len(X), len(num) + len(cat)))
    return [("model", X, y, list(range(len(num), len(num) + len(cat))), groups)]


def _trial_score(mats, params, cap_s, splits):
    parts = {}
    for (tag, X, y, cat_idx, groups), (val, trn) in zip(mats, splits):
        rmse, info = _fit_val(X, y, cat_idx, val, trn, params, cap_s)
        parts[tag] = dict(info, val_rmse=round(rmse, 6))
        _log("  fit %s: rmse=%.5f r2=%+0.4f %d/%d rows, %d trees (best %d), %.0fs%s"
             % (tag, rmse, info["val_r2"] or 0.0, len(trn), len(trn) + len(val),
                info["iterations"], info["best_iteration"], info["seconds"],
                " -- HIT THE %.0fs CAP" % cap_s if info["hit_cap"] else ""))
    score = sum(p["val_rmse"] for p in parts.values()) / len(parts)
    return score, parts


def _cv_score(mats, params, cap_s):
    per_fold = []
    for fold_splits in zip(*[_folds(groups) for _tag, _X, _y, _c, groups in mats]):
        score, parts = _trial_score(mats, params, cap_s, list(fold_splits))
        per_fold.append(round(score, 6))
    mean = sum(per_fold) / len(per_fold)
    sd = (sum((v - mean) ** 2 for v in per_fold) / max(1, len(per_fold) - 1)) ** 0.5
    return round(mean, 6), round(sd, 6), per_fold


def run(kind, trials, timeout_s):
    import optuna
    os.makedirs(OUT_DIR, exist_ok=True)
    trail = os.path.join(OUT_DIR, "trials_%s_%s.jsonl" % (kind, STAMP))
    cap_s = FIT_CAP_S[kind]
    mats = _matrices_main() if kind == "main" else _matrices_interrupt()
    splits = [grouped_split(len(X), groups) for _tag, X, _y, _c, groups in mats]
    study = optuna.create_study(
        study_name="catboost_%s_%s" % (kind, STAMP), storage=_storage(),
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=SAMPLER_SEED, multivariate=True))

    def objective(trial):
        p = _space(trial)
        _log("TRIAL %d start %s" % (trial.number, json.dumps(p, sort_keys=True)))
        t0 = time.time()
        score, parts = _trial_score(mats, p, cap_s, splits)
        row = {"trial": trial.number, "params": p, "score": round(score, 6),
               "parts": parts, "seconds": round(time.time() - t0, 1), "ts": time.time()}
        one = parts["model"]
        trial.set_user_attr("r2", one["val_r2"])
        trial.set_user_attr("iterations", one["iterations"])
        trial.set_user_attr("best_iteration", one["best_iteration"])
        trial.set_user_attr("hit_cap", one["hit_cap"])
        trial.set_user_attr("seconds", row["seconds"])
        with open(trail, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        vals = [t.value for t in study.trials if t.value is not None]
        _log("TRIAL %d done score=%.5f %s %.0fs (best so far %.5f)"
             % (trial.number, score,
                " ".join("%s=%.4f/%dit%s" % (t, v["val_rmse"], v["iterations"],
                                             " CAP" if v["hit_cap"] else "")
                         for t, v in parts.items()),
                row["seconds"], min(vals + [score])))
        return score

    _log("%s study: %s trials, %s study timeout, %.0fs fit cap, serial trials, "
         "study patience %d, fit early stopping %d, sampler seed %d"
         % (kind, trials if trials else "unbounded",
            ("%.0fs" % timeout_s) if timeout_s else "none",
            cap_s, PATIENCE, TUNE_EARLY_STOPPING, SAMPLER_SEED))
    study.optimize(objective, n_trials=trials, timeout=timeout_s, gc_after_trial=True,
                   catch=(RuntimeError,), callbacks=[_patience_cb()])
    done = [t for t in study.trials if t.value is not None]
    done.sort(key=lambda t: t.value)
    top = done[:CV_TOP]
    _log("study finished: %d scored trials, CV-checking top %d with %d grouped folds"
         % (len(done), len(top), CV_FOLDS))
    table = []
    for t in top:
        p = dict(t.params, grow_policy="SymmetricTree", bootstrap_type="Bernoulli")
        mean, sd, folds = _cv_score(mats, p, cap_s)
        table.append({"trial": t.number, "split_score": round(t.value, 6),
                      "cv_mean": mean, "cv_sd": sd, "cv_folds": folds, "params": p})
        _log("CV trial %d: split=%.5f cv=%.5f +/- %.5f folds=%s"
             % (t.number, t.value, mean, sd, folds))
    table.sort(key=lambda r: (r["cv_mean"], r["params"]["depth"]))
    winner = table[0] if table else None
    out = {"kind": kind, "stamp": STAMP, "trials_scored": len(done),
           "fit_cap_s": cap_s, "cv_folds": CV_FOLDS, "tune_early_stopping":
           TUNE_EARLY_STOPPING, "top": table, "winner": winner}
    path = os.path.join(OUT_DIR, "best_%s_%s.json" % (kind, STAMP))
    json.dump(out, open(path, "w"), indent=1)
    if winner:
        _log("WINNER trial %d cv=%.5f +/- %.5f params %s"
             % (winner["trial"], winner["cv_mean"], winner["cv_sd"],
                json.dumps(winner["params"], sort_keys=True)))
    _log("report -> %s" % path)
    return 0


if __name__ == "__main__":
    common.require_venv()
    a = sys.argv[1:]
    kind = a[0] if a and a[0] in ("main", "interrupt") else None
    if kind is None:
        raise SystemExit("usage: optimize_catboost.py main|interrupt "
                         "[--trials N] [--timeout S]")
    n = int(a[a.index("--trials") + 1]) if "--trials" in a else None
    t = float(a[a.index("--timeout") + 1]) if "--timeout" in a else None
    raise SystemExit(run(kind, n, t))
