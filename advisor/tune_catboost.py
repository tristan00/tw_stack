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
from base_model import params as BM_PARAMS
from model import MIN_ROWS, RUNS_ROOT, gather

TRAIN_DIR = r"D:\twdata\tmp\catboost_tune"
OUT_DIR = r"D:\twdata\metrics"
STUDY_DIR = r"D:\twdata\metrics\optuna"

TRAIN_FITS = 4
FAST_ITERATIONS = 200
FAST_MAX_FIT_S = 12.0

_NOT_CATBOOST = ("early_stopping_rounds", "iteration_cap", "loss", "val_fraction", "tuned_from")


def production_params():
    p = {k: v for k, v in BM_PARAMS().items() if k not in _NOT_CATBOOST}
    if p.get("grow_policy") == "SymmetricTree":
        p.pop("rsm", None)
    if p.get("bootstrap_type") == "Bayesian":
        p.pop("subsample", None)
    else:
        p.pop("bagging_temperature", None)
    return p


PRODUCTION = production_params()


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


def suggest(trial, fast=True):
    lr_lo, depth_hi, leaf_hi = (0.04, 8, 5) if fast else (0.003, 10, 10)
    borders = [32, 64, 128] if fast else [32, 64, 128, 254]
    p = {
        "depth": trial.suggest_int("depth", 3, depth_hi),
        "learning_rate": trial.suggest_float("learning_rate", lr_lo, 0.3, log=True),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 0.5, 50.0, log=True),
        "random_strength": trial.suggest_float("random_strength", 0.0, 10.0),
        "border_count": trial.suggest_categorical("border_count", borders),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 1, 100, log=True),
        "one_hot_max_size": trial.suggest_categorical("one_hot_max_size", [2, 10, 50, 255]),
        "leaf_estimation_iterations": trial.suggest_int(
            "leaf_estimation_iterations", 1, leaf_hi),
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
        p["max_leaves"] = trial.suggest_int("max_leaves", 8, 48, log=True)
        p["depth"] = min(p["depth"], 8)
    if p["grow_policy"] != "SymmetricTree":
        p["rsm"] = trial.suggest_float("rsm", 0.4, 1.0)
    return p


def cv_rmse(X, y, cat_idx, folds, params, cap, log=None, prune=None, threads=None,
            max_fit_s=None):
    from catboost import CatBoostRegressor, Pool
    scores, iters, secs = [], [], []
    extra = {} if not threads else {"thread_count": int(threads)}
    for fi, (val, trn) in enumerate(folds):
        m = CatBoostRegressor(iterations=cap, loss_function=CB_LOSS, random_seed=0,
                              verbose=0, train_dir=TRAIN_DIR, allow_writing_files=False,
                              **extra, **params)
        t0 = time.time()
        m.fit(Pool([X[i] for i in trn], [y[i] for i in trn], cat_features=cat_idx),
              eval_set=Pool([X[i] for i in val], [y[i] for i in val], cat_features=cat_idx),
              early_stopping_rounds=CB_EARLY_STOPPING, use_best_model=True, verbose=0)
        secs.append(round(time.time() - t0, 2))
        best = m.get_best_score() or {}
        scores.append(float((best.get("validation") or {}).get("RMSE", float("nan"))))
        iters.append(int(m.get_best_iteration() or 0))
        if max_fit_s and secs[-1] > max_fit_s:
            return scores, iters, secs, "too_slow"
        if prune is not None and prune(statistics.fmean(scores), fi):
            return scores, iters, secs, "worse_than_production"
    return scores, iters, secs, None


def summarise(scores, iters, secs=None, stopped=None):
    out = {"cv_rmse": round(statistics.fmean(scores), 6),
           "cv_rmse_sd": round(statistics.pstdev(scores), 6) if len(scores) > 1 else 0.0,
           "folds": len(scores), "fold_rmse": [round(s, 4) for s in scores],
           "best_iteration_mean": round(statistics.fmean(iters), 1) if iters else None}
    if secs:
        out["fit_s_mean"] = round(statistics.fmean(secs), 2)
        out["fit_s_max"] = round(max(secs), 2)
        out["est_train_s"] = round(statistics.fmean(secs) * TRAIN_FITS, 1)
    if stopped:
        out["stopped"] = stopped
    return out


def main():
    which = _arg("--target", "full")
    if which not in ("full", "state"):
        raise SystemExit("--target must be full or state")
    k = int(_arg("--folds", "5"))
    trials = int(_arg("--trials", "60"))
    fast = "--wide" not in sys.argv
    cap = int(_arg("--iterations", FAST_ITERATIONS if fast else CB_ITERATIONS))
    max_fit_s = float(_arg("--max-fit-s", FAST_MAX_FIT_S if fast else 0)) or None
    seed = int(_arg("--seed", "0"))
    runs_root = _arg("--runs-root", RUNS_ROOT)
    study_name = _arg("--study", "catboost_%s_k%d" % (which, k))
    threads = int(_arg("--threads", "0"))
    os.makedirs(OUT_DIR, exist_ok=True)
    progress = _arg("--progress", os.path.join(
        OUT_DIR, "tune_progress_%s_%s.jsonl" % (which, time.strftime("%Y%m%d_%H%M%S"))))
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
    log("space: %s | iteration cap %d | per-fold fit budget %s"
        % ("FAST (lr>=0.04, depth<=8, border<=128, leaf_est<=5)" if fast else "WIDE",
           cap, ("%.0fs" % max_fit_s) if max_fit_s else "none"))
    log("production config under test: %s" % json.dumps(PRODUCTION, sort_keys=True))
    log("progress log: %s" % progress)
    log("")

    log("baseline: production config over the same folds ...")
    t0 = time.time()
    bs, bi, bsec, _ = cv_rmse(X, y, cat_idx, folds, dict(PRODUCTION), cap, threads=threads)
    base = summarise(bs, bi, bsec)
    log("  production cv_rmse %.4f +/- %.4f  folds=%s  best_iter %.0f  (%.0fs)"
        % (base["cv_rmse"], base["cv_rmse_sd"], base["fold_rmse"],
           base["best_iteration_mean"], time.time() - t0))
    log("  production fit %.2fs/fold -> est %.0fs for a full train (%d fits)"
        % (base["fit_s_mean"], base["est_train_s"], TRAIN_FITS))
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

    state = {"n": 0, "t0": time.time(), "best": None, "best_params": None}

    def emit(row):
        try:
            with open(progress, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
                fh.flush()
        except OSError as e:
            log("!! progress log write failed: %s" % repr(e)[:100])

    emit({"kind": "run", "when": time.strftime("%Y-%m-%d %H:%M:%S"), "study": study_name,
          "target": which, "folds": len(folds), "trials_requested": trials, "threads": threads,
          "rows": len(rows), "campaigns": ncamp, "iteration_cap": cap,
          "production_config": PRODUCTION, "production": base})

    def objective(trial):
        params = suggest(trial, fast=fast)
        cut = base["cv_rmse"] * 1.6

        def prune(mean_so_far, fold_i):
            return fold_i >= 1 and mean_so_far > cut

        t1 = time.time()
        scores, iters, fsec, stopped = cv_rmse(X, y, cat_idx, folds, params, cap, prune=prune,
                                               threads=threads, max_fit_s=max_fit_s)
        s = summarise(scores, iters, fsec, stopped)
        if stopped == "too_slow":
            raise optuna.TrialPruned()
        trial.set_user_attr("summary", s)
        trial.set_user_attr("params_full", params)
        state["n"] += 1
        secs = round(time.time() - t1, 1)
        pruned = s["folds"] != len(folds)
        if state["best"] is None or s["cv_rmse"] < state["best"]:
            state["best"] = s["cv_rmse"]
            state["best_params"] = dict(params)
        emit({"kind": "trial", "trial": state["n"], "number": trial.number,
              "when": time.strftime("%H:%M:%S"), "elapsed_s": round(time.time() - state["t0"], 1),
              "seconds": secs, "pruned": pruned, "params": params,
              "cv_rmse": s["cv_rmse"], "cv_rmse_sd": s["cv_rmse_sd"],
              "fold_rmse": s["fold_rmse"], "folds_run": s["folds"],
              "best_iteration_mean": s["best_iteration_mean"],
              "vs_production": round(s["cv_rmse"] - base["cv_rmse"], 6),
              "running_best": state["best"], "running_best_params": state["best_params"]})
        flag = "" if not pruned else "  (%s after %d folds)" % (stopped or "pruned", s["folds"])
        log("  trial %3d/%-3d  cv_rmse %8.4f +/- %-7.4f  %+.4f vs prod  best %7.4f  "
            "depth=%-2s lr=%-6.4f l2=%-6.2f %-14s %-9s fit %5.2fs/fold est_train %4.0fs%s"
            % (state["n"], trials, s["cv_rmse"], s["cv_rmse_sd"],
               s["cv_rmse"] - base["cv_rmse"], state["best"], params["depth"],
               params["learning_rate"], params["l2_leaf_reg"], params["grow_policy"],
               params["bootstrap_type"], s.get("fit_s_mean") or 0.0,
               s.get("est_train_s") or 0.0, flag))
        sys.stdout.flush()
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
    bf, pf = bsum.get("fold_rmse") or [], base.get("fold_rmse") or []
    if len(bf) == len(pf) and len(bf) > 1:
        d = [a - b for a, b in zip(bf, pf)]
        md = statistics.fmean(d)
        se = statistics.pstdev(d) / math.sqrt(len(d))
        wins = sum(1 for x in d if x < 0)
        log("PAIRED comparison on identical folds (the correct test -- fold difficulty is "
            "common-mode and cancels):")
        log("   per-fold deltas %s" % [round(x, 4) for x in d])
        log("   mean %+.4f, paired SE %.4f, wins %d/%d folds" % (md, se, wins, len(d)))
        if md < 0 and se > 0 and abs(md) > 2 * se:
            log("   -> REAL improvement (%.1f x paired SE, %d/%d folds)"
                % (abs(md) / se, wins, len(d)))
        else:
            log("   -> within paired noise; treat as no improvement")
    else:
        noise = max(bsum.get("cv_rmse_sd", 0.0), base["cv_rmse_sd"]) / math.sqrt(max(1, len(folds)))
        log("fold standard error ~%.4f (unpaired fallback -- fold counts differ)" % noise)
        if gain <= noise:
            log("!! the gain does NOT exceed fold standard error -- treat as no improvement")

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
    emit({"kind": "final", "when": time.strftime("%Y-%m-%d %H:%M:%S"),
          "elapsed_s": round(time.time() - state["t0"], 1), "trials_completed": len(complete),
          "production": base, "best_value": best.value,
          "best_params": best.user_attrs.get("params_full"), "best_summary": bsum,
          "gain_vs_production": round(gain, 6), "fold_standard_error": round(noise, 6),
          "verdict": "improvement" if gain > noise else "within_noise", "report": out})
    log("")
    log("wrote %s" % out)
    log("per-trial log: %s" % progress)
    log("study persisted to %s (rerun with --study %s to add trials)" % (storage, study_name))
    log("nothing in D:\\twdata\\models was touched -- apply a winner by editing CB_* in "
        "advisor/base_model.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
