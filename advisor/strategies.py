from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import arms

NAMES = ("random", "greedy_catboost", "ruleset", "marwil_gnn")


class Random:

    def __init__(self, rng):
        self.rng = rng
        self.ready = True

    def pick(self, elig, record):
        pools = {}
        for r in elig:
            pools.setdefault(r["action_type"], []).append(r)
        return self.rng.choice(pools[self.rng.choice(sorted(pools))])


class GreedyCatboost:

    def __init__(self, ranker):
        self.ranker = ranker

    @property
    def ready(self):
        return bool(self.ranker.ready)

    def pick(self, elig, record):
        return max(elig, key=lambda r: r.get("exploit") or 0.0)


class Ruleset:

    def __init__(self, ruleset):
        self.ruleset = ruleset
        self.last_rule = None

    @property
    def ready(self):
        return self.ruleset is not None

    def pick(self, elig, record):
        self.last_rule = None
        hit = self.ruleset.match(elig, record)
        if hit is None:
            return None
        row, rule_name = hit
        self.last_rule = rule_name
        return row


def offer_key(r):
    return (r.get("context_kind"), str(r.get("context_id")),
            r.get("action_type"), str(r.get("key")))


class MarwilGnn:

    def __init__(self, gnn):
        self.gnn = gnn
        self.errors = 0
        self.last_scores = {}
        self.scored = None

    @property
    def ready(self):
        return bool(self.gnn is not None and self.gnn.ready)

    def pick(self, elig, record):
        scored, self.scored = self.scored, None
        if scored:
            self.last_scores = scored
            best, best_v = None, None
            for r in elig:
                v = scored.get(offer_key(r))
                if v is not None and (best_v is None or v > best_v):
                    best, best_v = r, v
            if best is not None:
                return best
        self.last_scores = {}
        try:
            best = self.gnn.pick(elig, record)
        except Exception as e:
            self.errors += 1
            sys.stderr.write("strategies: gnn pick failed (%d so far) -> %s\n"
                             % (self.errors, repr(e)[:140]))
            return None
        impact = getattr(self.gnn, "last_impact", None) or []
        self.last_scores = {offer_key(r): float(v) for r, v in zip(elig, impact)}
        return best


def build(name, rng=None, ranker=None, ruleset=None, gnn=None):
    name = arms.canonical(name)
    if name == "random":
        return Random(rng)
    if name == "greedy_catboost":
        return GreedyCatboost(ranker)
    if name == "ruleset":
        return Ruleset(ruleset)
    if name == "marwil_gnn":
        return MarwilGnn(gnn)
    raise ValueError("unknown strategy %r -- known: %s" % (name, ", ".join(NAMES)))
