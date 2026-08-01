"""The recorder stream: the only reader of the game for the advisor and the sole writer of
decisions.sqlite. Services requests off <run>/decisions_requests.jsonl."""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, r"D:\tw_stack\bus")

import collect                                   # noqa: E402
import journal                                   # noqa: E402
from store import DecisionStore                  # noqa: E402

POLL = 0.4


def run(ctx):
    from bus import Bus
    bus = Bus()
    store, cur_dir, offset = None, None, 0
    seq = 0
    last_uuid = [None]

    def campaign_changed(snap_camp):
        """Record a campaign change; always returns False -- the run dir never rotates."""
        u = (snap_camp or {}).get("campaign_uuid")
        if not u or u == last_uuid[0]:
            return False
        if last_uuid[0] is not None:
            ctx.emit({"kind": "decisions_status", "status": "campaign_changed",
                      "from": last_uuid[0], "to": u, "same_dir": True})
        last_uuid[0] = u
        return False
    counts = {"snapshot": 0, "target": 0, "turn": 0, "hash": 0, "pick": 0,
              "verification": 0, "error": 0}
    while ctx.is_running():
        try:
            out_dir = ctx.out_dir
            if out_dir != cur_dir:
                if store is not None:
                    store.close()
                store = DecisionStore(out_dir)
                cur_dir, offset, seq = out_dir, 0, 0
                ctx.emit({"kind": "decisions_status", "status": "store_open",
                          "db": os.path.join(out_dir, "decisions.sqlite")})
            rows, offset = journal.read_requests(out_dir, offset)
            for row in rows:
                kind, rid = row.get("kind"), row.get("req_id")
                try:
                    if kind == "snapshot":
                        t0 = time.time()
                        snap = collect.snapshot(bus, active=row.get("active"))
                        if campaign_changed(snap.get("campaign")):
                            seq = 0
                        did = store.write_decision(snap, decision_seq=seq)
                        seq += 1
                        counts["snapshot"] += 1
                        n = sum(len(e["offers"]) for e in snap["entities"])
                        journal.respond(out_dir, rid, decision_id=did,
                                        collect_ms=int((time.time() - t0) * 1000))
                        ctx.emit({"kind": "decisions_point", "decision_id": did,
                                  "entities": len(snap["entities"]), "offers": n,
                                  "turn": snap["campaign"].get("turn"),
                                  "ms": int((time.time() - t0) * 1000),
                                  "profile": snap.get("profile")})
                    elif kind == "target":
                        r = collect.target_row(bus)
                        wrote = store.write_target_row(r)
                        ents = collect.entity_target_rows(bus)
                        store.write_entity_target_rows(
                            store.campaign_key(r.get("campaign_id"), r.get("campaign_uuid")),
                            r.get("turn"), ents)
                        counts["target"] += 1
                        journal.respond(out_dir, rid, row=r, inserted=bool(wrote))
                        ctx.emit({"kind": "decisions_target", "turn": r.get("turn"),
                                  "inserted": bool(wrote)})
                    elif kind == "turn":
                        cs = collect.campaign_state(bus)
                        campaign_changed(cs)
                        counts["turn"] += 1
                        journal.respond(out_dir, rid, turn=cs.get("turn"),
                                        campaign_uuid=cs.get("campaign_uuid"))
                    elif kind == "hash":
                        h = collect.state_hash(bus)
                        counts["hash"] += 1
                        journal.respond(out_dir, rid, hash=h["hash"], roots=h["roots"])
                    elif kind == "interrupt":
                        cs = collect.campaign_state(bus)
                        store.write_interrupt(dict(row, campaign=cs))
                        counts["interrupt"] = counts.get("interrupt", 0) + 1
                        ctx.emit({"kind": "decisions_interrupt", "screen": row.get("kind_screen")
                                  or row.get("screen"), "chosen": row.get("chosen"),
                                  "turn": cs.get("turn")})
                    elif kind == "pick":
                        did = row.get("decision_id")
                        store.attach_scores(did, row.get("scores"))
                        store.attach_timings(did, row.get("timings"))
                        pick = row.get("pick") or {}
                        store.attach_taken(did, dict(pick, executed=False, confirmed=False,
                                                     counted=False, refusal="awaiting_execution"),
                                           policy=pick.get("policy"))
                        counts["pick"] += 1
                        ctx.emit({"kind": "decisions_pick", "decision_id": did,
                                  "context": pick.get("context_kind"),
                                  "action": pick.get("action_type"), "key": pick.get("key")})
                    elif kind == "verification":
                        did = row.get("decision_id")
                        res = row.get("result") or {}
                        store.attach_taken(did, res, policy=res.get("policy"))
                        counts["verification"] += 1
                        ctx.emit({"kind": "decisions_verify", "decision_id": did,
                                  "action": res.get("action_type"), "key": res.get("key"),
                                  "counted": bool(res.get("counted")),
                                  "refusal": res.get("refusal")})
                    else:
                        raise ValueError("unknown request kind %r" % kind)
                except Exception as e:
                    counts["error"] += 1
                    ctx.on_error("decisions-%s" % kind, e)
                    _m = repr(e)
                    ctx.emit({"kind": "decisions_error", "req_kind": kind,
                              "err": _m if len(_m) <= 700 else _m[:200] + " ...<<cut>>... " + _m[-500:]})
                    if rid:
                        journal.respond(out_dir, rid, error=(lambda _m: _m if len(_m) <= 700 else
                                                 _m[:200] + " ...<<cut>>... " + _m[-500:])(repr(e)))
        except Exception as e:
            ctx.on_error("decisions-stream", e)
            time.sleep(5.0)
            continue
        time.sleep(POLL)
    if store is not None:
        ctx.emit({"kind": "decisions_status", "status": "closing", **counts})
        store.close()
