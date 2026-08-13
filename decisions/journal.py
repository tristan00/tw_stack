from __future__ import annotations

"""The advisor <-> recorder channel, in the database.

Two processes, one conversation: the advisor asks for a snapshot / a target / the turn / a
state hash, and the recorder -- which owns decisions.sqlite and is the only thing that
writes the corpus -- answers. The advisor also hands back what it decided (options, pick,
verification) so the recorder can attach it to the decision it already wrote.

WHY THIS IS NOT A PAIR OF FILES ANY MORE. It used to be `decisions_requests.jsonl` and
`decisions_responses.jsonl`, appended by one side and scanned by the other. The scan
started at byte 0 every single call -- to find one req_id, `_await` read and JSON-parsed
the entire responses log. That is O(everything written so far), per request, against a
file that only grows. Measured on the run that exposed it: 11.85 ms per MB, a 234 MB
responses log, ~3000 ms per request by the end of the day, and 3.37 hours of a 24.19-hour
run -- 13.9% of wall clock -- spent scanning text. Reading the same record out of sqlite
measures 0.30 ms.

The file also had to carry a full 52 KB snapshot record on every reply, because a previous
change deleted the advisor's database read as "pure waste". That read is the 0.30 ms. It
is back, and the reply carries an id.

Nothing here writes a file. A reply lookup is a primary-key probe, which costs the same on
hour 24 as on minute one.

CONCURRENCY. The recorder is still the only writer of the corpus; the advisor writes only
to `rpc_requests` and reads everything else. WAL allows that -- many readers, one writer at
a time -- provided writes are short and the busy timeout is real. Both hold here: every
write is a single INSERT followed by a commit, and connections are opened with a 30 s busy
timeout. Connections are per-thread because the watchdog asks for a state hash from its own
thread while the main loop is mid-decision.
"""

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common
from decisions import dbopen

DB_NAME = "decisions.sqlite"
RUNS_ROOT = common.RUNS_ROOT
RUN_DIR = common.RUN_DIR

BUSY_TIMEOUT = 30.0


def current_run_dir(runs_root=RUNS_ROOT, timeout=0.0):
    return RUN_DIR


_local = threading.local()


def _con(run_dir):
    key = os.path.abspath(str(run_dir))
    have = getattr(_local, "cons", None)
    if have is None:
        have = _local.cons = {}
    con = have.get(key)
    if con is not None:
        return con
    path = os.path.join(run_dir, DB_NAME)
    if not os.path.exists(path):
        raise RuntimeError(
            "no %s in %s -- the recorder owns this database and creates it. Start the "
            "decisions stream before the advisor." % (DB_NAME, run_dir))
    con = dbopen.connect(path, readonly=False, timeout=BUSY_TIMEOUT)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "rpc_requests" not in names:
        con.close()
        raise RuntimeError(
            "%s has no rpc_requests table -- it predates the database request channel. "
            "The recorder creates it when it opens the store; restart the decisions "
            "stream." % path)
    have[key] = con
    return con


def _store(run_dir):
    """A read-only corpus handle, cached per thread."""
    from decisions.store import DecisionStore
    key = os.path.abspath(str(run_dir))
    have = getattr(_local, "stores", None)
    if have is None:
        have = _local.stores = {}
    st = have.get(key)
    if st is None:
        st = have[key] = DecisionStore(run_dir, readonly=True)
    return st


def close(run_dir=None):
    """Drop this thread's cached handles. Only the process teardown needs this."""
    have = getattr(_local, "cons", None) or {}
    stores = getattr(_local, "stores", None) or {}
    for k in ([os.path.abspath(str(run_dir))] if run_dir else list(have)):
        con = have.pop(k, None)
        if con is not None:
            try:
                con.close()
            except Exception:                                   # noqa: BLE001
                pass
    for k in ([os.path.abspath(str(run_dir))] if run_dir else list(stores)):
        st = stores.pop(k, None)
        if st is not None:
            st.close()


_req_seq = [0]
_seq_lock = threading.Lock()


def _new_id(kind):
    with _seq_lock:
        _req_seq[0] += 1
        seq = _req_seq[0]
    return "%s-%d-%d" % (kind, int(time.time() * 1000), seq)


def _ask(run_dir, kind, payload=None, req_id=None):
    """Put one request on the channel. `req_id` is None for fire-and-forget kinds."""
    con = _con(run_dir)
    con.execute("INSERT INTO rpc_requests(req_id,kind,ts,payload) VALUES(?,?,?,?)",
                (req_id, kind, time.time(),
                 json.dumps(payload or {}, default=str)))
    con.commit()


def respond(run_dir, req_id, **payload):
    """The recorder's answer. `decision_id` and `error` are columns; the rest is payload."""
    con = _con(run_dir)
    did = payload.pop("decision_id", None)
    err = payload.pop("error", None)
    con.execute("INSERT OR REPLACE INTO rpc_responses(req_id,ts,decision_id,payload,error)"
                " VALUES(?,?,?,?,?)",
                (req_id, time.time(), did, json.dumps(payload, default=str), err))
    con.commit()


def read_requests(run_dir, after_id=0):
    """Everything queued since `after_id`, oldest first. Returns (rows, new_after_id)."""
    con = _con(run_dir)
    rows, last = [], after_id
    for rpc_id, req_id, kind, ts, payload in con.execute(
            "SELECT rpc_id,req_id,kind,ts,payload FROM rpc_requests"
            " WHERE rpc_id>? ORDER BY rpc_id", (after_id,)):
        try:
            body = json.loads(payload or "{}")
        except json.JSONDecodeError:
            body = {"malformed": payload}
        body.update(kind=kind, req_id=req_id, ts=ts, rpc_id=rpc_id)
        rows.append(body)
        last = rpc_id
    return rows, last


def last_request_id(run_dir):
    """The head of the queue. The recorder starts here so a restart does not replay a run."""
    try:
        con = _con(run_dir)
    except RuntimeError:
        return 0
    row = con.execute("SELECT COALESCE(MAX(rpc_id),0) FROM rpc_requests").fetchone()
    return row[0] if row else 0


PRUNE_AFTER_S = 900.0


def prune(run_dir, before_id, older_than=PRUNE_AFTER_S):
    """Drop transport rows that have been consumed. Returns (requests, responses) deleted."""
    con = _con(run_dir)
    cutoff = time.time() - float(older_than)
    a = con.execute("DELETE FROM rpc_requests WHERE rpc_id<=? AND ts<?",
                    (before_id, cutoff)).rowcount
    b = con.execute("DELETE FROM rpc_responses WHERE ts<?", (cutoff,)).rowcount
    con.commit()
    return max(0, a), max(0, b)


def _await(run_dir, req_id, timeout, poll=0.02):
    """Block until the recorder answers `req_id`; raises on timeout."""
    con = _con(run_dir)
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = con.execute("SELECT decision_id,payload,error FROM rpc_responses"
                          " WHERE req_id=?", (req_id,)).fetchone()
        if row is not None:
            did, payload, err = row
            if err:
                raise RuntimeError("recorder failed request %s: %s" % (req_id, err))
            try:
                body = json.loads(payload or "{}")
            except json.JSONDecodeError:
                body = {}
            body["decision_id"] = did
            return body
        time.sleep(poll)
    raise RuntimeError("recorder never answered request %s within %ss -- is the decisions "
                       "stream running?" % (req_id, timeout))


def read_decision(run_dir, decision_id):
    """The record the recorder just wrote, read back by id."""
    return _store(run_dir).read_decision(decision_id)



def request_snapshot(run_dir, active=None, timeout=180.0):
    rid = _new_id("snapshot")
    t_request = time.time()
    _ask(run_dir, "snapshot", {"active": active}, req_id=rid)
    reply = _await(run_dir, rid, timeout)
    did = reply.get("decision_id")
    if did is None:
        raise RuntimeError("recorder answered snapshot %s without a decision_id" % rid)
    rec = read_decision(run_dir, did)
    rec["_t_request"] = t_request
    rec["_t_received"] = time.time()
    rec["_collect_ms"] = reply.get("collect_ms")
    rec["_store_ms"] = reply.get("store_ms")
    rec["_pickup_lag_ms"] = reply.get("pickup_lag_ms")
    return did, rec


def request_target(run_dir, timeout=120.0):
    rid = _new_id("target")
    _ask(run_dir, "target", req_id=rid)
    return _await(run_dir, rid, timeout).get("row")


def request_turn(run_dir, timeout=60.0):
    """(turn, campaign_uuid). The recorder has always answered with both -- the campaign"""
    rid = _new_id("turn")
    _ask(run_dir, "turn", req_id=rid)
    r = _await(run_dir, rid, timeout)
    return r.get("turn"), r.get("campaign_uuid")


def request_hash(run_dir, timeout=45.0):
    rid = _new_id("hash")
    _ask(run_dir, "hash", req_id=rid)
    r = _await(run_dir, rid, timeout)
    return r.get("hash"), r.get("roots") or []



def log_interrupt(run_dir, payload):
    body = dict(payload)
    body["screen"] = body.pop("kind", None)
    _ask(run_dir, "interrupt", body)


def log_options(run_dir, decision_id, options):
    """Hand the advisor's surviving options to the recorder, which owns every write."""
    _ask(run_dir, "options", {"decision_id": decision_id, "options": options})


def log_pick(run_dir, decision_id, pick, scores=None, timings=None):
    _ask(run_dir, "pick", {"decision_id": decision_id, "pick": pick,
                           "scores": scores, "timings": timings})


def log_verification(run_dir, decision_id, result):
    _ask(run_dir, "verification", {"decision_id": decision_id, "result": result})


def log_postmortem(run_dir, rec):
    """How a campaign ended. Through the recorder, like every other write."""
    _ask(run_dir, "postmortem", dict(rec or {}))


def log_diplomacy(run_dir, row):
    """A deal event. Goes through the recorder like every other write, so there is still"""
    _ask(run_dir, "diplomacy", dict(row or {}))
