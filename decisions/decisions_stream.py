r"""decisions_stream.py -- the v7 recorder stream. THE SOLE WRITER of decisions.sqlite.

v7 data is a clean break from v6: no UI-component scraping, no input/shot capture, no offline
synthesis of "what probably happened". The only thing recorded is DECISION POINTS --
context features, the actions that were offered, the one that was chosen, and hard evidence of
whether it actually happened -- plus one reward row per turn.

Contract with the manager (same as every other stream):
    run(ctx)  with ctx.emit / ctx.now / ctx.is_running / ctx.on_error / ctx.out_dir

It tails <run>/decisions_journal.jsonl (written by the advisor and the launcher through
decisions/journal.py) and persists each row via DecisionStore. ctx.out_dir is re-read every
iteration, so an R3 campaign swap moves the DB to the new run dir automatically.

Loud by design: a malformed or unpersistable journal row is reported through ctx.on_error and
emitted as an error row -- it is never silently dropped, because a missing decision point is
indistinguishable from "the advisor did nothing" unless we say so.
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import journal                                  # noqa: E402
from store import DecisionStore                 # noqa: E402

POLL = 1.0


def run(ctx):
    store, cur_dir, offset = None, None, 0
    counts = {"decision": 0, "target": 0, "verification": 0, "error": 0}
    while ctx.is_running():
        try:
            out_dir = ctx.out_dir
            if out_dir != cur_dir:                       # first run or R3 campaign swap
                if store is not None:
                    store.close()
                store = DecisionStore(out_dir)
                cur_dir, offset = out_dir, 0
                ctx.emit({"kind": "decisions_status", "status": "store_open",
                          "db": os.path.join(out_dir, "decisions.sqlite")})
            rows, offset = journal.read_journal(out_dir, offset)
            for row in rows:
                kind = row.get("kind")
                try:
                    if kind == "target":
                        wrote = store.write_target_row(row.get("row") or {})
                        counts["target"] += 1
                        ctx.emit({"kind": "decisions_target", "turn": (row.get("row") or {}).get("turn"),
                                  "inserted": bool(wrote)})
                    elif kind == "decision":
                        snap = store.write_decision(
                            row.get("features") or {}, row.get("offers") or [],
                            taken=_taken_from(row), decision_seq=row.get("decision_seq") or 0,
                            policy=row.get("policy"))
                        counts["decision"] += 1
                        ctx.emit({"kind": "decisions_point", "snapshot_id": snap,
                                  "context": (row.get("features") or {}).get("context_kind"),
                                  "offers": len(row.get("offers") or []),
                                  "pick": (row.get("pick") or {}).get("action_type")})
                    elif kind == "verification":
                        # a late-arriving launcher result for a decision already stored
                        n = _apply_verification(store, row)
                        counts["verification"] += 1
                        ctx.emit({"kind": "decisions_verify", "decision_id": row.get("decision_id"),
                                  "updated": n})
                    else:
                        counts["error"] += 1
                        ctx.on_error("decisions-journal", ValueError("unknown row kind %r" % kind))
                except Exception as e:                    # one bad row never kills the stream
                    counts["error"] += 1
                    ctx.on_error("decisions-write(%s)" % kind, e)
                    ctx.emit({"kind": "decisions_error", "row_kind": kind, "err": repr(e)[:180]})
        except Exception as e:
            ctx.on_error("decisions-stream", e)
            time.sleep(5.0)
            continue
        time.sleep(POLL)
    if store is not None:
        ctx.emit({"kind": "decisions_status", "status": "closing", **counts})
        store.close()


def _taken_from(row):
    """The taken action for a journal decision row: the launcher result when present, else a
    noop pick recorded as trivially confirmed ('the model chose to stop' is a real label)."""
    pick, result = row.get("pick"), row.get("result")
    if pick is None:
        return None
    if result is not None:
        taken = dict(result)
        taken.setdefault("action_type", pick.get("action_type"))
        taken.setdefault("key", pick.get("key"))
        return taken
    if pick.get("action_type") == "noop":
        return {"action_type": "noop", "key": "noop", "executed": True, "confirmed": True,
                "counted": True, "refusal": None,
                "confirm": {"signal": "none", "before": {}, "after": {}, "latency_ms": 0}}
    return None                                   # decided but not yet executed -> awaits stream 4


def _apply_verification(store, row):
    """Attach a late launcher ActionRecord to the most recent matching decision snapshot."""
    res = row.get("result") or {}
    atype, akey = res.get("action_type"), str(res.get("key"))
    hit = store.con.execute(
        "SELECT snapshot_id, offer_id FROM action_offers WHERE action_type=? AND action_key=? "
        "ORDER BY offer_id DESC LIMIT 1", (atype, akey)).fetchone()
    if not hit:
        return 0
    snap, offer_id = hit
    conf = res.get("confirm") or {}
    import json as _json
    store.con.execute(
        "INSERT INTO action_taken(snapshot_id,offer_id,ts,action_type,action_key,executed,confirmed,"
        "counted,refusal,confirm_signal,confirm_before,confirm_after,latency_ms,policy)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (snap, offer_id, row.get("ts") or time.time(), atype, akey,
         1 if res.get("executed") else 0, 1 if res.get("confirmed") else 0,
         1 if res.get("counted") else 0, res.get("refusal"), conf.get("signal"),
         _json.dumps(conf.get("before"), default=str), _json.dumps(conf.get("after"), default=str),
         conf.get("latency_ms"), res.get("policy")))
    store.con.commit()
    return 1
