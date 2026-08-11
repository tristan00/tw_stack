from __future__ import annotations

"""Replay real decisions from a v1 corpus into a v2 store and measure the difference.

The v2 layout is justified by numbers, so the numbers should be reproducible rather than
quoted. This reads N decisions out of a v1 database (the archive), writes them through
DecisionStore, and reports bytes per decision both ways plus where the space went.

    python -m decisions.bench_store <v1_db> [--n 2000]

It writes into a temporary directory and removes it, so it touches nothing.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from decisions.store import DecisionStore     # noqa: E402


def _read_v1(db, n, tail=False):
    """A window of N decisions. `tail` takes the newest, which is the only part of the
    corpus that reflects the mature collector -- the first 2,000 decisions carry 250
    offers each against a corpus average of 407, so the head understates the row count
    the layout has to survive."""
    con = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"), uri=True,
                          timeout=120)
    dids = [r[0] for r in con.execute(
        "SELECT decision_id FROM decision_points ORDER BY decision_id %s LIMIT ?"
        % ("DESC" if tail else "ASC"), (n,))]
    dids.sort()
    if not dids:
        return con, [], 0, 0
    lo, hi = dids[0], dids[-1]
    ents = {}
    for sid, did, ck, cid, feats in con.execute(
            "SELECT snapshot_id,decision_id,context_kind,context_id,features"
            " FROM entity_snapshots WHERE decision_id BETWEEN ? AND ?", (lo, hi)):
        ents.setdefault(did, []).append(
            {"snapshot_id": sid, "context_kind": ck, "context_id": cid,
             "state": json.loads(feats or "{}"), "offers": []})
    by_snap = {e["snapshot_id"]: e for v in ents.values() for e in v}
    for sid, atype, akey, avail, gate, params in con.execute(
            "SELECT snapshot_id,action_type,action_key,available,gate,params"
            " FROM action_offers WHERE decision_id BETWEEN ? AND ?", (lo, hi)):
        e = by_snap.get(sid)
        if e is not None:
            e["offers"].append({"action_type": atype, "key": akey,
                                "available": bool(avail), "gate": gate,
                                "params": json.loads(params or "{}")})
    out = []
    for did, ts, camp_id, turn, camp, world in con.execute(
            "SELECT decision_id,ts,campaign_id,turn,campaign,world FROM decision_points"
            " WHERE decision_id BETWEEN ? AND ? ORDER BY decision_id", (lo, hi)):
        c = json.loads(camp or "{}")
        c.setdefault("campaign_uuid", camp_id)
        c.setdefault("turn", turn)
        out.append({"ts": ts, "campaign": c, "world": json.loads(world or "{}"),
                    "entities": ents.get(did, [])})
    return con, out, lo, hi


_V1_DDL = """
CREATE TABLE decision_points(
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, campaign_id TEXT, turn INTEGER,
  decision_seq INTEGER NOT NULL DEFAULT 0, policy TEXT,
  n_entities INTEGER, n_offers INTEGER, campaign TEXT, world TEXT, timings TEXT);
CREATE TABLE entity_snapshots(
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER NOT NULL,
  context_kind TEXT NOT NULL, context_id TEXT NOT NULL, features TEXT NOT NULL);
CREATE TABLE action_offers(
  offer_id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER NOT NULL,
  snapshot_id INTEGER NOT NULL, context_kind TEXT NOT NULL, context_id TEXT NOT NULL,
  action_type TEXT NOT NULL, action_key TEXT NOT NULL,
  available INTEGER NOT NULL, gate TEXT, params TEXT,
  score REAL, exploit REAL, rank INTEGER, pct_global REAL, pct_local REAL,
  gnn_impact REAL, gnn_rank INTEGER);
CREATE INDEX ix_dp ON decision_points(campaign_id, turn);
CREATE INDEX ix_snap_dp ON entity_snapshots(decision_id);
CREATE INDEX ix_offer_dp ON action_offers(decision_id);
CREATE INDEX ix_offer_key ON action_offers(
  decision_id, context_kind, context_id, action_type, action_key);
"""


def _write_v1(path, snaps):
    """The v1 write path, so 'smaller and faster' is a comparison and not a claim."""
    con = sqlite3.connect(path, timeout=30.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_V1_DDL)
    con.commit()
    t0 = time.time()
    for s in snaps:
        camp = s.get("campaign") or {}
        ents = s.get("entities") or []
        n_offers = sum(len(e.get("offers") or []) for e in ents)
        cur = con.execute(
            "INSERT INTO decision_points(ts,campaign_id,turn,decision_seq,policy,"
            "n_entities,n_offers,campaign,world) VALUES(?,?,?,?,?,?,?,?,?)",
            (s.get("ts"), camp.get("campaign_uuid"), int(camp.get("turn") or 0), 0, None,
             len(ents), n_offers, json.dumps(camp, default=str),
             json.dumps(s.get("world") or {}, default=str)))
        did = cur.lastrowid
        for e in ents:
            ck, cid = e.get("context_kind"), str(e.get("context_id"))
            c = con.execute(
                "INSERT INTO entity_snapshots(decision_id,context_kind,context_id,features)"
                " VALUES(?,?,?,?)", (did, ck, cid, json.dumps(e.get("state") or {},
                                                             default=str)))
            snap = c.lastrowid
            for o in e.get("offers") or []:
                con.execute(
                    "INSERT INTO action_offers(decision_id,snapshot_id,context_kind,"
                    "context_id,action_type,action_key,available,gate,params)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (did, snap, ck, cid, o.get("action_type"), str(o.get("key")),
                     1 if o.get("available") else 0, o.get("gate"),
                     json.dumps(o.get("params") or {}, default=str)))
        con.commit()
    wall = time.time() - t0
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.commit()
    con.close()
    return wall, os.path.getsize(path)


def main(argv):
    db = next((a for a in argv if not a.startswith("--")), None)
    if not db or not os.path.exists(db):
        print("usage: python -m decisions.bench_store <v1_db> [--n 2000]")
        return 2
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 2000

    t0 = time.time()
    con, snaps, w0, w1 = _read_v1(db, n, tail="--tail" in argv)
    print("read %d v1 decisions in %.1fs" % (len(snaps), time.time() - t0))
    if not snaps:
        return 1
    n_offers = sum(len(e["offers"]) for s in snaps for e in s["entities"])

    # What those same decisions occupy in v1, measured rather than assumed.
    v1_bytes = con.execute(
        "SELECT SUM(LENGTH(COALESCE(campaign,''))+LENGTH(COALESCE(world,'')))"
        " FROM decision_points WHERE decision_id BETWEEN ? AND ?", (w0, w1)).fetchone()[0] or 0
    v1_bytes += con.execute(
        "SELECT SUM(LENGTH(COALESCE(features,''))) FROM entity_snapshots"
        " WHERE decision_id BETWEEN ? AND ?", (w0, w1)).fetchone()[0] or 0
    v1_bytes += con.execute(
        "SELECT SUM(LENGTH(COALESCE(params,''))+LENGTH(action_key)+LENGTH(action_type)"
        "+LENGTH(context_id)+LENGTH(context_kind)) FROM action_offers"
        " WHERE decision_id BETWEEN ? AND ?", (w0, w1)).fetchone()[0] or 0
    con.close()

    d = tempfile.mkdtemp(prefix="benchstore_")
    try:
        run = os.path.join(d, "run")
        os.makedirs(run)
        st = DecisionStore(run)
        st.register_collector("bench")
        t0 = time.time()
        for s in snaps:
            st.write_decision(s)
        wall = time.time() - t0
        st.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        st.con.commit()
        size = os.path.getsize(st.path)
        n_actions = st.con.execute("SELECT count(*) FROM actions").fetchone()[0]
        n_blobs, blob_raw, blob_stored = st.con.execute(
            "SELECT count(*), SUM(n), SUM(length(z)) FROM blobs").fetchone()
        viol = st.layout_violations()
        st.close()

        v1_wall, v1_size = _write_v1(os.path.join(d, "v1.sqlite"), snaps)

        per = size / float(len(snaps))
        v1_per = v1_size / float(len(snaps))
        print("")
        print("decisions            : %d  (%d offers, %.0f per decision)"
              % (len(snaps), n_offers, n_offers / float(len(snaps))))
        print("v1 payload bytes     : %.1f MB of text for the same rows" % (v1_bytes / 1e6))
        print("v1 file on disk      : %.1f MB  (%.0f B per decision), %.2f ms per write"
              % (v1_size / 1e6, v1_per, 1000.0 * v1_wall / len(snaps)))
        print("v2 file on disk      : %.1f MB  (%.0f B per decision)" % (size / 1e6, per))
        print("  actions interned   : %d distinct of %d offers  (%.1fx)"
              % (n_actions, n_offers, n_offers / float(max(n_actions, 1))))
        print("  blobs              : %d rows, %.1f MB text -> %.1f MB stored (%.1fx)"
              % (n_blobs, blob_raw / 1e6, blob_stored / 1e6,
                 blob_raw / float(max(blob_stored, 1))))
        print("write_decision       : %.2f ms each" % (1000.0 * wall / len(snaps)))
        print("layout violations    : %d" % viol)
        print("")
        print("v2 vs v1             : %.1fx smaller, %.2fx write time"
              % (v1_size / float(max(size, 1)), wall / max(v1_wall, 1e-9)))
        print("projected at 250,000 : %.1f GB  (v1: %.1f GB)"
              % (per * 250000 / 1e9, v1_per * 250000 / 1e9))
        return 0 if viol == 0 else 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
