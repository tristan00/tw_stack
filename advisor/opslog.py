from __future__ import annotations

import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

_SESSION = [None]


def log(msg):
    sys.stderr.write("%.3f  opslog %s\n" % (time.time(), msg))


def _con():
    return pg.connect(app_name="tw-opslog", autocommit=True,
                      search_path=pg.CORPUS_PATH)


def session_id():
    return _SESSION[0]


def reap_sessions(status="killed", con=None):
    t0 = time.time()
    own = con is None
    con = con or _con()
    try:
        n = con.execute(
            "UPDATE ops.session SET status = %s, ended_ts = %s"
            " WHERE host = %s AND status = 'running'",
            (status, time.time(), socket.gethostname())).rowcount
    finally:
        if own:
            con.close()
    log("reap_sessions exit %.0f ms closed=%d" % ((time.time() - t0) * 1000, n))
    return n


def open_session(trial=None, code_version=None):
    t0 = time.time()
    launch_id = os.environ.get("TW_LAUNCH_ID")
    segment_id = os.environ.get("TW_SEGMENT_ID")
    con = _con()
    try:
        reap_sessions(con=con)
        sid = con.execute(
            "INSERT INTO ops.session (launch_id, segment_id, trial, started_ts, status,"
            " host, code_version)"
            " VALUES (%s,%s,%s,%s,'running',%s,%s) RETURNING session_id",
            (int(launch_id) if launch_id else None,
             int(segment_id) if segment_id else None, trial, time.time(),
             socket.gethostname(),
             code_version or os.environ.get("TW_CODE_VERSION"))).fetchone()[0]
    finally:
        con.close()
    _SESSION[0] = sid
    os.environ["TW_SESSION_ID"] = str(sid)
    log("open_session exit %.0f ms session_id=%d" % ((time.time() - t0) * 1000, sid))
    return sid


def beat_session(campaigns=None, turns=None, turns_per_hour=None,
                 last_turn_seconds=None, stalls=None):
    t0 = time.time()
    sid = _SESSION[0]
    if sid is None:
        return None
    con = _con()
    try:
        con.execute(
            "UPDATE ops.session SET campaigns = %s, turns = %s, turns_per_hour = %s,"
            " last_turn_seconds = %s, stalls = %s WHERE session_id = %s",
            (campaigns, turns, turns_per_hour, last_turn_seconds, stalls, sid))
    finally:
        con.close()
    log("beat_session exit %.0f ms session_id=%d campaigns=%s turns=%s"
        % ((time.time() - t0) * 1000, sid, campaigns, turns))
    return sid


def close_session(status, campaigns=None, turns=None, turns_per_hour=None,
                  last_turn_seconds=None, stalls=None):
    t0 = time.time()
    sid = _SESSION[0]
    if sid is None:
        return None
    con = _con()
    try:
        con.execute(
            "UPDATE ops.session SET ended_ts = %s, status = %s,"
            " campaigns = COALESCE(%s, campaigns), turns = COALESCE(%s, turns),"
            " turns_per_hour = COALESCE(%s, turns_per_hour),"
            " last_turn_seconds = COALESCE(%s, last_turn_seconds),"
            " stalls = COALESCE(%s, stalls) WHERE session_id = %s",
            (time.time(), status, campaigns, turns, turns_per_hour,
             last_turn_seconds, stalls, sid))
    finally:
        con.close()
    log("close_session exit %.0f ms session_id=%d status=%s"
        % ((time.time() - t0) * 1000, sid, status))
    return sid


def record_retrain(model, report, campaign_index=None, trial=None):
    t0 = time.time()
    r = report or {}
    con = _con()
    try:
        con.execute(
            "INSERT INTO ops.retrain (trial, session_id, ts, campaign_index, model,"
            " trained, rows, mae, seconds, error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (trial, _SESSION[0], time.time(), campaign_index, model,
             r.get("trained"), r.get("rows"),
             r.get("mae") if r.get("mae") is not None else r.get("mae_in_sample"),
             r.get("seconds"), str(r.get("error"))[:400] if r.get("error") else None))
    finally:
        con.close()
    log("record_retrain exit %.0f ms model=%s trained=%s"
        % ((time.time() - t0) * 1000, model, r.get("trained")))


def record_stall(idle_seconds, turn=None, last_roots=None, recovered=None,
                 campaign_id=None):
    t0 = time.time()
    con = _con()
    try:
        con.execute(
            "INSERT INTO ops.stall (session_id, campaign_id, turn, ts, idle_seconds,"
            " last_roots, recovered) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (_SESSION[0], campaign_id, turn, time.time(), float(idle_seconds),
             [str(x) for x in (last_roots or [])], recovered))
    finally:
        con.close()
    log("record_stall exit %.0f ms idle=%.0fs" % ((time.time() - t0) * 1000, idle_seconds))
