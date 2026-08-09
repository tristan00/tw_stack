from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import features as F

RUNS_ROOT = "D:/twdata/runs/human"
GAIN_PARTS = ("settlements", "lord_level", "allies", "vassals")
TARGET_PARTS = GAIN_PARTS + ("survival",)
TARGET_WEIGHTS = {"settlements": 1.0, "lord_level": 1.0, "allies": 1.0, "vassals": 3.0,
                  "survival": 1.0}
SHORT_HORIZON = 3
SHORT_WEIGHT = 0.5

CB_ITERATIONS = 5000
CB_LOSS = "RMSE"

CB_TUNED_FROM = ("catboost_fast_k3 rank1 of 170 finished trials: cv_rmse 1.9575 +/- 0.1623, "
                 "folds [2.1335, 1.7418, 1.9971]; vs combined100-trial-81 production 2.2276 "
                 "+/- 0.4676 on 20260808 corpus (7339 rows, 209 campaigns), paired mean "
                 "-0.2701 SE 0.2827 within noise")

CB_PARAMS = {
    "depth": 7,
    "learning_rate": 0.12882996489398976,
    "l2_leaf_reg": 23.38853026720648,
    "grow_policy": "SymmetricTree",
    "bootstrap_type": "Bernoulli",
    "subsample": 0.8956008103406774,
    "random_strength": 9.475469909298225,
    "border_count": 64,
    "min_data_in_leaf": 1,
    "one_hot_max_size": 255,
    "leaf_estimation_iterations": 1,
}

CB_DEPTH = CB_PARAMS["depth"]
CB_LEARNING_RATE = CB_PARAMS["learning_rate"]
CB_TRAIN_DIR = r"D:\twdata\tmp\catboost"
CB_EARLY_STOPPING = 50
VAL_FRACTION = 0.15
SPLIT_SEED = 0


def grouped_split(n, groups, frac=VAL_FRACTION, seed=SPLIT_SEED):
    import random
    rng = random.Random(seed)
    if not groups or len(groups) != n:
        idx = list(range(n))
        rng.shuffle(idx)
        cut = max(1, int(n * frac)) if n > 20 else 0
        return idx[:cut], idx[cut:]
    uniq = sorted(set(groups))
    if len(uniq) < 3:
        return [], list(range(n))
    rng.shuffle(uniq)
    held = set(uniq[:max(1, int(len(uniq) * frac))])
    val = [i for i, g in enumerate(groups) if g in held]
    trn = [i for i, g in enumerate(groups) if g not in held]
    if not val or not trn:
        return [], list(range(n))
    return val, trn

ISO_ESTIMATORS = 200
ISO_SEED = 0


def params(iterations=None, learning_rate=None):
    return dict(CB_PARAMS,
                learning_rate=float(learning_rate or CB_LEARNING_RATE),
                early_stopping_rounds=CB_EARLY_STOPPING,
                iteration_cap=int(iterations or CB_ITERATIONS),
                loss=CB_LOSS, val_fraction=VAL_FRACTION,
                tuned_from=CB_TUNED_FROM)


def regressor(iterations=None, learning_rate=None):
    from catboost import CatBoostRegressor
    p = dict(CB_PARAMS)
    if learning_rate:
        p["learning_rate"] = float(learning_rate)
    return CatBoostRegressor(iterations=int(iterations or CB_ITERATIONS),
                             loss_function=CB_LOSS, verbose=0, train_dir=CB_TRAIN_DIR, **p)


def isolation_forest():
    from sklearn.ensemble import IsolationForest
    return IsolationForest(n_estimators=ISO_ESTIMATORS, random_state=ISO_SEED, n_jobs=-1)


def fit_es(X, y, cat_idx, groups, tag, report, iterations=None, learning_rate=None):
    from catboost import Pool
    val, trn = grouped_split(len(X), groups)
    m = regressor(iterations=iterations, learning_rate=learning_rate)
    if not val:
        m.fit(Pool(X, y, cat_features=cat_idx))
        report[tag] = {"early_stopping": False, "reason": "too few campaigns to hold out",
                       "iterations": int(m.tree_count_)}
        return m
    Xt = [X[i] for i in trn]
    yt = [y[i] for i in trn]
    Xv = [X[i] for i in val]
    yv = [y[i] for i in val]
    m.fit(Pool(Xt, yt, cat_features=cat_idx),
          eval_set=Pool(Xv, yv, cat_features=cat_idx),
          early_stopping_rounds=CB_EARLY_STOPPING, use_best_model=True, verbose=0)
    best = m.get_best_score() or {}
    report[tag] = {"early_stopping": True, "val_rows": len(val), "train_rows": len(trn),
                   "best_iteration": int(m.get_best_iteration() or 0),
                   "iterations": int(m.tree_count_),
                   "val_rmse": round(float((best.get("validation") or {}).get("RMSE", 0.0)), 6)}
    return m


def _mm0(v, lo, hi):
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.0


def _mm(v, lo, hi):
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.5


def _pct(v, sample):
    n = len(sample)
    if n == 0:
        return 0.5
    below = sum(1 for x in sample if x < v)
    equal = sum(1 for x in sample if x == v)
    return (below + 0.5 * equal) / n


def _sd(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def _ranks(vals):
    n = len(vals)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    order = sorted(range(n), key=lambda i: vals[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        share = (i + j) / 2.0 / (n - 1)
        for k in range(i, j + 1):
            out[order[k]] = share
        i = j + 1
    return out


def future_best(turns, turn, part, horizon=None):
    hi = None if horizon is None else turn + horizon
    vals = [turns[t].get(part) for t in turns
            if t >= turn and (hi is None or t <= hi) and turns[t].get(part) is not None]
    return max(vals) if vals else None


def future_best_at(turns, turn, part, horizon=None):
    hi = None if horizon is None else turn + horizon
    vals = [(turns[t].get(part), t) for t in turns
            if t >= turn and (hi is None or t <= hi) and turns[t].get(part) is not None]
    if not vals:
        return None, None
    mx = max(v for v, _ in vals)
    return mx, min(t for v, t in vals if v == mx)


def decision_deltas(campaign, turns, turn):
    camp = campaign or {}
    turn = int(turn or 0)
    out = {}
    for p in GAIN_PARTS:
        b = F._f(camp.get(p))
        far, far_t = future_best_at(turns, turn, p)
        if b is None or far is None:
            out[p] = None
            continue
        out[p] = far - b
    out["survival"] = turns_left(turns, turn)
    return out


def target(deltas):
    parts = [TARGET_WEIGHTS.get(k, 1.0) * v for k, v in deltas.items() if v is not None]
    if not parts:
        return None
    return float(sum(parts))


def turns_left(turns, turn):
    ts = [int(t) for t in turns if t is not None]
    if not ts:
        return None
    last = max(ts)
    return float(last - int(turn or 0)) if last >= int(turn or 0) else None




def _encode(rows, num, cat, cat_maps=None):
    if cat_maps is None:
        cat_maps = {c: {v: i for i, v in enumerate(sorted({str(r.get(c, "?")) for r in rows}))}
                    for c in cat}
    X = []
    for r in rows:
        v = [(F._f(r.get(c)) or 0.0) for c in num]
        for c in cat:
            mp = cat_maps[c]
            v.append(mp.get(str(r.get(c, "?")), len(mp)))
        X.append(v)
    return X, cat_maps
