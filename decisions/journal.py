from __future__ import annotations


import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common
from decisions import pg

DB_NAME = common.DECISIONS_DB
RUNS_ROOT = common.RUNS_ROOT
RUN_DIR = common.RUN_DIR


def current_run_dir(runs_root=RUNS_ROOT, timeout=0.0):
    return RUN_DIR


_local = threading.local()


def _con(run_dir):
    con = getattr(_local, "con", None)
    if con is not None:
        return con
    con = pg.connect(autocommit=True)
    if con.execute("SELECT to_regclass('public.rpc_requests')").fetchone()[0] is None:
        con.close()
        raise RuntimeError(
            "database %s has no rpc_requests table -- the recorder owns the schema and "
            "creates it when it opens the store. Start the decisions stream before the "
            "advisor." % pg.DB)
    con.execute("SET synchronous_commit = off")
    con.execute("LISTEN rpc_requests")
    con.execute("LISTEN rpc_responses")
    _local.con = con
    return con


def _store(run_dir):
    from decisions.store import DecisionStore
    st = getattr(_local, "store", None)
    if st is None:
        st = _local.store = DecisionStore(run_dir, readonly=True)
    return st


def close(run_dir=None):
    con = getattr(_local, "con", None)
    if con is not None:
        _local.con = None
        try:
            con.close()
        except Exception:
            pass
    st = getattr(_local, "store", None)
    if st is not None:
        _local.store = None
        st.close()


_req_seq = [0]
_seq_lock = threading.Lock()


def _new_id(kind):
    with _seq_lock:
        _req_seq[0] += 1
        seq = _req_seq[0]
    return "%s-%d-%d" % (kind, int(time.time() * 1000), seq)


def _ask(run_dir, kind, payload=None, req_id=None):
    con = _con(run_dir)
    con.execute("INSERT INTO rpc_requests(req_id,kind,ts,payload) VALUES(%s,%s,%s,%s)",
                (req_id, kind, time.time(),
                 json.dumps(payload or {}, default=str)))
    con.execute("SELECT pg_notify('rpc_requests', %s)", (req_id or kind,))


def respond(run_dir, req_id, **payload):
    con = _con(run_dir)
    did = payload.pop("decision_id", None)
    err = payload.pop("error", None)
    con.execute("INSERT INTO rpc_responses(req_id,ts,decision_id,payload,error)"
                " VALUES(%s,%s,%s,%s,%s)"
                " ON CONFLICT (req_id) DO UPDATE SET ts=excluded.ts,"
                " decision_id=excluded.decision_id, payload=excluded.payload,"
                " error=excluded.error",
                (req_id, time.time(), did, json.dumps(payload, default=str), err))
    con.execute("SELECT pg_notify('rpc_responses', %s)", (req_id,))


def read_requests(run_dir, after_id=0):
    con = _con(run_dir)
    rows, last = [], after_id
    for rpc_id, req_id, kind, ts, payload in con.execute(
            "SELECT rpc_id,req_id,kind,ts,payload FROM rpc_requests"
            " WHERE rpc_id>%s ORDER BY rpc_id", (after_id,)):
        try:
            body = json.loads(payload or "{}")
        except json.JSONDecodeError:
            body = {"malformed": payload}
        body.update(kind=kind, req_id=req_id, ts=ts, rpc_id=rpc_id)
        rows.append(body)
        last = rpc_id
    return rows, last


def wait_requests(run_dir, timeout):
    con = _con(run_dir)
    for _ in con.notifies(timeout=timeout, stop_after=1):
        pass


def last_request_id(run_dir):
    try:
        con = _con(run_dir)
    except RuntimeError:
        return 0
    row = con.execute("SELECT COALESCE(MAX(rpc_id),0) FROM rpc_requests").fetchone()
    return row[0] if row else 0


PRUNE_AFTER_S = 900.0


def prune(run_dir, before_id, older_than=PRUNE_AFTER_S):
    con = _con(run_dir)
    cutoff = time.time() - float(older_than)
    a = con.execute("DELETE FROM rpc_requests WHERE rpc_id<=%s AND ts<%s",
                    (before_id, cutoff)).rowcount
    b = con.execute("DELETE FROM rpc_responses WHERE ts<%s", (cutoff,)).rowcount
    return max(0, a), max(0, b)


def _await(run_dir, req_id, timeout):
    con = _con(run_dir)
    t0 = time.time()
    deadline = t0 + timeout
    while True:
        row = con.execute("SELECT decision_id,payload,error FROM rpc_responses"
                          " WHERE req_id=%s", (req_id,)).fetchone()
        if row is not None:
            did, payload, err = row
            common.waitlog("recorder_rpc", time.time() - t0, not err, req_id)
            if err:
                raise RuntimeError("recorder failed request %s: %s" % (req_id, err))
            try:
                body = json.loads(payload or "{}")
            except json.JSONDecodeError:
                body = {}
            body["decision_id"] = did
            return body
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        for _ in con.notifies(timeout=min(remaining, 1.0), stop_after=1):
            pass
    common.waitlog("recorder_rpc", time.time() - t0, False, req_id)
    raise RuntimeError("recorder never answered request %s within %ss -- is the decisions "
                       "stream running?" % (req_id, timeout))


def read_decision(run_dir, decision_id):
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


def request_turn(run_dir, timeout=60.0):
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
    _ask(run_dir, "options", {"decision_id": decision_id, "options": options})


def log_pick(run_dir, decision_id, pick, scores=None, timings=None):
    _ask(run_dir, "pick", {"decision_id": decision_id, "pick": pick,
                           "scores": scores, "timings": timings})


def log_verification(run_dir, decision_id, result):
    _ask(run_dir, "verification", {"decision_id": decision_id, "result": result})


def log_postmortem(run_dir, rec):
    _ask(run_dir, "postmortem", dict(rec or {}))


def log_ucb_pick(run_dir, rec):
    _ask(run_dir, "ucb_pick", dict(rec or {}))


def log_diplomacy(run_dir, row):
    _ask(run_dir, "diplomacy", dict(row or {}))
