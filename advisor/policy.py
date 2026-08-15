from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

import model as M
import options as O
import ruleset as R
import strategies as S


MAX_ACTIONS_PER_TURN = 6
MAX_ACTIONS_PER_ENTITY = 6

EPSILON = 0.10
BETA = 0.10

DEFAULT_STRATEGIES = {"greedy_catboost": 0.8, "random": 0.2}

ModelUnavailable = S.ModelUnavailable


def normalize_strategies(strategies):
    mix = dict(strategies if strategies is not None else DEFAULT_STRATEGIES)
    if not mix:
        raise ValueError("empty strategy mix -- known strategies: %s" % ", ".join(S.NAMES))
    unknown = sorted(k for k in mix if k not in S.NAMES)
    if unknown:
        raise ValueError("unknown strategy name(s) %s -- known: %s"
                         % (unknown, ", ".join(S.NAMES)))
    total = 0.0
    for k, v in mix.items():
        try:
            w = float(v)
        except (TypeError, ValueError):
            raise ValueError("strategy %r has a non-numeric weight %r" % (k, v))
        if w < 0.0:
            raise ValueError("strategy %r has a negative weight %r" % (k, v))
        mix[k] = w
        total += w
    if total <= 0.0:
        raise ValueError("strategy mix %r sums to zero" % (strategies,))
    return {k: w / total for k, w in mix.items()}


def _tally(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


class Policy:
    def __init__(self, ranker=None, seed=None, max_actions_per_turn=MAX_ACTIONS_PER_TURN,
                 max_actions_per_entity=MAX_ACTIONS_PER_ENTITY, strategies=None, ruleset=None):
        self.ranker = ranker if ranker is not None else M.Ranker()
        self.rng = random.Random(seed)
        self.strategies = normalize_strategies(strategies)
        self.ruleset = R.RuleSet.load(ruleset) if ruleset else None
        if "ruleset" in self.strategies and self.ruleset is None:
            raise ValueError("strategy mix includes 'ruleset' but no ruleset name was given")
        self.gnn = None
        if "marwil_gnn" in self.strategies:
            if common.ROOT not in sys.path:
                sys.path.insert(0, common.ROOT)
            from advisor.mapgraph import rank as GNN
            self.gnn = GNN.Ranker()
        self.members = {name: S.build(name, rng=self.rng, ranker=self.ranker,
                                      ruleset=self.ruleset, gnn=self.gnn)
                        for name in self.strategies}
        self.fallback = self.members.get("random") or S.build("random", rng=self.rng)
        self.max_actions_per_turn = max_actions_per_turn
        self.max_actions_per_entity = max_actions_per_entity
        self.gate = O.Gate(max_actions_per_entity=max_actions_per_entity)
        self.last_drops = []
        self.last_choice = {}

    def new_turn(self):
        self.gate.new_turn()

    @property
    def retired(self):
        return self.gate.retired

    def retire(self, context_kind, context_id):
        self.gate.retire(context_kind, context_id)

    def note_result(self, pick, counted):
        self.gate.note_result(pick, counted)

    def choose(self, record, actions_taken=0):
        ranked = self.ranker.score(record)
        hot = self.ranker.ready
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
        gnn_scores = self._score_with_gnn(ranked, record)
        if "marwil_gnn" in self.members:
            self.members["marwil_gnn"].scored = gnn_scores
        elig = ranked
        self.last_drops = list(self.gate.last_drops)
        self.last_choice = {"hot": bool(hot), "n_ranked": len(ranked), "n_eligible": len(elig),
                            "n_dropped": len(self.last_drops), "actions_taken": actions_taken,
                            "drop_reasons": _tally(d["reason"] for d in self.last_drops),
                            "eligible_types": _tally(r["action_type"] for r in elig),
                            "gnn_scored": len(gnn_scores or {}),
                            "mix": dict(self.strategies), "mode": None, "roll": None}
        if not elig:
            return None, ranked
        roll = self.rng.random()
        drawn = self._draw(roll)
        member = self.members[drawn]
        if drawn in S.TRAINABLE and not member.ready:
            raise S.ModelUnavailable(
                "policy: trainable arm %r drawn but its model is not ready -- a run that "
                "needs a model must not silently play random" % drawn)
        best = member.pick(elig, record) if member.ready else None
        if best is None:
            if drawn in S.TRAINABLE:
                raise S.ModelUnavailable(
                    "policy: trainable arm %r returned no pick from %d eligible offers"
                    % (drawn, len(elig)))
            mode = "%s_random_fallback" % drawn
            best = self.fallback.pick(elig, record)
        elif drawn == "ruleset":
            mode = "ruleset(%s)" % member.last_rule
        else:
            mode = drawn
        pick = {"context_kind": best["context_kind"], "context_id": best["context_id"],
                "action_type": best["action_type"], "key": best["key"],
                "params": best.get("params") or {},
                "policy": mode,
                "score": best.get("score"), "rank": best.get("rank")}
        self.last_choice["mode"] = mode
        self.last_choice["roll"] = round(roll, 4)
        return pick, ranked

    def _score_with_gnn(self, ranked, record):
        if self.gnn is None or not ranked:
            return None
        impact = self.gnn.score_elig(ranked, record)
        scored = {S.offer_key(r): float(v) for r, v in zip(ranked, impact)}
        if not scored:
            return None
        rank_of = {}
        for i, v in enumerate(sorted(scored.values(), reverse=True)):
            rank_of.setdefault(v, i + 1)
        for r in ranked:
            v = scored.get(S.offer_key(r))
            if v is not None:
                r["gnn_impact"] = round(v, 5)
                r["gnn_rank"] = rank_of[v]
        return scored

    def _draw(self, roll):
        names = list(self.strategies)
        acc = 0.0
        for name in names:
            acc += self.strategies[name]
            if roll < acc:
                return name
        return names[-1]


def scores_for_store(ranked, limit=None):
    rows = ranked if limit is None else ranked[:limit]
    return [{"context_kind": r["context_kind"], "context_id": r["context_id"],
             "action_type": r["action_type"], "key": r["key"], "score": r.get("score"),
             "exploit": r.get("exploit"), "rank": r.get("rank"),
             "pct_global": r.get("pct_global"), "pct_local": r.get("pct_local"),
             "gnn_impact": r.get("gnn_impact"), "gnn_rank": r.get("gnn_rank")}
            for r in rows]
