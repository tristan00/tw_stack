from __future__ import annotations

import glob
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "bus"))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import cco_queries as CQ
from bus import Bus

POLL = 2.0
BUS_BACKOFF = 10.0
REQ_FILE = "actions_requests.jsonl"
_TAIL_BYTES = 512 * 1024


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def _current_turn(out_dir):
    paths = sorted(glob.glob(os.path.join(out_dir, "logs", "script_log_*.tail")),
                   key=_mtime, reverse=True)
    for p in paths:
        best = (None, None)
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
        if best != (None, None):
            return best
    return (None, None)


def _sweep_entity(bus, ctx, ts, turn, kind, eid):
    if kind == "settlement":
        sa = CQ.settlement_actions(bus, eid)
        ctx.emit({"kind": "actions_settlement", "ts": ts, "turn": turn, "entity": eid,
                  "region": sa["region"], "slots": sa["slots"],
                  "edicts": sa["edicts"] or {}})
    elif kind == "lord":
        la = CQ.lord_actions(bus, eid)
        ctx.emit({"kind": "actions_lord", "ts": ts, "turn": turn, "entity": eid,
                  "stances": la["stances"], "unit_count": la["unit_count"],
                  "pending_recruits": la["pending_recruits"],
                  "action_point_pct": la["action_point_pct"],
                  "no_force": la.get("no_force", False)})
    else:
        raise CQ.CcoQueryError("unknown entity kind %r" % kind)


def _full_sweep(bus, ctx, turn):
    ts = ctx.now()
    ents = CQ.list_entities(bus)
    ctx.emit({"kind": "actions_entities", "ts": ts, "turn": turn, "entities": ents})
    n_ok = n_err = 0
    for region in ents["regions"]:
        try:
            _sweep_entity(bus, ctx, ctx.now(), turn, "settlement", region)
            n_ok += 1
        except CQ.CcoQueryError as e:
            n_err += 1
            ctx.on_error("actions-sweep settlement %s" % region, e)
            ctx.emit({"kind": "actions_error", "entity": region, "err": str(e)[:200]})
    for lord in ents["lords"]:
        try:
            _sweep_entity(bus, ctx, ctx.now(), turn, "lord", lord["cqi"])
            n_ok += 1
        except CQ.CcoQueryError as e:
            n_err += 1
            ctx.on_error("actions-sweep lord %s" % lord["cqi"], e)
            ctx.emit({"kind": "actions_error", "entity": lord["cqi"], "err": str(e)[:200]})
    return n_ok, n_err


def run(ctx, bus=None):
    bus = bus or Bus()
    swept_turn = None
    cur_dir = None
    req_off = 0
    while ctx.is_running():
        try:
            out_dir = ctx.out_dir
            if out_dir != cur_dir:
                cur_dir = out_dir
                swept_turn = None
                req_off = 0
            turn, faction = _current_turn(out_dir)
            if turn is None:
                turn = int(CQ._ev(bus, "return cm:model():turn_number()"))
            if turn is not None and turn != swept_turn:
                t0 = time.time()
                n_ok, n_err = _full_sweep(bus, ctx, turn)
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
                            n_ok, n_err = _full_sweep(bus, ctx, swept_turn)
                            ctx.emit({"kind": "actions_refresh", "entity": "all",
                                      "ok": n_ok, "err": n_err})
                        else:
                            _sweep_entity(bus, ctx, ctx.now(), swept_turn, kind, eid)
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
