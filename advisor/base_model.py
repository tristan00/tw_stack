from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import features as F

RUNS_ROOT = "D:/twdata/runs/human"
TARGET_PARTS = ("settlements", "lord_level")
SHORT_HORIZON = 3
SHORT_WEIGHT = 0.5

CB_ITERATIONS = 300
CB_DEPTH = 6
CB_LEARNING_RATE = 0.08
CB_LOSS = "RMSE"
CB_TRAIN_DIR = r"D:\twdata\tmp\catboost"

ISO_ESTIMATORS = 200
ISO_SEED = 0


def regressor():
    """A CatBoostRegressor on the shared parameters -- every fit in the stack is this one."""
    from catboost import CatBoostRegressor
    return CatBoostRegressor(iterations=CB_ITERATIONS, depth=CB_DEPTH,
                             learning_rate=CB_LEARNING_RATE, loss_function=CB_LOSS,
                             verbose=0, train_dir=CB_TRAIN_DIR)


def isolation_forest():
    """The explore/novelty forest on the shared parameters."""
    from sklearn.ensemble import IsolationForest
    return IsolationForest(n_estimators=ISO_ESTIMATORS, random_state=ISO_SEED, n_jobs=-1)


def _mm0(v, lo, hi):
    """min-max to [0,1]; a degenerate range gives 0.0."""
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.0


def _mm(v, lo, hi):
    """min-max to [0,1]; a degenerate range gives 0.5."""
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.5


def _pct(v, sample):
    """Mid-rank percentile of `v` within `sample`, in [0,1]."""
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
    """Fractional rank of each value in [0,1], ties sharing the average rank."""
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
    """Best end-of-turn value at or after `turn` -- the decision's own turn included, so a
    within-turn gain counts as OUTCOME, never baseline. `horizon` caps the window at
    turn+horizon; None leaves it open to the end of the campaign."""
    hi = None if horizon is None else turn + horizon
    vals = [turns[t].get(part) for t in turns
            if t >= turn and (hi is None or t <= hi) and turns[t].get(part) is not None]
    return max(vals) if vals else None


def future_best_at(turns, turn, part, horizon=None):
    """(best value in the window, EARLIEST turn attaining it). Earliest, so holding a gain does
    not stretch the timeframe that gain is divided by."""
    hi = None if horizon is None else turn + horizon
    vals = [(turns[t].get(part), t) for t in turns
            if t >= turn and (hi is None or t <= hi) and turns[t].get(part) is not None]
    if not vals:
        return None, None
    mx = max(v for v, _ in vals)
    return mx, min(t for v, t in vals if v == mx)


def decision_deltas(campaign, turns, turn):
    """Per TARGET_PART growth RATE of one decision, blended across two TIMELINES of the SAME
    metric: the near term (best within SHORT_HORIZON turns) and the rest of the campaign. Each
    is the gain over the decision's own snapshot -- the last known pre-decision point -- divided
    by the turns taken TO REACH THAT MAX, so both terms are gain per turn.

    The timeframe runs to the max, never to the end of the campaign: turns after the peak are not
    time the gain took to arrive, and counting them would dilute a decision for how long its
    campaign happened to run on. Blending a short and a long growth rate is what puts the turns
    taken into the label. A payoff landing inside the near window scores on both rates; one that
    only lands far out scores on the long rate alone, over a longer timeframe, so it is worth less
    per turn without being erased. The long term stays unbounded and stays a max, so a campaign
    that peaks and later collapses does not retroactively devalue the actions that built the peak.
    None per part when either side is missing."""
    camp = campaign or {}
    turn = int(turn or 0)
    out = {}
    for p in TARGET_PARTS:
        b = F._f(camp.get(p))
        far, far_t = future_best_at(turns, turn, p)
        if b is None or far is None:
            out[p] = None
            continue
        near, near_t = future_best_at(turns, turn, p, horizon=SHORT_HORIZON)
        short_rate = ((near - b) / (near_t - turn + 1)) if near is not None else 0.0
        long_rate = (far - b) / (far_t - turn + 1)
        out[p] = SHORT_WEIGHT * short_rate + (1.0 - SHORT_WEIGHT) * long_rate
    return out


def _encode(rows, num, cat, cat_maps=None):
    """Numeric matrix for IsolationForest; categoricals ordinal-encoded, unseen -> a fresh index."""
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
