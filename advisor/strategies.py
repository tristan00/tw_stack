from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import arms

NAMES = arms.NAMES
TRAINABLE = arms.TRAINABLE
ModelUnavailable = arms.ModelUnavailable


TYPE_WEIGHTS = {"building_dismantle": 0.2, "item_unequip": 0.2, "end_turn": 1.0, "move": 2.0,
                "diplomacy:declare_war": 1.2, "recruit_unit": 2.0, "recruit_hero": 0.2}


class Random:

    def __init__(self, rng):
        self.rng = rng
        self.ready = True

    def pick(self, elig, record):
        pools = {}
        for r in elig:
            t = r["action_type"]
            if t == "diplomacy" and str(r.get("key", "")).endswith(":declare_war"):
                t = "diplomacy:declare_war"
            pools.setdefault(t, []).append(r)
        types = sorted(pools)
        weights = [TYPE_WEIGHTS.get(t, 1.0) for t in types]
        return self.rng.choice(pools[self.rng.choices(types, weights=weights)[0]])


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
        best = self.gnn.pick(elig, record)
        impact = getattr(self.gnn, "last_impact", None) or []
        self.last_scores = {offer_key(r): float(v) for r, v in zip(elig, impact)}
        return best


class GreedyGnn:

    def __init__(self, ggnn):
        self.ggnn = ggnn
        self.graph = None
        self.scored = None
        self.last_reward = None

    @property
    def ready(self):
        return bool(self.ggnn is not None and self.ggnn.ready)

    def pick(self, elig, record):
        graph, self.graph = self.graph, None
        scored, self.scored = self.scored, None
        self.last_reward = None
        if scored:
            best, best_v = None, None
            for r in elig:
                v = scored.get(offer_key(r))
                if v is not None and (best_v is None or v > best_v):
                    best, best_v = r, v
            if best is not None:
                self.last_reward = best_v
                return best
        best = self.ggnn.pick(elig, record, graph=graph)
        self.last_reward = self.ggnn.last_reward
        return best


def build(name, rng=None, ranker=None, ruleset=None, gnn=None, ggnn=None):
    name = arms.canonical(name)
    if name == "random":
        return Random(rng)
    if name == "greedy_catboost":
        return GreedyCatboost(ranker)
    if name == "ruleset":
        return Ruleset(ruleset)
    if name == "marwil_gnn":
        return MarwilGnn(gnn)
    if name == "greedy_gnn":
        return GreedyGnn(ggnn)
    raise ValueError("unknown strategy %r -- known: %s" % (name, ", ".join(NAMES)))
