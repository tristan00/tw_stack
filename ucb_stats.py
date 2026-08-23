from __future__ import annotations

import math

WINDOW = 1000
MIN_PLAYS = 2
KEYS = ("mean", "entropy", "std")
EMPTY = {"n": 0, "mean": 0.0, "entropy": 0.0, "std": 0.0}


def entropy_bits(rewards):
    n = len(rewards)
    counts = {}
    for r in rewards:
        k = int(round(r))
        counts[k] = counts.get(k, 0) + 1
    h = -sum((k / n) * math.log2(k / n) for k in counts.values())
    return h + (len(counts) - 1) / (2.0 * n)


def window_rewards(con, window=WINDOW):
    out = {}
    for m, f, r in con.execute(
            "SELECT campaign_map, faction, settlements_gained + levels_gained FROM"
            " (SELECT campaign_map, faction, settlements_gained, levels_gained"
            "    FROM campaign_gains ORDER BY first_ts DESC LIMIT ?)", (int(window),)):
        out.setdefault((m, f), []).append(float(r or 0.0))
    return out


def describe(vals):
    n = len(vals)
    mean = sum(vals) / n
    std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5 if n > 1 else 0.0
    return {"n": n, "mean": mean, "entropy": entropy_bits(vals), "std": std}


def start_stats(rewards):
    return {key: describe(vals) for key, vals in rewards.items()}


def blend(d):
    return sum(d[key] for key in KEYS) / len(KEYS)


def explore_term(c, total, n):
    return c * math.sqrt(math.log(max(1, total)) / n)


def score(d, c, total):
    if d["n"] < MIN_PLAYS:
        return 0.0, float("inf"), float("inf")
    b = blend(d)
    e = explore_term(c, total, d["n"])
    return b, e, b + e


def gini(values):
    xs = sorted(float(v) for v in values)
    n = len(xs)
    s = sum(xs)
    if n < 2 or s <= 0:
        return 0.0
    return sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(xs)) / (n * s)
