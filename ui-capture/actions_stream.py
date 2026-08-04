r"""actions stream -- per-entity ACTIONABLE-STATE collection into actions.sqlite (the 5th stream).

run(ctx) sweeps lords and settlements via cco_queries at every new turn, plus on-demand refreshes.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "bus"))
sys.path.insert(0, _HERE)

import cco_queries as CQ
from bus import Bus

POLL = 2.0
BUS_BACKOFF = 10.0
REQ_FILE = "actions_requests.jsonl"
DB_FILE = "actions.sqlite"
_TAIL_BYTES = 512 * 1024


def _db(out_dir):
    con = sqlite3.connect(os.path.join(out_dir, DB_FILE), timeout=10.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS snapshots("
                "ts REAL, turn INTEGER, entity_kind TEXT, entity_id TEXT, "
                "action_type TEXT, payload TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS latest("
                "entity_kind TEXT, entity_id TEXT, action_type TEXT, "
                "ts REAL, turn INTEGER, payload TEXT, "
                "PRIMARY KEY(entity_kind, entity_id, action_type))")
    return con


def _write(con, ts, turn, kind, eid, atype, payload):
    p = json.dumps(payload, separators=(",", ":"))
    con.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", (ts, turn, kind, str(eid), atype, p))
    con.execute("INSERT OR REPLACE INTO latest VALUES(?,?,?,?,?,?)",
                (kind, str(eid), atype, ts, turn, p))
    con.commit()


def _current_turn(out_dir):
    """Newest human faction row's (turn, faction) from logs/script_log_*.tail; (None, None) until a state row exists."""
    best = (None, None)
    for p in glob.glob(os.path.join(out_dir, "logs", "script_log_*.tail")):
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - _TAIL_BYTES))
                for line in f.read().decode("utf-8", errors="replace").splitlines():
                    if "TWSTATE " not in line:
                        continue
                    try:
                        r = json.loads(line.split("TWSTATE ", 1)[1])
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if r.get("kind") == "faction" and r.get("is_human"):
                        best = (r.get("turn"), r.get("faction"))
        except OSError:
            continue
    return best


def _sweep_entity(bus, con, ts, turn, kind, eid):
    """Refresh ONE entity's rows. Raises CcoQueryError on a broken chain (caller reports)."""
    if kind == "settlement":
        sa = CQ.settlement_actions(bus, eid)
        _write(con, ts, turn, "settlement", eid, "building_slots",
               {"region": sa["region"], "slots": sa["slots"]})
        _write(con, ts, turn, "settlement", eid, "edicts", sa["edicts"] or {})
    elif kind == "lord":
        la = CQ.lord_actions(bus, eid)
        _write(con, ts, turn, "lord", eid, "stances", {"stances": la["stances"]})
        _write(con, ts, turn, "lord", eid, "force",
               {"unit_count": la["unit_count"], "pending_recruits": la["pending_recruits"],
                "action_point_pct": la["action_point_pct"], "no_force": la.get("no_force", False)})
    else:
        raise CQ.CcoQueryError("unknown entity kind %r" % kind)


def _full_sweep(bus, con, ctx, turn):
    """All lords + all settlements. Returns (n_ok, n_err); every failure is loud."""
    ts = ctx.now()
    ents = CQ.list_entities(bus)
    _write(con, ts, turn, "campaign", "entities", "entities", ents)
    n_ok = n_err = 0
    for region in ents["regions"]:
        try:
            _sweep_entity(bus, con, ctx.now(), turn, "settlement", region)
            n_ok += 1
        except CQ.CcoQueryError as e:
            n_err += 1
            ctx.on_error("actions-sweep settlement %s" % region, e)
            ctx.emit({"kind": "actions_error", "entity": region, "err": str(e)[:200]})
    for lord in ents["lords"]:
        try:
            _sweep_entity(bus, con, ctx.now(), turn, "lord", lord["cqi"])
            n_ok += 1
        except CQ.CcoQueryError as e:
            n_err += 1
            ctx.on_error("actions-sweep lord %s" % lord["cqi"], e)
            ctx.emit({"kind": "actions_error", "entity": lord["cqi"], "err": str(e)[:200]})
    return n_ok, n_err


def run(ctx, bus=None):
    """Turn-start full sweeps + on-demand per-entity refreshes until ctx.is_running() flips."""
    bus = bus or Bus()
    swept_turn = None
    cur_dir = None
    con = None
    req_off = 0
    while ctx.is_running():
        try:
            out_dir = ctx.out_dir
            if out_dir != cur_dir:
                if con is not None:
                    con.close()
                con = _db(out_dir)
                cur_dir = out_dir
                swept_turn = None
                req_off = 0
            turn, faction = _current_turn(out_dir)
            if turn is None:
                turn = int(CQ._ev(bus, "return cm:model():turn_number()"))
            if turn is not None and turn != swept_turn:
                t0 = time.time()
                n_ok, n_err = _full_sweep(bus, con, ctx, turn)
                ctx.emit({"kind": "actions_sweep", "turn": turn, "faction": faction,
                          "ok": n_ok, "err": n_err, "secs": round(time.time() - t0, 2)})
                swept_turn = turn
            rp = os.path.join(out_dir, REQ_FILE)
            if os.path.exists(rp):
                with open(rp, encoding="utf-8", errors="replace") as f:
                    f.seek(req_off)
                    new = f.read()
                    req_off = f.tell()
                for line in new.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        req = json.loads(line)
                    except json.JSONDecodeError:
                        ctx.on_error("actions-request parse", ValueError(line[:120]))
                        continue
                    kind, eid = req.get("entity_kind"), req.get("entity_id")
                    try:
                        if kind == "all" or eid == "all":
                            n_ok, n_err = _full_sweep(bus, con, ctx, swept_turn)
                            ctx.emit({"kind": "actions_refresh", "entity": "all",
                                      "ok": n_ok, "err": n_err})
                        else:
                            _sweep_entity(bus, con, ctx.now(), swept_turn, kind, eid)
                            ctx.emit({"kind": "actions_refresh", "entity": "%s:%s" % (kind, eid)})
                    except CQ.CcoQueryError as e:
                        ctx.on_error("actions-refresh %s:%s" % (kind, eid), e)
                        ctx.emit({"kind": "actions_error", "entity": "%s:%s" % (kind, eid),
                                  "err": str(e)[:200]})
        except CQ.CcoQueryError as e:
            ctx.on_error("actions-stream bus", e)
            ctx.emit({"kind": "actions_status", "status": "bus_unavailable", "err": str(e)[:160]})
            time.sleep(BUS_BACKOFF)
            continue
        except Exception as e:
            ctx.on_error("actions-stream", e)
            time.sleep(BUS_BACKOFF)
            continue
        time.sleep(POLL)
    if con is not None:
        con.close()
