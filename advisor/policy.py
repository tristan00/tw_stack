from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import model as M
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
                 "stance": 1, "hero_action": 3, "building_dismantle": 1,
                 "raise_dead": 4, "recruit_ror": 1,
                 "recruit_blessed": 4, "recruit_imperial": 1}


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
        self.members = {name: S.build(name, rng=self.rng, ranker=self.ranker,
                                      ruleset=self.ruleset)
                        for name in self.strategies}
        self.fallback = self.members.get("random") or S.build("random", rng=self.rng)
        self.max_actions_per_turn = max_actions_per_turn
        self.max_actions_per_entity = max_actions_per_entity
        self.retired = set()
        self.blacklist = set()
        self.failed_types = set()
        self.entity_actions = {}
        self.type_actions = {}
        self.last_drops = []
        self.last_choice = {}

    def new_turn(self):
        self.retired.clear()
        self.blacklist.clear()
        self.failed_types.clear()
        self.entity_actions.clear()
        self.type_actions.clear()

    def retire(self, context_kind, context_id):
        self.retired.add((context_kind, str(context_id)))

    def note_result(self, pick, counted):
        k = (pick["context_kind"], str(pick["context_id"]))
        tk = _cap_key(k[0], k[1], pick["action_type"])
        self.type_actions[tk] = self.type_actions.get(tk, 0) + 1
        if counted:
            n = self.entity_actions.get(k, 0) + 1
            self.entity_actions[k] = n
            if n >= self.max_actions_per_entity:
                self.retired.add(k)
        elif pick["action_type"] != "end_turn":
            self.blacklist.add((k[0], k[1], pick["action_type"], str(pick["key"])))
            self.failed_types.add(pick["action_type"])

    def eligible(self, ranked, actions_taken=0):
        out = []
        self.last_drops = []
        for r in ranked:
            k = (r["context_kind"], str(r["context_id"]))
            cap = PER_TURN_CAPS.get(r["action_type"])
            reason = None
            if not r.get("available"):
                reason = "unavailable:%s" % (r.get("gate") or "no_gate_recorded")
            elif r["action_type"] == "end_turn" and actions_taken < 5:
                reason = "end_turn_before_6th_decision"
            elif str(r.get("key")) in FORBIDDEN_KEYS:
                reason = "forbidden_key"
            elif k in self.retired and r["action_type"] != "end_turn":
                reason = "entity_retired"
            elif r["action_type"] in self.failed_types:
                reason = "type_failed_this_turn"
            elif (k[0], k[1], r["action_type"], str(r["key"])) in self.blacklist:
                reason = "blacklisted_this_turn"
            elif cap is not None and self.type_actions.get(
                    _cap_key(k[0], k[1], r["action_type"]), 0) >= cap:
                reason = "per_turn_cap:%d" % cap
            if reason is None:
                out.append(r)
            else:
                self.last_drops.append(
                    {"context_kind": k[0], "context_id": k[1], "action_type": r["action_type"],
                     "key": r.get("key"), "rank": r.get("rank"), "reason": reason})
        return out

    def choose(self, record, actions_taken=0):
        ranked = self.ranker.score(record)
        hot = self.ranker.ready
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
        elig = self.eligible(ranked, actions_taken=actions_taken)
        self.last_choice = {"hot": bool(hot), "n_ranked": len(ranked), "n_eligible": len(elig),
                            "n_dropped": len(self.last_drops), "actions_taken": actions_taken,
                            "drop_reasons": _tally(d["reason"] for d in self.last_drops),
                            "eligible_types": _tally(r["action_type"] for r in elig),
                            "mix": dict(self.strategies), "mode": None, "roll": None}
        if not elig:
            return None, ranked
        roll = self.rng.random()
        drawn = self._draw(roll)
        member = self.members[drawn]
        best = member.pick(elig, record) if member.ready else None
        if best is None:
            mode = "%s->random" % drawn
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
             "exploit": r.get("exploit"), "explore": r.get("explore"), "rank": r.get("rank"),
             "pct_global": r.get("pct_global"), "pct_local": r.get("pct_local")}
            for r in rows]
