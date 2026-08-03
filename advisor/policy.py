from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import model as M                                          # noqa: E402

FORBIDDEN_KEYS = frozenset({"button_attack", "button_spectate"})

MAX_ACTIONS_PER_TURN = 10
MAX_ACTIONS_PER_ENTITY = 6

EPSILON = 0.25                     # P(uniform random pick)          (operator, 2026-08-02)
BETA = 0.25                        # P(argmax novelty); the remainder is argmax exploit

SUBSAMPLE_CAPS = {"diplomacy": 12}
# per (context_kind, context_id, action_type), per turn
# counted once across entities rather than per entity. The exact scope of the game's character
# capacity is UNCONFIRMED (province-wide is at least as likely as faction-wide); this is the
# conservative choice, and multi-province campaigns are rare enough that it rarely binds.
FACTION_WIDE_CAPS = frozenset(("recruit_lord", "research", "rites"))
PER_TURN_CAPS = {"recruit_lord": 1, "recruit_unit": 4, "edict": 1, "research": 1, "rites": 1,
                 "diplomacy": 1,
                 "stance": 1}
COLD_NOOP_WEIGHT = 0.10


def _cap_key(context_kind, context_id, action_type):
    """Cap counter key: faction-wide types share one counter across every entity."""
    if action_type in FACTION_WIDE_CAPS:
        return ("faction", "*", action_type)
    return (context_kind, str(context_id), action_type)


class Policy:
    def __init__(self, ranker=None, seed=None, max_actions_per_turn=MAX_ACTIONS_PER_TURN,
                 max_actions_per_entity=MAX_ACTIONS_PER_ENTITY):
        self.ranker = ranker if ranker is not None else M.Ranker()
        self.rng = random.Random(seed)
        self.max_actions_per_turn = max_actions_per_turn
        self.max_actions_per_entity = max_actions_per_entity
        self.retired = set()           # (context_kind, context_id) done for this turn
        self.blacklist = set()         # (ck, cid, action_type, key) that failed confirmation
        self.failed_types = set()
        self.entity_actions = {}       # (ck, cid) -> confirmed actions this turn
        self.type_actions = {}         # (ck, cid, action_type) -> confirmed of THAT type this turn

    def new_turn(self):
        self.retired.clear()
        self.blacklist.clear()
        self.failed_types.clear()
        self.entity_actions.clear()
        self.type_actions.clear()

    def retire(self, context_kind, context_id):
        self.retired.add((context_kind, str(context_id)))

    def note_result(self, pick, counted):
        """A confirmed action advances that entity's counter; an unconfirmed one is blacklisted."""
        k = (pick["context_kind"], str(pick["context_id"]))
        # counts attempts, not confirmations -- must stay above the `if counted` branch
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
        """Available, not battle-UI, not blacklisted, not belonging to a retired entity.
        end_turn is not OFFERED before the 6th decision of a turn (operator, 2026-08-02);
        a turn with nothing else eligible still ends via the loop's forced end-turn path."""
        out = []
        for r in ranked:
            if not r.get("available"):
                continue
            if r["action_type"] == "end_turn" and actions_taken < 5:
                continue
            if str(r.get("key")) in FORBIDDEN_KEYS:
                continue
            k = (r["context_kind"], str(r["context_id"]))
            if k in self.retired and r["action_type"] != "end_turn":
                continue
            if r["action_type"] in self.failed_types:
                continue
            if (k[0], k[1], r["action_type"], str(r["key"])) in self.blacklist:
                continue
            cap = PER_TURN_CAPS.get(r["action_type"])
            if cap is not None and self.type_actions.get(
                    _cap_key(k[0], k[1], r["action_type"]), 0) >= cap:
                continue
            out.append(r)
        return self._subsample(out)

    def _subsample(self, rows):
        """Thin the action types listed in SUBSAMPLE_CAPS down to their cap, uniformly."""
        keep, pools = [], {}
        for r in rows:
            cap = SUBSAMPLE_CAPS.get(r["action_type"])
            (pools.setdefault(r["action_type"], []) if cap else keep).append(r)
        for atype, pool in pools.items():
            cap = SUBSAMPLE_CAPS[atype]
            keep.extend(pool if len(pool) <= cap else self.rng.sample(pool, cap))
        return keep

    def choose(self, record, actions_taken=0):
        """(pick, ranked) -- the action to execute next, and the full scored table. pick is None
        when nothing is eligible."""
        ranked = self.ranker.score(record)
        hot = self.ranker.ready
        if not hot:
            self._cold_scores(ranked, actions_taken)
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
        elig = self.eligible(ranked, actions_taken=actions_taken)
        if not elig:
            return None, ranked
        roll = self.rng.random() if hot else 1.0
        if hot and roll < EPSILON:
            mode, best = "epsilon_random", self.rng.choice(elig)
        elif hot and roll < EPSILON + BETA:
            mode = "explore"
            best = max(elig, key=lambda r: r.get("explore") or 0.0)
        elif hot:
            mode = "exploit"
            best = max(elig, key=lambda r: r.get("exploit") or 0.0)
        else:
            mode = "cold_random"
            best = max(elig, key=lambda r: r["score"] if r["score"] is not None else 0.0)
        pick = {"context_kind": best["context_kind"], "context_id": best["context_id"],
                "action_type": best["action_type"], "key": best["key"],
                "params": best.get("params") or {},
                "policy": mode,
                "score": best.get("score"), "rank": best.get("rank")}
        return pick, ranked

    def _cold_scores(self, ranked, actions_taken):
        """Uniform random scores, with end_turn weighted up by actions_taken and noop weighted down."""
        end_w = min(0.9, 0.05 + 0.9 * (actions_taken / float(max(self.max_actions_per_turn, 1))))
        for r in ranked:
            base = self.rng.random()
            if r["action_type"] == "end_turn":
                r["score"] = base * end_w + (1.0 - end_w) * 0.0 + end_w
            elif r["action_type"] == "noop":
                r["score"] = base * COLD_NOOP_WEIGHT
            else:
                r["score"] = base
            r.setdefault("exploit", None)
            r.setdefault("explore", None)


def scores_for_store(ranked, limit=None):
    """The ranking rows the recorder persists next to the offers."""
    rows = ranked if limit is None else ranked[:limit]
    return [{"context_kind": r["context_kind"], "context_id": r["context_id"],
             "action_type": r["action_type"], "key": r["key"], "score": r.get("score"),
             "exploit": r.get("exploit"), "explore": r.get("explore"), "rank": r.get("rank"),
             "pct_global": r.get("pct_global"), "pct_local": r.get("pct_local")}
            for r in rows]
