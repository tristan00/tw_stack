r"""record.py -- the v7 RECORDER SESSION: the one object the advisor loop talks to.

Ties the four data streams together (V7_DESIGN.md):

  STREAM 1  REWARD            end of each turn      -> target_rows
  STREAM 2  POSSIBLE ACTIONS  before every decision -> action_offers  (incl. unavailable + reason)
  STREAM 3  CHOSEN ACTION     after every decision  -> action_taken.offer_id -> the exact offer
  STREAM 4  VERIFICATION      same row              -> executed / confirmed / counted + evidence

Stream 3 is *tied to* stream 2 by construction: `decide()` records the offers, keeps their row
ids, and `commit()` links the taken action to the offer it came from. Streams 3 and 4 are one
row because "which action" and "did it really happen" are only meaningful together -- an action
we cannot verify is not training data (its whole decision point is voided by the reader).

Typical use from the advisor loop:

    rec = RecorderSession(bus, run_dir)
    ctx, offers = rec.observe("lord", cqi)        # streams 1-2 data for the model
    pick = model.rank(ctx, offers)[0]
    result = launcher.execute_confirmed(entity_ctx, pick)   # None for a noop
    rec.commit(ctx, offers, pick, result)         # streams 3-4
    ...
    rec.end_of_turn()                             # stream 1
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

import collect                                   # noqa: E402
from store import DecisionStore                  # noqa: E402


class RecorderSession:
    def __init__(self, bus, run_dir, stance_whitelist=None):
        self.bus = bus
        self.store = DecisionStore(run_dir)
        self.stance_whitelist = set(stance_whitelist or ())
        self._seq = {}          # (context_kind, context_id) -> decision index within this turn
        self._turn_done = set()  # turns whose reward row is already written

    # ------------------------------------------------------------------ streams 2 (+ features)
    def observe(self, kind, entity_id=None):
        """Fresh context features + the full offer set for one decision point.

        Called before EVERY prediction (state changes after every executed action, so this is
        never cached). Raises collect.CollectError if a chain breaks -- a broken read must never
        masquerade as "no options".
        """
        feats = collect.context_features(self.bus, kind, entity_id)
        offers = collect.available_actions(self.bus, kind, entity_id,
                                           stance_whitelist=self.stance_whitelist)
        return feats, offers

    def entities(self):
        return collect.entities(self.bus)

    # ------------------------------------------------------------------ streams 3 + 4
    def commit(self, features, offers, pick, result=None, policy=None):
        """Persist the decision point: snapshot + offers + the chosen action + its verification.

        `pick`   the action the model chose ({action_type, key, params}); a noop is a real choice.
        `result` the launcher ActionRecord (executed/confirmed/counted/refusal/confirm{}), or None
                 for a noop / observation-only decision -- in which case the noop is recorded as
                 taken and trivially confirmed, because "chose to stop" is the label we want.
        """
        key = (features.get("context_kind"), str(features.get("context_id")))
        seq = self._seq.get(key, 0)
        self._seq[key] = seq + 1

        taken = None
        if pick is not None:
            if result is not None:
                taken = dict(result)
                taken.setdefault("action_type", pick.get("action_type"))
                taken.setdefault("key", pick.get("key"))
            elif pick.get("action_type") == "noop":
                taken = {"action_type": "noop", "key": "noop", "executed": True,
                         "confirmed": True, "counted": True, "refusal": None,
                         "confirm": {"signal": "none", "before": {}, "after": {}, "latency_ms": 0},
                         "ts": time.time()}
        return self.store.write_decision(features, offers, taken=taken,
                                         decision_seq=seq, policy=policy)

    # ------------------------------------------------------------------ stream 1
    def end_of_turn(self):
        """Write the reward row for the turn that is ending. Exactly-once per (campaign, turn)."""
        row = collect.target_row(self.bus)
        wrote = self.store.write_target_row(row)
        self._turn_done.add((row.get("campaign_id"), row.get("turn")))
        self._seq.clear()                        # decision_seq restarts each turn
        return row, wrote

    # ------------------------------------------------------------------ misc
    def summary(self):
        return self.store.summary()

    def close(self):
        self.store.close()


if __name__ == "__main__":
    # LIVE smoke: observe every context, record one observation each, write the reward row.
    from bus import Bus
    import json
    import tempfile

    run_dir = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
    b = Bus()
    rec = RecorderSession(b, run_dir)
    print("run dir:", run_dir)

    ents = rec.entities()
    print("entities: %d lords, %d regions" % (len(ents["lords"]), len(ents["regions"])))

    for kind, eid in ([("campaign", None)] +
                      [("province", r) for r in ents["regions"][:1]] +
                      [("lord", l["cqi"]) for l in ents["lords"][:1]]):
        feats, offers = rec.observe(kind, eid)
        avail = [o for o in offers if o["available"]]
        pick = {"action_type": "noop", "key": "noop"}      # observation-only smoke
        snap = rec.commit(feats, offers, pick, None, policy="smoke")
        print("  %-9s %-38s offers=%-3d avail=%-3d -> snapshot %s"
              % (kind, str(eid)[:38], len(offers), len(avail), snap))

    row, wrote = rec.end_of_turn()
    print("reward row (inserted=%s): %s" % (wrote, json.dumps(row)))
    print("summary:", rec.summary())
    print("training rows:", len(rec.store.training_rows()))
    rec.close()
