from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

REQUESTS = "decisions_requests.jsonl"
RESPONSES = "decisions_responses.jsonl"
DB_NAME = "decisions.sqlite"
RUNS_ROOT = "D:/twdata/runs/human"
CURRENT_POINTER = "CURRENT_RUN"


RUN_DIR = "D:/twdata/runs/human/run"


def current_run_dir(runs_root=RUNS_ROOT, timeout=0.0):
    return RUN_DIR


def _unused_current_run_dir(runs_root=RUNS_ROOT, timeout=0.0):
    path = os.path.join(runs_root, CURRENT_POINTER)
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            with open(path, encoding="utf-8") as f:
                d = f.read().strip()
            if d and os.path.isdir(d):
                return d
        except OSError:
            pass
        if time.time() >= deadline:
            raise RuntimeError("no live run dir at %s -- is the manager running?" % path)
        time.sleep(0.5)


_io_lock = threading.Lock()


def _append(run_dir, name, row):
    row.setdefault("ts", time.time())
    line = json.dumps(row, default=str) + "\n"
    with _io_lock:
        with open(os.path.join(run_dir, name), "a", encoding="utf-8") as f:
            f.write(line)


def _read_rows(path, offset=0):
    if not os.path.exists(path):
        return [], offset
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"kind": "malformed", "raw": line[:200]})
        offset = f.tell()
    return rows, offset


def read_requests(run_dir, offset=0):
    return _read_rows(os.path.join(run_dir, REQUESTS), offset)


def requests_size(run_dir):
    try:
        return os.path.getsize(os.path.join(run_dir, REQUESTS))
    except OSError:
        return 0


def respond(run_dir, req_id, **payload):
    _append(run_dir, RESPONSES, dict(payload, req_id=req_id,
                                     served_by=str(run_dir).replace("\\", "/")))


_req_seq = [0]


def _new_id(kind):
    with _io_lock:
        _req_seq[0] += 1
        seq = _req_seq[0]
    return "%s-%d-%d" % (kind, int(time.time() * 1000), seq)


def _await(run_dir, req_id, timeout, poll=0.05):
    path = os.path.join(run_dir, RESPONSES)
    offset, deadline = 0, time.time() + timeout
    while time.time() < deadline:
        rows, offset = _read_rows(path, offset)
        for r in rows:
            if r.get("req_id") == req_id:
                if r.get("error"):
                    raise RuntimeError("recorder failed request %s: %s" % (req_id, r["error"]))
                return r
        time.sleep(poll)
    hint = ""
    try:
        live = current_run_dir()
        if os.path.abspath(live) != os.path.abspath(run_dir):
            hint = (" -- THE RECORDER HAS MOVED: it is now servicing %s, not %s. The campaign "
                    "almost certainly changed; re-resolve with journal.current_run_dir()."
                    % (live, run_dir))
    except RuntimeError:
        hint = " -- no live run dir is published either; the manager looks dead."
    raise RuntimeError("recorder never answered request %s within %ss%s" % (req_id, timeout, hint))


def request_snapshot(run_dir, active=None, timeout=180.0, diplo_epoch=None):
    rid = _new_id("snapshot")
    t_request = time.time()
    _append(run_dir, REQUESTS, {"kind": "snapshot", "req_id": rid, "active": active,
                                "diplo_epoch": diplo_epoch})
    reply = _await(run_dir, rid, timeout)
    did = reply.get("decision_id")
    rec = read_decision(run_dir, did)
    rec["_t_request"] = t_request
    rec["_t_received"] = time.time()
    rec["_collect_ms"] = reply.get("collect_ms")
    rec["_store_ms"] = reply.get("store_ms")
    rec["_pickup_lag_ms"] = reply.get("pickup_lag_ms")
    return did, rec


def log_interrupt(run_dir, payload):
    body = dict(payload)
    body["screen"] = body.pop("kind", None)
    body["kind"] = "interrupt"
    _append(run_dir, REQUESTS, body)


def request_target(run_dir, timeout=120.0):
    rid = _new_id("target")
    _append(run_dir, REQUESTS, {"kind": "target", "req_id": rid})
    return _await(run_dir, rid, timeout).get("row")


def _retry_once(fn, what):
    try:
        return fn()
    except RuntimeError as e:
        if "never answered" not in str(e):
            raise
        sys.stderr.write("journal: %s timed out, retrying once -- %s\n" % (what, str(e)[:120]))
        return fn()


def request_turn(run_dir, timeout=60.0):
    rid = _new_id("turn")
    _append(run_dir, REQUESTS, {"kind": "turn", "req_id": rid})
    return _await(run_dir, rid, timeout).get("turn")


def request_hash(run_dir, timeout=45.0):
    rid = _new_id("hash")
    _append(run_dir, REQUESTS, {"kind": "hash", "req_id": rid})
    r = _await(run_dir, rid, timeout)
    return r.get("hash"), r.get("roots") or []


def log_pick(run_dir, decision_id, pick, scores=None, timings=None):
    _append(run_dir, REQUESTS, {"kind": "pick", "decision_id": decision_id,
                                "pick": pick, "scores": scores, "timings": timings})


def log_verification(run_dir, decision_id, result):
    _append(run_dir, REQUESTS, {"kind": "verification", "decision_id": decision_id, "result": result})


def _con(run_dir):
    path = os.path.join(run_dir, DB_NAME)
    if not os.path.exists(path):
        raise RuntimeError("no decisions.sqlite in %s -- the recorder has not opened it yet" % run_dir)
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=15.0)
    con.row_factory = sqlite3.Row
    return con


def read_decision(run_dir, decision_id):
    con = _con(run_dir)
    try:
        dp = con.execute("SELECT * FROM decision_points WHERE decision_id=?",
                         (decision_id,)).fetchone()
        if dp is None:
            raise RuntimeError("decision %s not in the store" % decision_id)
        ents, by_snap = [], {}
        for r in con.execute("SELECT * FROM entity_snapshots WHERE decision_id=?", (decision_id,)):
            e = {"snapshot_id": r["snapshot_id"], "context_kind": r["context_kind"],
                 "context_id": r["context_id"], "state": json.loads(r["features"]), "offers": []}
            by_snap[r["snapshot_id"]] = e
            ents.append(e)
        for r in con.execute("SELECT * FROM action_offers WHERE decision_id=?", (decision_id,)):
            e = by_snap.get(r["snapshot_id"])
            if e is not None:
                e["offers"].append({"offer_id": r["offer_id"], "action_type": r["action_type"],
                                    "key": r["action_key"], "available": bool(r["available"]),
                                    "gate": r["gate"], "params": json.loads(r["params"] or "{}")})
        return {"decision_id": decision_id, "turn": dp["turn"], "campaign_id": dp["campaign_id"],
                "campaign": json.loads(dp["campaign"] or "{}"),
                "world": json.loads(dp["world"] or "{}"), "entities": ents}
    finally:
        con.close()


def labelled_decisions(run_dir):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from store import DecisionStore
    s = DecisionStore(run_dir)
    try:
        return s.labelled_decisions(), s.target_series()
    finally:
        s.close()
