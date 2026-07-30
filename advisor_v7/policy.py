r"""policy.py -- turns a ranking into ONE choice.

The advisor scores every action the whole faction could take, then this picks one. It is the only
place allowed to decide "do nothing here" or "end the turn", and it is the second place (after the
launcher's executor) where the battle-UI keys are hard-blocked.

    HOT   a trained Ranker exists -> take the top-scoring available offer, with an epsilon-greedy
          slice of random picks that shrinks as the training set grows.
    COLD  nothing trained yet -> uniform random over the available offers, with two priors that
          keep a cold run from stalling: end_turn's weight RISES with the number of actions already
          taken this turn, and noop keeps a small constant weight so entities retire.

Both paths go through the same argmax over a `score` column, so a cold run and a hot run produce
identically shaped decision records -- which is what makes cold-start data trainable later.
"""
from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import model as M                                          # noqa: E402

# battle-UI entries are NEVER selectable -- hardcoded here AND in the launcher's executor, because
# the one thing a testing agent must never do is wander into a manual battle.
FORBIDDEN_KEYS = frozenset({"button_attack", "button_spectate"})

MAX_ACTIONS_PER_TURN = 40          # hard ceiling; the loop force-ends the turn at this
MAX_ACTIONS_PER_ENTITY = 6         # an entity retires after this many confirmed actions in a turn
EPS_FLOOR = 0.05                   # never stop exploring entirely
COLD_NOOP_WEIGHT = 0.10            # cold-start prior mass on "this entity is done"


class Policy:
    def __init__(self, ranker=None, seed=None, max_actions_per_turn=MAX_ACTIONS_PER_TURN,
                 max_actions_per_entity=MAX_ACTIONS_PER_ENTITY):
        self.ranker = ranker if ranker is not None else M.Ranker()
        self.rng = random.Random(seed)
        self.max_actions_per_turn = max_actions_per_turn
        self.max_actions_per_entity = max_actions_per_entity
        self.retired = set()           # (context_kind, context_id) done for this turn
        self.blacklist = set()         # (ck, cid, action_type, key) that failed confirmation
        self.entity_actions = {}       # (ck, cid) -> confirmed actions this turn

    # ------------------------------------------------------------------ turn bookkeeping
    def new_turn(self):
        self.retired.clear()
        self.blacklist.clear()
        self.entity_actions.clear()

    def retire(self, context_kind, context_id):
        self.retired.add((context_kind, str(context_id)))

    def note_result(self, pick, counted):
        """A confirmed action advances that entity's counter; an unconfirmed one is blacklisted so
        the same dead action is never retried a second time in the same turn."""
        k = (pick["context_kind"], str(pick["context_id"]))
        if counted:
            n = self.entity_actions.get(k, 0) + 1
            self.entity_actions[k] = n
            if n >= self.max_actions_per_entity:
                self.retired.add(k)
        else:
            self.blacklist.add((k[0], k[1], pick["action_type"], str(pick["key"])))

    @property
    def epsilon(self):
        if not self.ranker.ready:
            return 1.0
        rows = (self.ranker.meta or {}).get("rows") or 0
        return max(EPS_FLOOR, min(1.0, 200.0 / max(rows, 1)))

    # ------------------------------------------------------------------ the choice
    def eligible(self, ranked):
        """Available, not battle-UI, not blacklisted, not belonging to a retired entity."""
        out = []
        for r in ranked:
            if not r.get("available"):
                continue
            if str(r.get("key")) in FORBIDDEN_KEYS:
                continue
            k = (r["context_kind"], str(r["context_id"]))
            if k in self.retired and r["action_type"] != "end_turn":
                continue
            if (k[0], k[1], r["action_type"], str(r["key"])) in self.blacklist:
                continue
            out.append(r)
        return out

    def choose(self, record, actions_taken=0):
        """(pick, ranked) -- the action to execute next, and the full scored table behind it.

        `pick` is {context_kind, context_id, action_type, key, params, policy, score, rank}.
        Returns pick=None only when there is genuinely nothing eligible, which the loop treats as
        "force the turn to end" rather than as an error.
        """
        ranked = self.ranker.score(record)
        hot = self.ranker.ready
        if hot:
            eps = self.epsilon
            explore_pick = self.rng.random() < eps
        else:
            eps, explore_pick = 1.0, True
        if not hot or explore_pick:
            self._cold_scores(ranked, actions_taken)
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
        elig = self.eligible(ranked)
        if not elig:
            return None, ranked
        best = max(elig, key=lambda r: r["score"] if r["score"] is not None else 0.0)
        pick = {"context_kind": best["context_kind"], "context_id": best["context_id"],
                "action_type": best["action_type"], "key": best["key"],
                "params": best.get("params") or {},
                "policy": ("cold_random" if not hot else
                           ("epsilon_explore" if explore_pick else "greedy")),
                "score": best.get("score"), "rank": best.get("rank"),
                "epsilon": round(eps, 4)}
        return pick, ranked

    def _cold_scores(self, ranked, actions_taken):
        """Uniform random over the available offers, with the two priors that keep a run moving.

        end_turn's weight rises with how much has already been done this turn, so a cold run does a
        handful of things and then ends the turn instead of grinding to the hard cap; noop keeps a
        small constant weight so entities retire and the sweep shrinks.
        """
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
    """The ranking rows the recorder persists next to the offers (see store.attach_scores)."""
    rows = ranked if limit is None else ranked[:limit]
    return [{"context_kind": r["context_kind"], "context_id": r["context_id"],
             "action_type": r["action_type"], "key": r["key"], "score": r.get("score"),
             "exploit": r.get("exploit"), "explore": r.get("explore"), "rank": r.get("rank")}
            for r in rows]
