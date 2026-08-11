from __future__ import annotations

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

REQUESTS = "decisions_requests.jsonl"
RESPONSES = "decisions_responses.jsonl"
DB_NAME = "decisions.sqlite"
RUNS_ROOT = common.RUNS_ROOT


RUN_DIR = common.RUN_DIR


def current_run_dir(runs_root=RUNS_ROOT, timeout=0.0):
    return RUN_DIR


# Serialises the jsonl appends below. `import threading` survived the path refactor in
# 4c74853 and this line did not, so both `with _io_lock:` blocks raised NameError on the
# first journal write -- which is the first thing that happens after a campaign loads.
# Every campaign errored at once and no decision was ever recorded.
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


def request_snapshot(run_dir, active=None, timeout=180.0):
    rid = _new_id("snapshot")
    t_request = time.time()
    _append(run_dir, REQUESTS, {"kind": "snapshot", "req_id": rid, "active": active})
    reply = _await(run_dir, rid, timeout)
    did = reply.get("decision_id")
    rec = reply.get("record")
    if rec is None:
        raise RuntimeError(
            "recorder answered snapshot %s without a record. The advisor does not read "
            "the decision database -- the recorder owns it and hands the record back." % rid)
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




def request_turn(run_dir, timeout=60.0):
    rid = _new_id("turn")
    _append(run_dir, REQUESTS, {"kind": "turn", "req_id": rid})
    return _await(run_dir, rid, timeout).get("turn")


def request_hash(run_dir, timeout=45.0):
    rid = _new_id("hash")
    _append(run_dir, REQUESTS, {"kind": "hash", "req_id": rid})
    r = _await(run_dir, rid, timeout)
    return r.get("hash"), r.get("roots") or []


def log_options(run_dir, decision_id, options):
    """Hand the advisor's surviving options to the recorder, which owns every write."""
    _append(run_dir, REQUESTS, {"kind": "options", "decision_id": decision_id,
                                "options": options})


def log_pick(run_dir, decision_id, pick, scores=None, timings=None):
    _append(run_dir, REQUESTS, {"kind": "pick", "decision_id": decision_id,
                                "pick": pick, "scores": scores, "timings": timings})


def log_verification(run_dir, decision_id, result):
    _append(run_dir, REQUESTS, {"kind": "verification", "decision_id": decision_id, "result": result})






