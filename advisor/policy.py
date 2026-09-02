from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

import arms
import model as M
import options as O
import strategies as S


MAX_ACTIONS_PER_ENTITY = 6

EPSILON = 0.10
BETA = 0.10

DEFAULT_STRATEGIES = {"greedy_catboost": 0.8, "random": 0.2}

ModelUnavailable = S.ModelUnavailable


def normalize_strategies(strategies, allowed=None):
    names = tuple(allowed) if allowed is not None else S.NAMES
    mix = dict(strategies if strategies is not None else DEFAULT_STRATEGIES)
    if not mix:
        raise ValueError("empty strategy mix -- known strategies: %s" % ", ".join(names))
    unknown = sorted(k for k in mix if k not in names)
    if unknown:
        raise ValueError("unknown strategy name(s) %s -- known: %s"
                         % (unknown, ", ".join(names)))
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
    if abs(total - 1.0) > 1e-6:
        raise ValueError(
            "strategy mix %r sums to %.4f, not 1 -- the weights are the shares the run will "
            "play, so they must add up; nothing is renormalised for you" % (strategies, total))
    return {k: w / total for k, w in mix.items()}


def normalize_interrupt_strategies(strategies):
    mix = dict(strategies if strategies is not None else DEFAULT_STRATEGIES)
    modelless = sorted(k for k in mix if k in S.NAMES and k not in arms.INTERRUPT_NAMES)
    if modelless:
        raise ValueError(
            "interrupt mix names %s, which have no interrupt model -- blocking screens are "
            "answered by %s only" % (modelless, ", ".join(arms.INTERRUPT_NAMES)))
    return normalize_strategies(mix, allowed=arms.INTERRUPT_NAMES)


def interrupt_strategies_for(action_mix, interrupt_strategies):
    if interrupt_strategies is not None:
        return normalize_interrupt_strategies(interrupt_strategies)
    spill = sorted(k for k in (action_mix or {}) if k not in arms.INTERRUPT_NAMES)
    if spill:
        raise ValueError(
            "the action mix plays %s, which have no interrupt model, so blocking screens "
            "need their own mix -- give --interrupt-strategies over %s"
            % (spill, ", ".join(arms.INTERRUPT_NAMES)))
    return normalize_strategies(action_mix, allowed=arms.INTERRUPT_NAMES)


def _tally(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


class Policy:
    def __init__(self, ranker=None, seed=None,
                 max_actions_per_entity=MAX_ACTIONS_PER_ENTITY, strategies=None):
        self.ranker = ranker if ranker is not None else M.Ranker()
        self.rng = random.Random(seed)
        self.strategies = normalize_strategies(strategies)
        self.ggnn = None
        if "greedy_gnn" in self.strategies:
            if common.ROOT not in sys.path:
                sys.path.insert(0, common.ROOT)
            from advisor.mapgraph import greedy_rank as GGNN
            self.ggnn = GGNN.Ranker()
        self.members = {name: S.build(name, rng=self.rng, ranker=self.ranker,
                                      ggnn=self.ggnn)
                        for name in self.strategies}
        self.fallback = self.members.get("random") or S.build("random", rng=self.rng)
        self.max_actions_per_entity = max_actions_per_entity
        self.gate = O.Gate(max_actions_per_entity=max_actions_per_entity)
        self.last_drops = []
        self.last_choice = {}

    def new_turn(self):
        self.gate.new_turn()

    def new_campaign(self):
        self.gate.new_campaign()

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
        if hot:
            for i, r in enumerate(ranked):
                r["rank"] = i + 1
        graph = (self._graph(record)
                 if (ranked and self.ggnn is not None) else None)
        ggnn_scores = self._score_with_greedy(ranked, record, graph)
        if "greedy_gnn" in self.members:
            self.members["greedy_gnn"].scored = ggnn_scores
            self.members["greedy_gnn"].graph = graph
        elig = ranked
        self.last_drops = list(self.gate.last_drops)
        self.last_choice = {"hot": bool(hot), "n_ranked": len(ranked), "n_eligible": len(elig),
                            "n_dropped": len(self.last_drops), "actions_taken": actions_taken,
                            "drop_reasons": _tally(d["reason"] for d in self.last_drops),
                            "eligible_types": _tally(r["action_type"] for r in elig),
                            "ggnn_scored": len(ggnn_scores or {}),
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
        else:
            mode = drawn
        if drawn == "greedy_gnn" and member.last_reward is not None:
            self.last_choice["greedy_gnn_reward"] = round(float(member.last_reward), 4)
        pick = {"context_kind": best["context_kind"], "context_id": best["context_id"],
                "action_type": best["action_type"], "key": best["key"],
                "params": best.get("params") or {},
                "policy": mode,
                "score": best.get("score"), "rank": best.get("rank")}
        self.last_choice["mode"] = mode
        self.last_choice["roll"] = round(roll, 4)
        return pick, ranked

    def _graph(self, record):
        from advisor.mapgraph import build as B
        return B.build_graph(record)

    def _score_with_greedy(self, ranked, record, graph=None):
        if self.ggnn is None or not ranked:
            return None
        reward = self.ggnn.score_elig(ranked, record, graph=graph)
        return self._stamp(ranked, reward, "ggnn_score", "ggnn_rank")

    @staticmethod
    def _stamp(ranked, values, score_field, rank_field):
        scored = {S.offer_key(r): float(v) for r, v in zip(ranked, values)}
        if not scored:
            return None
        rank_of = {}
        for i, v in enumerate(sorted(scored.values(), reverse=True)):
            rank_of.setdefault(v, i + 1)
        for r in ranked:
            v = scored.get(S.offer_key(r))
            if v is not None:
                r[score_field] = round(v, 5)
                r[rank_field] = rank_of[v]
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
    out = []
    for r in rows:
        vals = {f: r.get(f) for f in ("score", "exploit", "rank", "pct_global",
                                      "gnn_impact", "gnn_rank")}
        models = {}
        if r.get("ggnn_score") is not None or r.get("ggnn_rank") is not None:
            models["greedy_gnn"] = {"score": r.get("ggnn_score"), "rank": r.get("ggnn_rank")}
        if all(v is None for v in vals.values()) and not models:
            continue
        out.append(dict(vals, models=models, context_kind=r["context_kind"],
                        context_id=r["context_id"],
                        action_type=r["action_type"], key=r["key"]))
    return out
