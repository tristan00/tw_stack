from __future__ import annotations


import json
import time

from decisions import pg

DB_PATH = "%s search_path=metrics" % pg.dsn()

DDL = """
CREATE SCHEMA IF NOT EXISTS metrics;
CREATE TABLE IF NOT EXISTS metrics.trials(
  trial TEXT PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, first_ts DOUBLE PRECISION,
  snapshots INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_trials_ts ON metrics.trials(ts);
CREATE TABLE IF NOT EXISTS metrics.trials_archive(
  trial TEXT PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, first_ts DOUBLE PRECISION,
  snapshots INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL,
  archived_ts DOUBLE PRECISION NOT NULL, reason TEXT);
"""


def connect(readonly=False):
    con = pg.connect(autocommit=True, readonly=readonly, search_path="metrics, public")
    if not readonly:
        con.execute(DDL)
    return con


def write_trial(row):
    trial = (row or {}).get("trial")
    if not trial:
        raise RuntimeError("trial row has no `trial` id: %r" % (row,))
    now = time.time()
    con = connect()
    try:
        con.execute("INSERT INTO trials(trial,ts,first_ts,snapshots,payload)"
                    " VALUES(%s,%s,%s,1,%s)"
                    " ON CONFLICT(trial) DO UPDATE SET ts=excluded.ts,"
                    " payload=excluded.payload, snapshots=trials.snapshots+1",
                    (str(trial), now, now, json.dumps(row, default=str)))
    finally:
        con.close()
    return True


def prune_unmatched(have, reason="campaign data gone from the run dir"):
    now = time.time()
    con = connect()
    try:
        gone = []
        for trial, ts, first_ts, snaps, payload in con.execute(
                "SELECT trial,ts,first_ts,snapshots,payload FROM trials").fetchall():
            try:
                uuids = json.loads(payload).get("campaign_uuids") or []
            except ValueError:
                uuids = []
            if not any(u in have for u in uuids):
                gone.append((trial, ts, first_ts, snaps, payload, now, reason))
        if gone:
            with con.cursor() as cur:
                cur.executemany(
                    "INSERT INTO trials_archive"
                    "(trial,ts,first_ts,snapshots,payload,archived_ts,reason)"
                    " VALUES(%s,%s,%s,%s,%s,%s,%s)"
                    " ON CONFLICT(trial) DO UPDATE SET ts=excluded.ts,"
                    " first_ts=excluded.first_ts, snapshots=excluded.snapshots,"
                    " payload=excluded.payload, archived_ts=excluded.archived_ts,"
                    " reason=excluded.reason", gone)
                cur.executemany("DELETE FROM trials WHERE trial=%s",
                                [(g[0],) for g in gone])
        return [g[0] for g in gone]
    finally:
        con.close()


def trials():
    try:
        con = connect(readonly=True)
    except Exception:
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
    except Exception:
        return []
    finally:
        con.close()


def trial_ids():
    try:
        con = connect(readonly=True)
    except Exception:
        return set()
    try:
        return {r[0] for r in con.execute("SELECT trial FROM trials")}
    except Exception:
        return set()
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
