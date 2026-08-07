from __future__ import annotations

import itertools
import json
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
                        VAL_FRACTION, grouped_split)
from model import MIN_ROWS, RUNS_ROOT, gather

TRAIN_DIR = r"D:\twdata\tmp\catboost_tune"
OUT_DIR = r"D:\twdata\metrics"

GRIDS = {
    "quick": {"depth": [4, 6, 8], "learning_rate": [0.01, 0.05], "l2_leaf_reg": [3.0]},
    "full": {"depth": [4, 6, 8, 10], "learning_rate": [0.005, 0.01, 0.03, 0.1],
             "l2_leaf_reg": [1.0, 3.0, 9.0]},
    "lr": {"depth": [CB_DEPTH], "learning_rate": [0.005, 0.01, 0.02, 0.05, 0.1, 0.2],
           "l2_leaf_reg": [3.0]},
}


def _arg(name, default=None):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _fit_once(X, y, cat_idx, groups, cfg, seed):
    from catboost import CatBoostRegressor, Pool
    val, trn = grouped_split(len(X), groups, frac=VAL_FRACTION, seed=seed)
    if not val:
        return None
    m = CatBoostRegressor(iterations=int(cfg["iterations"]), depth=int(cfg["depth"]),
                          learning_rate=float(cfg["learning_rate"]),
                          l2_leaf_reg=float(cfg["l2_leaf_reg"]),
                          loss_function=CB_LOSS, random_seed=seed,
                          verbose=0, train_dir=TRAIN_DIR)
    t0 = time.time()
    m.fit(Pool([X[i] for i in trn], [y[i] for i in trn], cat_features=cat_idx),
          eval_set=Pool([X[i] for i in val], [y[i] for i in val], cat_features=cat_idx),
          early_stopping_rounds=CB_EARLY_STOPPING, use_best_model=True, verbose=0)
    best = m.get_best_score() or {}
    return {"val_rmse": float((best.get("validation") or {}).get("RMSE", float("nan"))),
            "best_iteration": int(m.get_best_iteration() or 0),
            "trees": int(m.tree_count_), "seconds": round(time.time() - t0, 1),
            "val_rows": len(val), "train_rows": len(trn),
            "val_campaigns": len({groups[i] for i in val}),
            "train_campaigns": len({groups[i] for i in trn})}


def evaluate(X, y, cat_idx, groups, cfg, seeds, log):
    runs = []
    for s in seeds:
        r = _fit_once(X, y, cat_idx, groups, cfg, s)
        if r is None:
            log("   seed %s: no grouped holdout possible -- too few campaigns" % s)
            return None
        runs.append(r)
    rm = [r["val_rmse"] for r in runs]
    return {"config": dict(cfg), "seeds": list(seeds), "runs": runs,
            "val_rmse_mean": round(statistics.fmean(rm), 6),
            "val_rmse_sd": round(statistics.pstdev(rm), 6) if len(rm) > 1 else 0.0,
            "val_rmse_min": round(min(rm), 6), "val_rmse_max": round(max(rm), 6),
            "best_iteration_mean": round(statistics.fmean(
                [r["best_iteration"] for r in runs]), 1),
            "seconds_total": round(sum(r["seconds"] for r in runs), 1)}


def main():
    which = _arg("--target", "full")
    if which not in ("full", "state"):
        raise SystemExit("--target must be full (the ranker) or state (the E2/state model)")
    grid_name = _arg("--grid", "quick")
    if grid_name not in GRIDS:
        raise SystemExit("--grid must be one of %s" % ", ".join(sorted(GRIDS)))
    seeds = [int(s) for s in str(_arg("--seeds", "0,1,2")).split(",") if s.strip() != ""]
    cap = int(_arg("--iterations", CB_ITERATIONS))
    limit = int(_arg("--max-configs", "0"))
    runs_root = _arg("--runs-root", RUNS_ROOT)

    log = print
    log("corpus: %s" % runs_root)
    t0 = time.time()
    data = gather(runs_root)
    rows = data["full"] if which == "full" else data["state"]
    y, groups = data["y"], data.get("groups")
    log("gathered %d rows over %d campaigns in %.0fs (%d decisions, %d unlabelled skipped)"
        % (len(rows), len(set(groups or [])), time.time() - t0,
           data.get("n_decisions", 0), data.get("skipped_unlabelled", 0)))
    if len(rows) < MIN_ROWS:
        log("!! only %d rows -- below the %d the production trainer needs; results will be noise"
            % (len(rows), MIN_ROWS))
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    log("columns: %d numeric + %d categorical" % (len(num), len(cat)))
    log("holdout: grouped by campaign, frac=%s, seeds=%s" % (VAL_FRACTION, seeds))
    log("")

    g = GRIDS[grid_name]
    combos = [dict(zip(("depth", "learning_rate", "l2_leaf_reg"), c), iterations=cap)
              for c in itertools.product(g["depth"], g["learning_rate"], g["l2_leaf_reg"])]
    prod = {"depth": CB_DEPTH, "learning_rate": CB_LEARNING_RATE, "l2_leaf_reg": 3.0,
            "iterations": cap}
    combos = [prod] + [c for c in combos if c != prod]
    if limit:
        combos = combos[:limit]
    log("grid %r: %d configs x %d seeds = %d fits (production config first)"
        % (grid_name, len(combos), len(seeds), len(combos) * len(seeds)))
    log("")

    results = []
    for i, cfg in enumerate(combos):
        tag = "depth=%s lr=%s l2=%s" % (cfg["depth"], cfg["learning_rate"], cfg["l2_leaf_reg"])
        is_prod = cfg == prod
        r = evaluate(X, y, cat_idx, groups, cfg, seeds, log)
        if r is None:
            return 1
        r["production"] = is_prod
        results.append(r)
        log("[%2d/%2d] %-34s val_rmse %8.4f +/- %-7.4f  best_iter %6.1f  %5.0fs%s"
            % (i + 1, len(combos), tag, r["val_rmse_mean"], r["val_rmse_sd"],
               r["best_iteration_mean"], r["seconds_total"], "   <- production" if is_prod else ""))

    results.sort(key=lambda r: r["val_rmse_mean"])
    base = next((r for r in results if r["production"]), None)
    log("")
    log("=" * 78)
    log("RANKED (lower val_rmse is better)")
    for i, r in enumerate(results):
        c = r["config"]
        delta = ""
        if base is not None and not r["production"]:
            d = r["val_rmse_mean"] - base["val_rmse_mean"]
            delta = "  %+.4f vs production" % d
        log("  %2d. depth=%-3s lr=%-6s l2=%-4s  %8.4f +/- %.4f%s%s"
            % (i + 1, c["depth"], c["learning_rate"], c["l2_leaf_reg"],
               r["val_rmse_mean"], r["val_rmse_sd"], delta,
               "   <- production" if r["production"] else ""))

    best = results[0]
    log("")
    if base is not None and best["production"]:
        log("production config is already the best on this grid -- no change recommended")
    elif base is not None:
        gain = base["val_rmse_mean"] - best["val_rmse_mean"]
        log("best: %s" % json.dumps(best["config"]))
        log("improvement over production: %.4f val_rmse (%.1f%%)"
            % (gain, 100.0 * gain / base["val_rmse_mean"] if base["val_rmse_mean"] else 0.0))
        if gain < best["val_rmse_sd"]:
            log("!! that gain is SMALLER than the seed-to-seed spread (%.4f) -- not a real win"
                % best["val_rmse_sd"])

    os.makedirs(OUT_DIR, exist_ok=True)
    out = _arg("--out", os.path.join(
        OUT_DIR, "catboost_tuning_%s_%s_%s.json"
        % (which, grid_name, time.strftime("%Y%m%d_%H%M%S"))))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "runs_root": runs_root,
                   "target": which, "grid": grid_name, "seeds": seeds,
                   "rows": len(rows), "campaigns": len(set(groups or [])),
                   "num_columns": len(num), "cat_columns": len(cat),
                   "val_fraction": VAL_FRACTION, "early_stopping_rounds": CB_EARLY_STOPPING,
                   "iteration_cap": cap, "loss": CB_LOSS,
                   "production_config": prod, "results": results}, fh, indent=1, default=str)
    log("")
    log("wrote %s" % out)
    log("nothing in D:\\twdata\\models was touched -- apply a winner by editing CB_* in "
        "advisor/base_model.py and retraining")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
