r"""store.py -- the v7 decision store (SQLite, one file per run dir).

Schema follows V7_DESIGN.md §5. The unit of training data is a DECISION POINT:
one context snapshot + every action that was OFFERED at that instant + which one was TAKEN and
whether it was CONFIRMED. Positives = the taken+confirmed offer; negatives = the other offers in
the same snapshot; `noop` is a first-class offer and a first-class label ("the model saw N options
and chose to stop"), never an absence of a row.

Rules baked in:
  * `counted` (executed AND confirmed) is the ONLY thing training treats as taken. Executed-but-
    unconfirmed rows are KEPT (they are how we debug silent no-ops) and excluded by the reader.
  * target_rows is keyed (campaign_id, turn) with INSERT OR IGNORE, so the end-of-turn write is
    exactly-once even if the loop restarts mid-campaign.
  * Single writer (the recorder). Readers open read-only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

DB_NAME = "decisions.sqlite"

_DDL = """
CREATE TABLE IF NOT EXISTS target_rows(
  campaign_id TEXT NOT NULL, turn INTEGER NOT NULL, ts REAL,
  income REAL, settlements REAL, allies REAL, vassals REAL, power_rank REAL,
  PRIMARY KEY(campaign_id, turn));

CREATE TABLE IF NOT EXISTS context_snapshots(
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, campaign_id TEXT, turn INTEGER,
  context_kind TEXT NOT NULL, context_id TEXT NOT NULL,
  decision_seq INTEGER NOT NULL DEFAULT 0,
  features TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS action_offers(
  offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL REFERENCES context_snapshots(snapshot_id),
  action_type TEXT NOT NULL, action_key TEXT NOT NULL,
  available INTEGER NOT NULL, gate TEXT, params TEXT);

CREATE TABLE IF NOT EXISTS action_taken(
  taken_id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL REFERENCES context_snapshots(snapshot_id),
  offer_id INTEGER, ts REAL,
  action_type TEXT NOT NULL, action_key TEXT,
  executed INTEGER NOT NULL, confirmed INTEGER NOT NULL, counted INTEGER NOT NULL,
  refusal TEXT, confirm_signal TEXT, confirm_before TEXT, confirm_after TEXT,
  latency_ms INTEGER, policy TEXT);

CREATE INDEX IF NOT EXISTS ix_snap_ctx ON context_snapshots(campaign_id, turn, context_kind, context_id);
CREATE INDEX IF NOT EXISTS ix_offer_snap ON action_offers(snapshot_id);
CREATE INDEX IF NOT EXISTS ix_taken_snap ON action_taken(snapshot_id);
"""


class DecisionStore:
    def __init__(self, run_dir):
        self.path = os.path.join(run_dir, DB_NAME)
        self.con = sqlite3.connect(self.path, timeout=10.0)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(_DDL)
        self.con.commit()

    # ---------------------------------------------------------------- writes
    def write_target_row(self, row):
        """Exactly-once per (campaign, turn). Returns True if this call inserted it."""
        cur = self.con.execute(
            "INSERT OR IGNORE INTO target_rows(campaign_id,turn,ts,income,settlements,allies,vassals,power_rank)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (row.get("campaign_id"), int(row.get("turn") or 0), row.get("ts") or time.time(),
             row.get("income"), row.get("settlements"), row.get("allies"),
             row.get("vassals"), row.get("power_rank")))
        self.con.commit()
        return cur.rowcount > 0

    def write_decision(self, features, offers, taken=None, decision_seq=0, policy=None):
        """One decision point: snapshot + all offers (+ the taken one, if any).

        `taken` is the launcher's ActionRecord (executed/confirmed/counted/refusal/confirm...),
        or None when the loop recorded a pure observation. A `noop` pick is passed like any other
        action -- it is a real label.
        Returns the snapshot_id.
        """
        ts = time.time()
        cur = self.con.execute(
            "INSERT INTO context_snapshots(ts,campaign_id,turn,context_kind,context_id,decision_seq,features)"
            " VALUES(?,?,?,?,?,?,?)",
            (ts, features.get("faction"), int(features.get("turn") or 0),
             features.get("context_kind"), str(features.get("context_id")),
             int(decision_seq), json.dumps(features, default=str)))
        snap = cur.lastrowid
        offer_ids = {}
        for o in offers or []:
            c = self.con.execute(
                "INSERT INTO action_offers(snapshot_id,action_type,action_key,available,gate,params)"
                " VALUES(?,?,?,?,?,?)",
                (snap, o.get("action_type"), str(o.get("key")), 1 if o.get("available") else 0,
                 o.get("gate"), json.dumps(o.get("params") or {}, default=str)))
            offer_ids[(o.get("action_type"), str(o.get("key")))] = c.lastrowid
        if taken is not None:
            conf = taken.get("confirm") or {}
            self.con.execute(
                "INSERT INTO action_taken(snapshot_id,offer_id,ts,action_type,action_key,executed,"
                "confirmed,counted,refusal,confirm_signal,confirm_before,confirm_after,latency_ms,policy)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (snap, offer_ids.get((taken.get("action_type"), str(taken.get("key")))),
                 taken.get("ts") or ts, taken.get("action_type"), str(taken.get("key")),
                 1 if taken.get("executed") else 0, 1 if taken.get("confirmed") else 0,
                 1 if taken.get("counted") else 0, taken.get("refusal"), conf.get("signal"),
                 json.dumps(conf.get("before"), default=str), json.dumps(conf.get("after"), default=str),
                 conf.get("latency_ms"), policy or taken.get("policy")))
        self.con.commit()
        return snap

    # ---------------------------------------------------------------- reads
    def summary(self):
        q = lambda s: self.con.execute(s).fetchone()[0]
        return {"target_rows": q("SELECT COUNT(*) FROM target_rows"),
                "snapshots": q("SELECT COUNT(*) FROM context_snapshots"),
                "offers": q("SELECT COUNT(*) FROM action_offers"),
                "taken": q("SELECT COUNT(*) FROM action_taken"),
                "counted": q("SELECT COUNT(*) FROM action_taken WHERE counted=1"),
                "unconfirmed": q("SELECT COUNT(*) FROM action_taken WHERE executed=1 AND confirmed=0")}

    def training_rows(self):
        """One row per OFFER, labelled y=1 for the taken+confirmed action in that snapshot.

        Snapshots whose taken action failed confirmation are VOIDED (excluded entirely) -- a
        decision point where we do not know what really happened would poison the labels.
        """
        rows = []
        for snap, ck, cid, seq, feats in self.con.execute(
                "SELECT snapshot_id,context_kind,context_id,decision_seq,features FROM context_snapshots"):
            t = self.con.execute(
                "SELECT action_type,action_key,counted FROM action_taken WHERE snapshot_id=?", (snap,)).fetchone()
            if t is None:
                continue                       # observation-only snapshot, no label
            if not t[2]:
                continue                       # voided: executed but unconfirmed
            key = (t[0], t[1])
            for atype, akey, avail, gate, params in self.con.execute(
                    "SELECT action_type,action_key,available,gate,params FROM action_offers WHERE snapshot_id=?",
                    (snap,)):
                rows.append({"_snapshot": snap, "_context_kind": ck, "_context_id": cid,
                             "_decision_seq": seq, "features": json.loads(feats),
                             "action_type": atype, "action_key": akey,
                             "available": bool(avail), "gate": gate,
                             "params": json.loads(params or "{}"),
                             "y": 1 if (atype, akey) == key else 0})
        return rows

    def close(self):
        try:
            self.con.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    import tempfile
    d = tempfile.mkdtemp()
    s = DecisionStore(d)
    print("first target write:", s.write_target_row({"campaign_id": "c", "turn": 1, "income": 100,
                                                     "settlements": 1, "allies": 0, "vassals": 0,
                                                     "power_rank": 144}))
    print("duplicate ignored :", s.write_target_row({"campaign_id": "c", "turn": 1, "income": 999,
                                                     "settlements": 9, "allies": 9, "vassals": 9,
                                                     "power_rank": 9}))
    feats = {"faction": "c", "turn": 1, "context_kind": "lord", "context_id": "56", "income": 100}
    offers = [{"action_type": "stance", "key": "MARCH", "available": True, "params": {}},
              {"action_type": "skills", "key": "sk1", "available": False, "gate": "no_points"},
              {"action_type": "noop", "key": "noop", "available": True}]
    s.write_decision(feats, offers, taken={"action_type": "stance", "key": "MARCH", "executed": True,
                                           "confirmed": True, "counted": True,
                                           "confirm": {"signal": "is_active_flip", "before": {}, "after": {}}})
    s.write_decision(feats, offers, decision_seq=1,
                     taken={"action_type": "skills", "key": "sk1", "executed": True, "confirmed": False,
                            "counted": False, "refusal": "command_silently_refused", "confirm": {}})
    print("summary:", s.summary())
    tr = s.training_rows()
    print("training rows:", len(tr), "positives:", sum(r["y"] for r in tr),
          "(voided snapshot correctly excluded)")
    s.close()
