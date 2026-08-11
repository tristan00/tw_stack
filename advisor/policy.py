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

FORBIDDEN_KEYS = frozenset({"button_attack", "button_spectate"})

MAX_ACTIONS_PER_TURN = 16
MAX_ACTIONS_PER_ENTITY = 6

EPSILON = 0.10
BETA = 0.10

DEFAULT_STRATEGIES = {"exploit_tree": 0.8, "random": 0.2}


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


FACTION_WIDE_CAPS = frozenset(("recruit_lord", "recruit_hero", "research", "rites",
                               "building_dismantle"))
PER_TURN_CAPS = {"recruit_lord": 1, "recruit_hero": 1, "recruit_unit": 4, "edict": 1,
                 "research": 1, "rites": 1,
                 "diplomacy": 3, "noop": 0,
                 "stance": 1, "hero_action": 3, "building_dismantle": 0,
                 "raise_dead": 4, "recruit_ror": 1,
                 "recruit_blessed": 4, "recruit_imperial": 1}

ATTACK_PAIR_TYPES = frozenset(("attack_army", "attack_settlement"))
ATTACK_PAIR_CAP = 1
DIPLO_TARGET_CAP = 1
DIPLO_GIFT_CAP = 0


def _is_gift(key):
    return str(key).split(":", 1)[-1].startswith("gift_")


def _pair_key(context_kind, context_id, action_type, key):
    if action_type == "diplomacy":
        return ("diplomacy", str(key).split(":", 1)[0])
    return (context_kind, str(context_id), action_type, str(key))


def _tally(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _cap_key(context_kind, context_id, action_type):
    if action_type in FACTION_WIDE_CAPS:
        return ("faction", "*", action_type)
    return (context_kind, str(context_id), action_type)


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
        if "gnn" in self.strategies:
            # Resolve mapgraph against the checkout this file lives in -- common.ROOT
            # is derived from common.py's own location. Hardcoding D:\tw_stack here meant
            # a worktree loaded the MAIN checkout's mapgraph while training wrote a model
            # from its own -- which surfaces as a state_dict key mismatch and silently
            # drops the gnn arm to random.
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
        # ONE gate, and it is options.Gate. Policy used to carry a second, private
        # copy of the same idea in `eligible()`, working on rows the collector had
        # already marked available -- two components deciding the same question.
        self.gate = O.Gate(max_actions_per_entity=max_actions_per_entity)
        self.last_drops = []
        self.last_choice = {}
        self.gnn_score_errors = 0

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
        # Both models score the same offers, on every decision, before anything is drawn
        # and before eligibility is even considered. Neither is the pipeline and neither is
        # a plugin: they are two estimates of the same quantity (an action's advantage over
        # doing nothing), and a strategy that wants one reads a number that already exists.
        # catboost is listed first only because its ordering is what defines `rank`.
        ranked = self.ranker.score(record)
        hot = self.ranker.ready
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
        gnn_scores = self._score_with_gnn(ranked, record)
        if "gnn" in self.members:
            self.members["gnn"].scored = gnn_scores
        # Everything on the record is already a survivor -- the loop generated and
        # gated before the recorder stored it -- so there is nothing left to filter.
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
        best = member.pick(elig, record) if member.ready else None
        if best is None:
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
        """The gnn's half of what `self.ranker.score()` does for catboost.

        Scores the same set catboost scores -- every offer, every decision, before
        eligibility or the draw -- so `gnn_rank` and `rank` are 1..N over the same N and
        can be compared directly. Either model can then be asked what it made of an action
        the other took, which is the only way to judge a model on decisions it did not
        control.

        Returns the scores so the gnn strategy picks straight from them: one forward pass
        per decision serves both the record and the choice, rather than the choice
        computing numbers the record scavenges afterwards.

        Consumes no rng, so the draw sequence is unchanged, and swallows its own failures
        -- an unscored decision costs a comparison; a raised one costs the run.
        """
        if self.gnn is None or not getattr(self.gnn, "ready", False) or not ranked:
            return None
        try:
            impact = self.gnn.score_elig(ranked, record)
        except Exception as e:
            self.gnn_score_errors += 1
            sys.stderr.write("policy: gnn scoring failed (%d so far) -> %s\n"
                             % (self.gnn_score_errors, repr(e)[:140]))
            return None
        scored = {S.offer_key(r): float(v) for r, v in zip(ranked, impact)}
        if not scored:
            return None
        rank_of = {}
        for i, v in enumerate(sorted(scored.values(), reverse=True)):
            rank_of.setdefault(v, i + 1)     # ties share the better rank
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
