from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.BUS)

import collect
import journal
from decisions.store2 import Store

POLL = 0.1

PRUNE_EVERY = 600


EVENT_KINDS = ("incident_occured", "dilemma_issued", "dilemma_choice_made",
               "ancillary_gained")


BATTLE_KIND = "battle_completed"
FINANCE_KIND = "finance_income"


def _drain_events(bus, store, offset, counts, ctx):
    rows, offset = bus.drain_rows(EVENT_KINDS + (BATTLE_KIND, FINANCE_KIND), offset)
    if not rows:
        return offset
    finance = [r for r in rows if r.get("cmd") == FINANCE_KIND]
    battles = [r for r in rows if r.get("cmd") == BATTLE_KIND]
    rows = [r for r in rows if r.get("cmd") not in (BATTLE_KIND, FINANCE_KIND)]
    if finance:
        try:
            nf = store.write_finance(finance)
            counts["finance"] = counts.get("finance", 0) + nf
            ctx.emit({"kind": "decisions_finance", "n": nf})
        except Exception as e:
            counts["error"] += 1
            ctx.on_error("decisions-finance", e)
    if battles:
        try:
            nb = store.write_battles(battles)
            counts["battle"] = counts.get("battle", 0) + nb
            ctx.emit({"kind": "decisions_battles", "n": nb})
        except Exception as e:
            counts["error"] += 1
            ctx.on_error("decisions-battles", e)
    if not rows:
        return offset
    out = []
    for r in rows:
        out.append({"kind": r.get("cmd"), "turn": r.get("turn"), "ts": r.get("ts"),
                    "incident": r.get("incident"), "dilemma": r.get("dilemma"),
                    "choice": r.get("choice"), "faction": r.get("faction"),
                    "ancillary": r.get("ancillary"), "cqi": r.get("cqi"),
                    "region": r.get("region")})
    try:
        n = store.write_events(out)
        counts["event"] = counts.get("event", 0) + n
        ctx.emit({"kind": "decisions_events", "n": n})
    except Exception as e:
        counts["error"] += 1
        ctx.on_error("decisions-events", e)
    return offset


def run(ctx):
    from advisor.reference import check as refcheck
    refcheck.ensure()
    from bus import Bus
    bus = Bus()
    store, cur_dir, after_id = None, None, 0
    event_off = bus.out_offset()
    ticks = 0
    counts = {"snapshot": 0, "turn": 0, "hash": 0, "decide": 0,
              "verification": 0, "event": 0, "battle": 0, "error": 0}
    t_loop = time.time()
    sys.stderr.write("decisions_stream: poll loop starting (tick %.2fs)\n" % POLL)
    while ctx.is_running():
        try:
            out_dir = ctx.out_dir
            if out_dir != cur_dir:
                if store is not None:
                    store.close()
                store = Store()
                cur_dir, after_id = out_dir, journal.cursor(out_dir)
                ctx.emit({"kind": "decisions_status", "status": "store_open",
                          "db": journal.pg.dsn(), "cursor": after_id,
                          "code_version": os.environ.get("TW_CODE_VERSION")})
            rows, after_id = journal.read_requests(out_dir, after_id)
            for row in rows:
                kind, rid = row.get("rpc_kind"), row.get("req_id")
                try:
                    if kind == "snapshot":
                        t0 = time.time()
                        pickup_lag_ms = int((t0 - (row.get("ts") or t0)) * 1000)
                        snap = collect.snapshot(bus, active=row.get("active"))
                        t1 = time.time()
                        did = store.write_snapshot(snap, row.get("decision_uuid")
                                                   or rid, req_id=rid)
                        t2 = time.time()
                        counts["snapshot"] += 1
                        journal.respond(out_dir, rid, snapshot_id=did,
                                        collect_ms=int((t1 - t0) * 1000),
                                        store_ms=int((t2 - t1) * 1000),
                                        pickup_lag_ms=pickup_lag_ms)
                        ctx.emit({"kind": "decisions_point", "decision_id": did,
                                  "entities": len(snap["entities"]),
                                  "turn": snap["campaign"].get("turn"),
                                  "ms": int((time.time() - t0) * 1000),
                                  "profile": snap.get("profile")})
                    elif kind == "turn":
                        cs = collect.campaign_state(bus)
                        counts["turn"] += 1
                        journal.respond(out_dir, rid, turn=cs.get("turn"),
                                        campaign_uuid=cs.get("campaign_uuid"))
                    elif kind == "hash":
                        h = collect.state_hash(bus)
                        counts["hash"] += 1
                        journal.respond(out_dir, rid, hash=h["hash"], roots=h["roots"])
                    elif kind == "interrupt":
                        store.write_interrupt(row, req_id=rid)
                        counts["interrupt"] = counts.get("interrupt", 0) + 1
                        ctx.emit({"kind": "decisions_interrupt", "screen": row.get("kind"),
                                  "chosen": row.get("chosen"),
                                  "turn": (row.get("campaign") or {}).get("turn")})
                    elif kind == "diplomacy":
                        store.write_diplomacy(row, req_id=rid)
                        counts["diplomacy"] = counts.get("diplomacy", 0) + 1
                    elif kind == "ucb_pick":
                        store.write_ucb_pick(row, req_id=rid)
                        counts["ucb_pick"] = counts.get("ucb_pick", 0) + 1
                    elif kind == "postmortem":
                        store.write_postmortem(row, req_id=rid)
                        counts["postmortem"] = counts.get("postmortem", 0) + 1
                        ctx.emit({"kind": "decisions_postmortem",
                                  "campaign": row.get("campaign_key"),
                                  "outcome": row.get("outcome")})
                    elif kind == "decide":
                        did = row.get("decision_id")
                        store.write_decide(did, row.get("offers"), row.get("pick"),
                                           scores=row.get("scores"),
                                           timings=row.get("timings"), req_id=rid)
                        counts["decide"] += 1
                        pick = row.get("pick") or {}
                        ctx.emit({"kind": "decisions_pick", "decision_id": did,
                                  "action": pick.get("action_type"),
                                  "key": pick.get("key")})
                    elif kind == "verification":
                        did = row.get("decision_id")
                        res = row.get("result") or {}
                        store.write_verification(did, res, req_id=rid)
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
            if store is not None:
                event_off = _drain_events(bus, store, event_off, counts, ctx)
            ticks += 1
            if ticks % PRUNE_EVERY == 0 and after_id:
                gone_a, gone_b = journal.prune(out_dir, after_id)
                if gone_a or gone_b:
                    ctx.emit({"kind": "decisions_status", "status": "pruned",
                              "requests": gone_a, "responses": gone_b})
        except Exception as e:
            ctx.on_error("decisions-stream", e)
            common.wait("decisions_error_backoff", 5.0, repr(e)[:80])
            continue
        journal.wait_requests(out_dir, POLL)
    common.waitlog("decisions_poll", time.time() - t_loop, True, "stopped")
    if store is not None:
        ctx.emit({"kind": "decisions_status", "status": "closing", **counts})
        store.close()
