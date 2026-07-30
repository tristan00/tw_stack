r"""decisions_stream.py -- the v7 recorder stream. THE ONLY PROCESS THAT READS THE GAME FOR THE
ADVISOR, and THE SOLE WRITER of decisions.sqlite.

v7 data is a clean break from v6: no UI-component scraping, no input/shot capture, no offline
synthesis of "what probably happened". The only thing recorded is DECISION POINTS -- the raw
campaign/world/entity state, every action that was offered, the one that was chosen, and hard
evidence of whether it actually happened -- plus one reward row per turn.

It services four request kinds off <run>/decisions_requests.jsonl:

    snapshot      read the game NOW -> persist a faction-wide decision point -> reply decision_id
    target        read + persist this turn's reward row                      -> reply row
    turn          reply the current turn number
    pick          attach the advisor's chosen offer (+ its ranking) to a decision   [stream 3]
    verification  attach the launcher's ActionRecord to a decision                  [stream 4]

Contract with the manager (same as every other stream):
    run(ctx)  with ctx.emit / ctx.now / ctx.is_running / ctx.on_error / ctx.out_dir
ctx.out_dir is re-read every iteration, so an R3 campaign swap moves the DB to the new run dir.

Loud by design: a malformed or unserviceable request is answered with an explicit error (so the
advisor raises instead of hanging) AND reported through ctx.on_error -- it is never dropped,
because a missing decision point is indistinguishable from "the advisor did nothing".
"""
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
    counts = {"snapshot": 0, "target": 0, "turn": 0, "hash": 0, "pick": 0,
              "verification": 0, "error": 0}
    while ctx.is_running():
        try:
            out_dir = ctx.out_dir
            if out_dir != cur_dir:                       # first run or R3 campaign swap
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
                        did = store.write_decision(snap, decision_seq=seq)
                        seq += 1
                        counts["snapshot"] += 1
                        n = sum(len(e["offers"]) for e in snap["entities"])
                        journal.respond(out_dir, rid, decision_id=did)
                        ctx.emit({"kind": "decisions_point", "decision_id": did,
                                  "entities": len(snap["entities"]), "offers": n,
                                  "turn": snap["campaign"].get("turn"),
                                  "ms": int((time.time() - t0) * 1000)})
                    elif kind == "target":
                        r = collect.target_row(bus)
                        wrote = store.write_target_row(r)
                        counts["target"] += 1
                        journal.respond(out_dir, rid, row=r, inserted=bool(wrote))
                        ctx.emit({"kind": "decisions_target", "turn": r.get("turn"),
                                  "inserted": bool(wrote)})
                    elif kind == "turn":
                        t = collect.campaign_state(bus).get("turn")
                        counts["turn"] += 1
                        journal.respond(out_dir, rid, turn=t)
                    elif kind == "hash":
                        h = collect.state_hash(bus)
                        counts["hash"] += 1
                        journal.respond(out_dir, rid, hash=h["hash"], roots=h["roots"])
                    elif kind == "pick":
                        did = row.get("decision_id")
                        store.attach_scores(did, row.get("scores"))
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
                except Exception as e:                    # one bad request never kills the stream
                    counts["error"] += 1
                    ctx.on_error("decisions-%s" % kind, e)
                    ctx.emit({"kind": "decisions_error", "req_kind": kind, "err": repr(e)[:200]})
                    if rid:                               # unblock the waiting advisor, loudly
                        journal.respond(out_dir, rid, error=repr(e)[:300])
        except Exception as e:
            ctx.on_error("decisions-stream", e)
            time.sleep(5.0)
            continue
        time.sleep(POLL)
    if store is not None:
        ctx.emit({"kind": "decisions_status", "status": "closing", **counts})
        store.close()
