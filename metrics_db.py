from __future__ import annotations

"""The training trial ledger.

One row per trial, keyed by trial id. This was `metrics/experiments.jsonl`: an append-only
text file that three separate readers re-parsed from the top, and whose semantics were
already "last row for a trial id wins" -- because a live trial is checkpointed repeatedly
as the session runs, and a backfill rewrites rows it has seen before. That is an upsert
spelled as an append, and a table says it directly.

It is NOT in decisions.sqlite. That database is one run directory's corpus; this ledger
spans every run directory, so filing it there would scope it wrong. It lives beside the
metrics it describes, which is what `common.METRICS_DIR` is for.

Readers get whole rows back, because the row is a trial report whose shape belongs to
session.py, not here. This module owns storage and identity, nothing else.
"""

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
    """Open the ledger. Creates it on first write; a reader of a missing file gets nothing"""
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
    """Upsert one trial. Raises rather than returning quietly: an unrecorded experiment is"""
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
    """Every trial, newest last. `_snapshots` rides on the row so a reader can still say"""
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


# A running trial rewrites its row when a campaign starts and again when it ends, so a live
# one is never quiet for longer than a campaign. Deliberately looser than one campaign.
TRIAL_LIVE_WINDOW_S = 1200.0


def live_trials(rows, now=None):
    """The ids in `rows` that a session is writing right now, as a set.

    `running` is written per campaign and cannot be cleared by a session that was killed, so
    it is a claim rather than a fact: every killed session leaves a row asserting it forever.
    The claim holds only for the newest such row, and only while it is still being rewritten.
    """
    now = time.time() if now is None else now
    claimed = [r for r in rows if r.get("running")]
    if not claimed:
        return set()
    newest = max(claimed, key=lambda r: float(r.get("ts") or 0))
    if now - float(newest.get("ts") or 0) > TRIAL_LIVE_WINDOW_S:
        return set()
    return {str(newest.get("trial") or "")}


def trial_ids():
    """The ids already banked, for a backfill that must not write one twice."""
    con = connect(readonly=True)
    if con is None:
        return set()
    try:
        return {r[0] for r in con.execute("SELECT trial FROM trials")}
    finally:
        con.close()
