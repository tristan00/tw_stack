from __future__ import annotations


import json
import os
import sqlite3
import time

import common

DB_PATH = os.path.join(common.native(common.METRICS_DIR), "metrics.sqlite")

DDL = """
CREATE TABLE IF NOT EXISTS trials(
  trial TEXT PRIMARY KEY, ts REAL NOT NULL, first_ts REAL,
  snapshots INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_trials_ts ON trials(ts);
"""


def connect(readonly=False, timeout=30.0):
    if readonly:
        if not os.path.exists(DB_PATH):
            return None
        return sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"),
                               uri=True, timeout=timeout)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=timeout)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(DDL)
    con.commit()
    return con


def write_trial(row):
    trial = (row or {}).get("trial")
    if not trial:
        raise RuntimeError("trial row has no `trial` id: %r" % (row,))
    now = time.time()
    con = connect()
    try:
        con.execute("INSERT INTO trials(trial,ts,first_ts,snapshots,payload)"
                    " VALUES(?,?,?,1,?)"
                    " ON CONFLICT(trial) DO UPDATE SET ts=excluded.ts,"
                    " payload=excluded.payload, snapshots=trials.snapshots+1",
                    (str(trial), now, now, json.dumps(row, default=str)))
        con.commit()
    finally:
        con.close()
    return True


def trials():
    con = connect(readonly=True)
    if con is None:
        return []
    try:
        out = []
        for payload, snaps, ts in con.execute(
                "SELECT payload,snapshots,ts FROM trials ORDER BY ts, trial"):
            try:
                d = json.loads(payload)
            except ValueError:
                continue
            d["_snapshots"] = snaps
            d.setdefault("ts", ts)
            out.append(d)
        return out
    finally:
        con.close()


TRIAL_LIVE_WINDOW_S = 1200.0


def live_trials(rows, now=None):
    now = time.time() if now is None else now
    claimed = [r for r in rows if r.get("running")]
    if not claimed:
        return set()
    newest = max(claimed, key=lambda r: float(r.get("ts") or 0))
    if now - float(newest.get("ts") or 0) > TRIAL_LIVE_WINDOW_S:
        return set()
    return {str(newest.get("trial") or "")}


def trial_ids():
    con = connect(readonly=True)
    if con is None:
        return set()
    try:
        return {r[0] for r in con.execute("SELECT trial FROM trials")}
    finally:
        con.close()
