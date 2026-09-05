from __future__ import annotations


import json
import os
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common
from decisions import pg

DB_NAME = common.DECISIONS_DB
RUNS_ROOT = common.RUNS_ROOT
RUN_DIR = common.RUN_DIR

KINDS = ('snapshot', 'decide', 'verification', 'interrupt',
         'diplomacy', 'postmortem', 'ucb_pick')
READ_KINDS = ('turn', 'hash')


def current_run_dir(runs_root=RUNS_ROOT, timeout=0.0):
    return RUN_DIR


_local = threading.local()


def log(msg):
    sys.stderr.write("%.3f  journal %s\n" % (time.time(), msg))


def _con(run_dir, app_name='tw-advisor'):
    con = getattr(_local, "con", None)
    if con is not None:
        return con
    con = pg.connect(app_name=app_name, autocommit=True, search_path=pg.CORPUS_PATH)
    if con.execute("SELECT to_regclass('corpus.rpc_request')").fetchone()[0] is None:
        con.close()
        raise RuntimeError(
            "database %s has no corpus.rpc_request -- apply sql/03_tables.sql with "
            "db-init before starting the advisor." % pg.DB)
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


def _new_id():
    return str(uuid.uuid4())


def _ask(run_dir, kind, payload=None, req_id=None):
    req_id = req_id or _new_id()
    con = _con(run_dir)
    con.execute("INSERT INTO corpus.rpc_request(req_id,kind,ts,payload)"
                " VALUES(%s,%s,%s,%s) ON CONFLICT (req_id) DO NOTHING",
                (req_id, kind, time.time(), json.dumps(payload or {}, default=str)))
    con.execute("SELECT pg_notify('rpc_requests', %s)", (req_id,))
    return req_id


def respond(run_dir, req_id, **payload):
    con = _con(run_dir)
    sid = payload.pop("snapshot_id", None)
    if sid is None:
        sid = payload.pop("decision_id", None)
    err = payload.pop("error", None)
    con.execute("INSERT INTO corpus.rpc_response(req_id,ts,snapshot_id,payload,error)"
                " VALUES(%s,%s,%s,%s,%s) ON CONFLICT (req_id) DO NOTHING",
                (req_id, time.time(), sid, json.dumps(payload, default=str), err))
    con.execute("SELECT pg_notify('rpc_responses', %s)", (req_id,))


def read_requests(run_dir, after_id=0):
    con = _con(run_dir)
    rows, last = [], after_id
    for rpc_id, req_id, kind, ts, payload in con.execute(
            "SELECT rpc_id,req_id,kind,ts,payload FROM corpus.rpc_request"
            " WHERE rpc_id>%s ORDER BY rpc_id", (after_id,)):
        try:
            body = json.loads(payload or "{}")
        except json.JSONDecodeError:
            body = {"malformed": payload}
        body["rpc_kind"] = kind
        body["rpc_ts"] = ts
        body["rpc_id"] = rpc_id
        body["req_id"] = str(req_id)
        rows.append(body)
        last = rpc_id
    return rows, last


def wait_requests(run_dir, timeout):
    con = _con(run_dir)
    for _ in con.notifies(timeout=timeout, stop_after=1):
        pass


def cursor(run_dir):
    con = _con(run_dir)
    row = con.execute(
        "SELECT COALESCE(MIN(r.rpc_id), 0) FROM corpus.rpc_request r"
        " WHERE NOT EXISTS (SELECT 1 FROM corpus.rpc_response s WHERE s.req_id = r.req_id)"
    ).fetchone()
    first_open = row[0] if row else 0
    if first_open:
        return first_open - 1
    row = con.execute("SELECT COALESCE(MAX(rpc_id),0) FROM corpus.rpc_request").fetchone()
    return row[0] if row else 0


def last_request_id(run_dir):
    try:
        return cursor(run_dir)
    except RuntimeError:
        return 0


PRUNE_AFTER_S = 900.0


def prune(run_dir, before_id, older_than=PRUNE_AFTER_S):
    con = _con(run_dir)
    cutoff = time.time() - float(older_than)
    a = con.execute("DELETE FROM corpus.rpc_request WHERE rpc_id<=%s AND ts<%s",
                    (before_id, cutoff)).rowcount
    b = con.execute("DELETE FROM corpus.rpc_response WHERE ts<%s", (cutoff,)).rowcount
    return max(0, a), max(0, b)


def _await(run_dir, req_id, timeout):
    con = _con(run_dir)
    t0 = time.time()
    deadline = t0 + timeout
    while True:
        row = con.execute("SELECT snapshot_id,payload,error FROM corpus.rpc_response"
                          " WHERE req_id=%s", (req_id,)).fetchone()
        if row is not None:
            sid, payload, err = row
            common.waitlog("recorder_rpc", time.time() - t0, not err, req_id)
            if err:
                raise RuntimeError("recorder failed request %s: %s" % (req_id, err))
            try:
                body = json.loads(payload or "{}")
            except json.JSONDecodeError:
                body = {}
            body["decision_id"] = sid
            body["snapshot_id"] = sid
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
    t_request = time.time()
    rid = _ask(run_dir, "snapshot", {"active": active})
    reply = _await(run_dir, rid, timeout)
    did = reply.get("snapshot_id")
    if did is None:
        raise RuntimeError("recorder answered snapshot %s without a snapshot_id" % rid)
    rec = read_decision(run_dir, did)
    rec["_t_request"] = t_request
    rec["_t_received"] = time.time()
    rec["_collect_ms"] = reply.get("collect_ms")
    rec["_store_ms"] = reply.get("store_ms")
    rec["_pickup_lag_ms"] = reply.get("pickup_lag_ms")
    return did, rec


def request_turn(run_dir, timeout=60.0):
    rid = _ask(run_dir, "turn")
    r = _await(run_dir, rid, timeout)
    return r.get("turn"), r.get("campaign_uuid")


def request_hash(run_dir, timeout=45.0):
    rid = _ask(run_dir, "hash")
    r = _await(run_dir, rid, timeout)
    return r.get("hash"), r.get("roots") or []


def log_interrupt(run_dir, payload):
    return _ask(run_dir, "interrupt", dict(payload or {}))


def log_decide(run_dir, decision_id, offers, pick, scores=None, timings=None):
    return _ask(run_dir, "decide", {"decision_id": decision_id, "offers": offers,
                                    "pick": pick, "scores": scores, "timings": timings})


def log_verification(run_dir, decision_id, result):
    return _ask(run_dir, "verification",
                {"decision_id": decision_id, "result": result})


def log_postmortem(run_dir, rec):
    return _ask(run_dir, "postmortem", dict(rec or {}))


def log_ucb_pick(run_dir, rec):
    return _ask(run_dir, "ucb_pick", dict(rec or {}))


def log_diplomacy(run_dir, row):
    return _ask(run_dir, "diplomacy", dict(row or {}))
