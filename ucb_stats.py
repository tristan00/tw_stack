from __future__ import annotations

import json
import math
import os

import common

WINDOW = common.LOOKBACK_CAMPAIGNS
MIN_PLAYS = 2
KEYS = ("mean", "entropy", "std")
EMPTY = {"n": 0, "mean": 0.0, "entropy": 0.0, "std": 0.0}
ADJUST_FILE = "ucb_adjust.json"


def adjust_path():
    for d in (common.RULES_DIR, common.RULES_DIR_REPO):
        p = os.path.join(d, ADJUST_FILE)
        if os.path.isfile(p):
            return p
    return None


def load_adjustments():
    p = adjust_path()
    if not p:
        return {}, None
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    adj = {str(k): float(v) for k, v in (data.get("adjustments") or {}).items()}
    return {k: v for k, v in adj.items() if v}, p


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
            "    FROM campaign_gains ORDER BY first_ts DESC LIMIT %s) w", (int(window),)):
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


def window_blend(rewards):
    gains = [g for vals in rewards.values() for g in vals]
    return blend(describe(gains)) if gains else 0.0


def explore_term(c, total, n):
    return c * math.sqrt(math.log(max(1, total)) / n)


def score(d, c, total, adjust=0.0):
    if d["n"] < MIN_PLAYS:
        return 0.0, float("inf"), float("inf")
    b = blend(d)
    e = explore_term(c, total, d["n"])
    return b, e, b + e + adjust


def gini(values):
    xs = sorted(float(v) for v in values)
    n = len(xs)
    s = sum(xs)
    if n < 2 or s <= 0:
        return 0.0
    return sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(xs)) / (n * s)
