from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "reference"))
sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import features as F
from base_model import (CB_DEPTH, CB_EARLY_STOPPING, CB_ITERATIONS, CB_LEARNING_RATE, CB_LOSS,
                        VAL_FRACTION)
from model import MIN_ROWS, RUNS_ROOT, gather

TRAIN_DIR = r"D:\twdata\tmp\catboost_tune"
OUT_DIR = r"D:\twdata\metrics"
STUDY_DIR = r"D:\twdata\metrics\optuna"

PRODUCTION = {"depth": CB_DEPTH, "learning_rate": CB_LEARNING_RATE, "l2_leaf_reg": 3.0,
              "bootstrap_type": "Bayesian", "bagging_temperature": 1.0,
              "random_strength": 1.0, "border_count": 254, "min_data_in_leaf": 1,
              "grow_policy": "SymmetricTree", "one_hot_max_size": 2,
              "leaf_estimation_iterations": 1, "rsm": 1.0}


def _arg(name, default=None):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _flag(name):
    return name in sys.argv


def group_folds(groups, k, seed):
    import random
    uniq = sorted(set(groups))
    if len(uniq) < k:
        return None
    rng = random.Random(seed)
    rng.shuffle(uniq)
    buckets = [set(uniq[i::k]) for i in range(k)]
    folds = []
    for b in buckets:
        val = [i for i, g in enumerate(groups) if g in b]
        trn = [i for i, g in enumerate(groups) if g not in b]
        if val and trn:
            folds.append((val, trn))
    return folds or None


def suggest(trial):
    p = {
        "depth": trial.suggest_int("depth", 3, 10),
        "learning_rate": trial.suggest_float("learning_rate", 0.003, 0.3, log=True),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 0.5, 50.0, log=True),
        "random_strength": trial.suggest_float("random_strength", 0.0, 10.0),
        "border_count": trial.suggest_categorical("border_count", [32, 64, 128, 254]),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 1, 100, log=True),
        "one_hot_max_size": trial.suggest_categorical("one_hot_max_size", [2, 10, 50, 255]),
        "leaf_estimation_iterations": trial.suggest_int("leaf_estimation_iterations", 1, 10),
        "grow_policy": trial.suggest_categorical(
            "grow_policy", ["SymmetricTree", "Depthwise", "Lossguide"]),
        "bootstrap_type": trial.suggest_categorical(
            "bootstrap_type", ["Bayesian", "Bernoulli", "MVS"]),
    }
    if p["bootstrap_type"] == "Bayesian":
        p["bagging_temperature"] = trial.suggest_float("bagging_temperature", 0.0, 10.0)
    else:
        p["subsample"] = trial.suggest_float("subsample", 0.5, 1.0)
    if p["grow_policy"] == "Lossguide":
        p["max_leaves"] = trial.suggest_int("max_leaves", 8, 64, log=True)
        p["depth"] = min(p["depth"], 8)
    if p["grow_policy"] != "SymmetricTree":
        p["rsm"] = trial.suggest_float("rsm", 0.4, 1.0)
    return p


def cv_rmse(X, y, cat_idx, folds, params, cap, log=None, prune=None, threads=None):
    from catboost import CatBoostRegressor, Pool
    scores, iters = [], []
    extra = {} if not threads else {"thread_count": int(threads)}
    for fi, (val, trn) in enumerate(folds):
        m = CatBoostRegressor(iterations=cap, loss_function=CB_LOSS, random_seed=0,
                              verbose=0, train_dir=TRAIN_DIR, allow_writing_files=False,
                              **extra, **params)
        m.fit(Pool([X[i] for i in trn], [y[i] for i in trn], cat_features=cat_idx),
              eval_set=Pool([X[i] for i in val], [y[i] for i in val], cat_features=cat_idx),
              early_stopping_rounds=CB_EARLY_STOPPING, use_best_model=True, verbose=0)
        best = m.get_best_score() or {}
        scores.append(float((best.get("validation") or {}).get("RMSE", float("nan"))))
        iters.append(int(m.get_best_iteration() or 0))
        if prune is not None and prune(statistics.fmean(scores), fi):
            break
    return scores, iters


def summarise(scores, iters):
    return {"cv_rmse": round(statistics.fmean(scores), 6),
            "cv_rmse_sd": round(statistics.pstdev(scores), 6) if len(scores) > 1 else 0.0,
            "folds": len(scores), "fold_rmse": [round(s, 4) for s in scores],
            "best_iteration_mean": round(statistics.fmean(iters), 1) if iters else None}


def main():
    which = _arg("--target", "full")
    if which not in ("full", "state"):
        raise SystemExit("--target must be full or state")
    k = int(_arg("--folds", "5"))
    trials = int(_arg("--trials", "60"))
    cap = int(_arg("--iterations", CB_ITERATIONS))
    seed = int(_arg("--seed", "0"))
    runs_root = _arg("--runs-root", RUNS_ROOT)
    study_name = _arg("--study", "catboost_%s_k%d" % (which, k))
    threads = int(_arg("--threads", "0"))
    log = print

    import warnings
    import optuna
    warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    log("corpus: %s" % runs_root)
    t0 = time.time()
    data = gather(runs_root)
    rows = data["full"] if which == "full" else data["state"]
    y, groups = data["y"], data.get("groups")
    ncamp = len(set(groups or []))
    log("gathered %d rows over %d campaigns in %.0fs" % (len(rows), ncamp, time.time() - t0))
    if len(rows) < MIN_ROWS:
        raise SystemExit("only %d rows -- below the %d the production trainer needs"
                         % (len(rows), MIN_ROWS))
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    folds = group_folds(groups, k, seed)
    if folds is None:
        raise SystemExit("cannot build %d campaign folds from %d campaigns" % (k, ncamp))
    log("columns: %d numeric + %d categorical" % (len(num), len(cat)))
    log("cv: %d-fold grouped by campaign (%d campaigns, ~%d val campaigns per fold)"
        % (len(folds), ncamp, ncamp // len(folds)))
    log("threads: %s" % (threads if threads else "all cores (catboost default)"))
    log("")

    log("baseline: production config over the same folds ...")
    t0 = time.time()
    bs, bi = cv_rmse(X, y, cat_idx, folds, dict(PRODUCTION), cap, threads=threads)
    base = summarise(bs, bi)
    log("  production cv_rmse %.4f +/- %.4f  folds=%s  best_iter %.0f  (%.0fs)"
        % (base["cv_rmse"], base["cv_rmse_sd"], base["fold_rmse"],
           base["best_iteration_mean"], time.time() - t0))
    log("")

    os.makedirs(STUDY_DIR, exist_ok=True)
    storage = "sqlite:///%s" % os.path.join(STUDY_DIR, "studies.db").replace("\\", "/")
    study = optuna.create_study(
        study_name=study_name, storage=storage, load_if_exists=True,
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=seed, n_startup_trials=12,
                                           multivariate=True, group=True))
    done = len([t for t in study.trials if t.state.is_finished()])
    if done:
        log("resuming study %r with %d finished trials" % (study_name, done))

    state = {"n": 0, "t0": time.time()}

    def objective(trial):
        params = suggest(trial)
        cut = base["cv_rmse"] * 1.6

        def prune(mean_so_far, fold_i):
            return fold_i >= 1 and mean_so_far > cut

        t1 = time.time()
        scores, iters = cv_rmse(X, y, cat_idx, folds, params, cap, prune=prune, threads=threads)
        s = summarise(scores, iters)
        trial.set_user_attr("summary", s)
        trial.set_user_attr("params_full", params)
        state["n"] += 1
        flag = "" if s["folds"] == len(folds) else "  (pruned after %d folds)" % s["folds"]
        log("  trial %3d  cv_rmse %8.4f +/- %-7.4f  depth=%-2s lr=%-6.4f l2=%-6.2f %-14s %-13s %4.0fs%s"
            % (state["n"], s["cv_rmse"], s["cv_rmse_sd"], params["depth"],
               params["learning_rate"], params["l2_leaf_reg"], params["grow_policy"],
               params["bootstrap_type"], time.time() - t1, flag))
        return s["cv_rmse"]

    log("optuna TPE: %d trials, pruning any config 1.6x worse than production after 2 folds"
        % trials)
    study.optimize(objective, n_trials=trials, show_progress_bar=False)
    log("")

    complete = [t for t in study.trials if t.value is not None]
    complete.sort(key=lambda t: t.value)
    log("=" * 92)
    log("TOP 10 (cv_rmse, lower is better; production %.4f +/- %.4f)"
        % (base["cv_rmse"], base["cv_rmse_sd"]))
    for i, t in enumerate(complete[:10]):
        s = t.user_attrs.get("summary") or {}
        p = t.user_attrs.get("params_full") or {}
        log("  %2d. %8.4f +/- %-7.4f  depth=%-2s lr=%-7.4f l2=%-6.2f %-14s %-10s folds=%s  %+.4f"
            % (i + 1, t.value, s.get("cv_rmse_sd", 0.0), p.get("depth"),
               p.get("learning_rate", 0), p.get("l2_leaf_reg", 0), p.get("grow_policy"),
               p.get("bootstrap_type"), s.get("folds"), t.value - base["cv_rmse"]))

    best = complete[0] if complete else None
    log("")
    if best is None:
        log("no completed trials")
        return 1
    bsum = best.user_attrs.get("summary") or {}
    gain = base["cv_rmse"] - best.value
    log("best params: %s" % json.dumps(best.user_attrs.get("params_full"), sort_keys=True))
    log("cv_rmse %.4f vs production %.4f -> %+.4f (%.1f%%)"
        % (best.value, base["cv_rmse"], -gain,
           100.0 * gain / base["cv_rmse"] if base["cv_rmse"] else 0.0))
    noise = max(bsum.get("cv_rmse_sd", 0.0), base["cv_rmse_sd"]) / math.sqrt(max(1, len(folds)))
    log("fold standard error ~%.4f" % noise)
    if gain <= noise:
        log("!! the gain does NOT exceed fold standard error -- treat as no improvement")
    else:
        log("gain exceeds fold standard error -- plausible, confirm with --folds %d --seed 1"
            % (k + 2))

    try:
        imp = optuna.importance.get_param_importances(study)
        log("")
        log("parameter importance:")
        for kk, v in list(imp.items())[:12]:
            log("   %-28s %5.1f%%" % (kk, 100.0 * v))
    except Exception as e:
        log("parameter importance unavailable: %s" % repr(e)[:100])

    os.makedirs(OUT_DIR, exist_ok=True)
    out = _arg("--out", os.path.join(
        OUT_DIR, "catboost_tuning_%s_%s.json" % (which, time.strftime("%Y%m%d_%H%M%S"))))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "runs_root": runs_root,
                   "target": which, "folds": len(folds), "seed": seed, "trials": len(complete),
                   "rows": len(rows), "campaigns": ncamp,
                   "num_columns": len(num), "cat_columns": len(cat),
                   "iteration_cap": cap, "early_stopping_rounds": CB_EARLY_STOPPING,
                   "loss": CB_LOSS, "study": study_name, "storage": storage,
                   "production_config": PRODUCTION, "production": base,
                   "best": {"value": best.value, "params": best.user_attrs.get("params_full"),
                            "summary": bsum},
                   "trials_detail": [{"value": t.value,
                                      "params": t.user_attrs.get("params_full"),
                                      "summary": t.user_attrs.get("summary")}
                                     for t in complete]}, fh, indent=1, default=str)
    log("")
    log("wrote %s" % out)
    log("study persisted to %s (rerun with --study %s to add trials)" % (storage, study_name))
    log("nothing in D:\\twdata\\models was touched -- apply a winner by editing CB_* in "
        "advisor/base_model.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
