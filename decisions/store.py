"""The decision store: SQLite, one file per run dir. Single writer (the recorder)."""
from __future__ import annotations

import json
import os
import sqlite3
import time

DB_NAME = "decisions.sqlite"


class IncompatibleStore(RuntimeError):
    """This decisions.sqlite predates the current schema and cannot be read."""

_DDL = """
CREATE TABLE IF NOT EXISTS interrupt_decisions(
  interrupt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, campaign_id TEXT, turn INTEGER,
  kind TEXT NOT NULL, root TEXT, root_context TEXT,
  n_options INTEGER, options_json TEXT,
  chosen TEXT, chosen_context TEXT,
  campaign_json TEXT,
  executed INTEGER, confirmed INTEGER, counted INTEGER, refusal TEXT, latency_ms INTEGER,
  tree_json TEXT);

CREATE TABLE IF NOT EXISTS entity_target_rows(
  campaign_id TEXT NOT NULL, turn INTEGER NOT NULL,
  context_kind TEXT NOT NULL, context_id TEXT NOT NULL,
  value REAL, ts REAL,
  PRIMARY KEY(campaign_id, turn, context_kind, context_id));

CREATE TABLE IF NOT EXISTS target_rows(
  campaign_id TEXT NOT NULL, turn INTEGER NOT NULL, ts REAL,
  income REAL, settlements REAL, allies REAL, vassals REAL, power_rank REAL, lord_level REAL,
  PRIMARY KEY(campaign_id, turn));

CREATE TABLE IF NOT EXISTS decision_points(
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, campaign_id TEXT, turn INTEGER,
  decision_seq INTEGER NOT NULL DEFAULT 0, policy TEXT,
  n_entities INTEGER, n_offers INTEGER,
  campaign TEXT, world TEXT, timings TEXT);

CREATE TABLE IF NOT EXISTS entity_snapshots(
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL REFERENCES decision_points(decision_id),
  context_kind TEXT NOT NULL, context_id TEXT NOT NULL,
  features TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS action_offers(
  offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL REFERENCES decision_points(decision_id),
  snapshot_id INTEGER NOT NULL REFERENCES entity_snapshots(snapshot_id),
  context_kind TEXT NOT NULL, context_id TEXT NOT NULL,
  action_type TEXT NOT NULL, action_key TEXT NOT NULL,
  available INTEGER NOT NULL, gate TEXT, params TEXT,
  score REAL, exploit REAL, explore REAL, rank INTEGER);

CREATE TABLE IF NOT EXISTS action_taken(
  taken_id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL REFERENCES decision_points(decision_id),
  snapshot_id INTEGER, offer_id INTEGER, ts REAL,
  context_kind TEXT, context_id TEXT,
  action_type TEXT NOT NULL, action_key TEXT,
  executed INTEGER NOT NULL, confirmed INTEGER NOT NULL, counted INTEGER NOT NULL,
  refusal TEXT, confirm_signal TEXT, confirm_before TEXT, confirm_after TEXT,
  latency_ms INTEGER, policy TEXT, timing TEXT);

CREATE INDEX IF NOT EXISTS ix_dp ON decision_points(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_snap_dp ON entity_snapshots(decision_id);
CREATE INDEX IF NOT EXISTS ix_offer_dp ON action_offers(decision_id);
CREATE INDEX IF NOT EXISTS ix_taken_dp ON action_taken(decision_id);
"""


class DecisionStore:
    # columns added after a DB may already exist; CREATE TABLE IF NOT EXISTS does not add them
    _MIGRATIONS = (("decision_points", "campaign", "TEXT"),
                   ("decision_points", "world", "TEXT"),
                   ("decision_points", "timings", "TEXT"),
                   ("action_taken", "timing", "TEXT"),
                   ("interrupt_decisions", "executed", "INTEGER"),
                   ("interrupt_decisions", "confirmed", "INTEGER"),
                   ("interrupt_decisions", "counted", "INTEGER"),
                   ("interrupt_decisions", "refusal", "TEXT"),
                   ("interrupt_decisions", "latency_ms", "INTEGER"),
                   ("interrupt_decisions", "tree_json", "TEXT"),
                   ("interrupt_decisions", "policy", "TEXT"),
                   ("interrupt_decisions", "world_json", "TEXT"),
                   ("interrupt_decisions", "panel_json", "TEXT"),
                   ("action_offers", "score", "REAL"),
                   ("action_offers", "exploit", "REAL"),
                   ("action_offers", "explore", "REAL"),
                   ("action_offers", "rank", "INTEGER"),
                   ("action_offers", "pct_global", "REAL"),
                   ("action_offers", "pct_local", "REAL"))

    _REQUIRED = (("action_offers", "decision_id"), ("entity_snapshots", "decision_id"))

    def _assert_compatible(self):
        for table, col in self._REQUIRED:
            exists = self.con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                continue
            cols = {r[1] for r in self.con.execute("PRAGMA table_info(%s)" % table)}
            if col not in cols:
                raise IncompatibleStore(
                    "%s predates the faction-wide decision-point schema (%s.%s missing). Its rows "
                    "have a different shape and cannot be read by the current queries."
                    % (self.path, table, col))

    def __init__(self, run_dir):
        self.run_id = os.path.basename(str(run_dir).rstrip("/\\"))
        self.path = os.path.join(run_dir, DB_NAME)
        self.con = sqlite3.connect(self.path, timeout=10.0)
        self.con.execute("PRAGMA journal_mode=WAL")
        self._assert_compatible()
        self.con.executescript(_DDL)
        self._migrate()
        self.con.commit()

    def _migrate(self):
        for table, col, typ in self._MIGRATIONS:
            cols = {r[1] for r in self.con.execute("PRAGMA table_info(%s)" % table)}
            if col not in cols:
                self.con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, typ))

    def campaign_key(self, faction, uuid=None):
        """The campaign uuid if there is one, else faction@rundir."""
        return str(uuid) if uuid else "%s@%s" % (faction, self.run_id)

    def write_target_row(self, row):
        """Exactly-once per (campaign, turn). Returns True if this call inserted it."""
        cur = self.con.execute(
            "INSERT OR IGNORE INTO target_rows"
            "(campaign_id,turn,ts,income,settlements,allies,vassals,power_rank,lord_level)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (self.campaign_key(row.get("campaign_id"), row.get("campaign_uuid")),
             int(row.get("turn") or 0), row.get("ts") or time.time(),
             row.get("income"), row.get("settlements"), row.get("allies"),
             row.get("vassals"), row.get("power_rank"), row.get("lord_level")))
        self.con.commit()
        return cur.rowcount > 0

    def write_decision(self, snapshot, decision_seq=0, policy=None):
        """Persist one collect.snapshot() as a decision point; returns the decision_id."""
        camp = snapshot.get("campaign") or {}
        ents = snapshot.get("entities") or []
        n_offers = sum(len(e.get("offers") or []) for e in ents)
        cur = self.con.execute(
            "INSERT INTO decision_points(ts,campaign_id,turn,decision_seq,policy,n_entities,"
            "n_offers,campaign,world) VALUES(?,?,?,?,?,?,?,?,?)",
            (snapshot.get("ts") or time.time(),
             self.campaign_key(camp.get("faction"), camp.get("campaign_uuid")),
             int(camp.get("turn") or 0),
             int(decision_seq), policy, len(ents), n_offers,
             json.dumps(camp, default=str), json.dumps(snapshot.get("world") or {}, default=str)))
        did = cur.lastrowid
        for e in ents:
            ck, cid = e.get("context_kind"), str(e.get("context_id"))
            c = self.con.execute(
                "INSERT INTO entity_snapshots(decision_id,context_kind,context_id,features)"
                " VALUES(?,?,?,?)", (did, ck, cid, json.dumps(e.get("state") or {}, default=str)))
            snap = c.lastrowid
            for o in e.get("offers") or []:
                self.con.execute(
                    "INSERT INTO action_offers(decision_id,snapshot_id,context_kind,context_id,"
                    "action_type,action_key,available,gate,params) VALUES(?,?,?,?,?,?,?,?,?)",
                    (did, snap, ck, cid, o.get("action_type"), str(o.get("key")),
                     1 if o.get("available") else 0, o.get("gate"),
                     json.dumps(o.get("params") or {}, default=str)))
        self.con.commit()
        return did

    def attach_timings(self, decision_id, timings):
        """Millisecond phase timings for one decision."""
        if not timings:
            return 0
        self.con.execute("UPDATE decision_points SET timings=? WHERE decision_id=?",
                         (json.dumps(timings, default=str), decision_id))
        self.con.commit()
        return 1

    def attach_scores(self, decision_id, scores):
        """Attach per-offer scores to one decision; returns the number of offer rows updated."""
        n = 0
        for s in scores or []:
            n += self.con.execute(
                "UPDATE action_offers SET score=?,exploit=?,explore=?,rank=?,"
                "pct_global=?,pct_local=? WHERE decision_id=? "
                "AND context_kind=? AND context_id=? AND action_type=? AND action_key=?",
                (s.get("score"), s.get("exploit"), s.get("explore"), s.get("rank"),
                 s.get("pct_global"), s.get("pct_local"), decision_id,
                 s.get("context_kind"), str(s.get("context_id")), s.get("action_type"),
                 str(s.get("key")))).rowcount
        self.con.commit()
        return n

    def attach_taken(self, decision_id, taken, policy=None):
        """The chosen offer and its verification. A second call for the same decision replaces the row."""
        ck, cid = taken.get("context_kind"), str(taken.get("context_id"))
        atype, akey = taken.get("action_type"), str(taken.get("key"))
        row = self.con.execute(
            "SELECT offer_id,snapshot_id FROM action_offers WHERE decision_id=? AND context_kind=?"
            " AND context_id=? AND action_type=? AND action_key=?",
            (decision_id, ck, cid, atype, akey)).fetchone()
        offer_id, snap_id = (row if row else (None, None))
        conf = taken.get("confirm") or {}
        self.con.execute("DELETE FROM action_taken WHERE decision_id=?", (decision_id,))
        self.con.execute(
            "INSERT INTO action_taken(decision_id,snapshot_id,offer_id,ts,context_kind,context_id,"
            "action_type,action_key,executed,confirmed,counted,refusal,confirm_signal,"
            "confirm_before,confirm_after,latency_ms,policy)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (decision_id, snap_id, offer_id, taken.get("ts") or time.time(), ck, cid, atype, akey,
             1 if taken.get("executed") else 0, 1 if taken.get("confirmed") else 0,
             1 if taken.get("counted") else 0, taken.get("refusal"), conf.get("signal"),
             json.dumps(conf.get("before"), default=str),
             json.dumps(conf.get("after"), default=str),
             conf.get("latency_ms"), policy or taken.get("policy")))
        self.con.commit()
        return offer_id is not None

    def summary(self):
        q = lambda s: self.con.execute(s).fetchone()[0]
        return {"target_rows": q("SELECT COUNT(*) FROM target_rows"),
                "decisions": q("SELECT COUNT(*) FROM decision_points"),
                "snapshots": q("SELECT COUNT(*) FROM entity_snapshots"),
                "offers": q("SELECT COUNT(*) FROM action_offers"),
                "taken": q("SELECT COUNT(*) FROM action_taken"),
                "counted": q("SELECT COUNT(*) FROM action_taken WHERE counted=1"),
                "unconfirmed": q("SELECT COUNT(*) FROM action_taken WHERE executed=1 AND confirmed=0")}

    def read_decision(self, decision_id):
        """One stored decision point in the shape the advisor featurizes."""
        dp = self.con.execute("SELECT * FROM decision_points WHERE decision_id=?",
                              (decision_id,)).fetchone()
        if dp is None:
            raise KeyError("decision %s not in the store" % decision_id)
        cols = [d[0] for d in self.con.execute(
            "SELECT * FROM decision_points WHERE decision_id=?", (decision_id,)).description]
        dp = dict(zip(cols, dp))
        ents, by_snap = [], {}
        for sid, ck, cid, feats in self.con.execute(
                "SELECT snapshot_id,context_kind,context_id,features FROM entity_snapshots"
                " WHERE decision_id=?", (decision_id,)):
            e = {"snapshot_id": sid, "context_kind": ck, "context_id": cid,
                 "state": json.loads(feats), "offers": []}
            by_snap[sid] = e
            ents.append(e)
        for sid, oid, atype, akey, avail, gate, params in self.con.execute(
                "SELECT snapshot_id,offer_id,action_type,action_key,available,gate,params"
                " FROM action_offers WHERE decision_id=?", (decision_id,)):
            e = by_snap.get(sid)
            if e is not None:
                e["offers"].append({"offer_id": oid, "action_type": atype, "key": akey,
                                    "available": bool(avail), "gate": gate,
                                    "params": json.loads(params or "{}")})
        return {"decision_id": decision_id, "turn": dp["turn"], "campaign_id": dp["campaign_id"],
                "campaign": json.loads(dp["campaign"] or "{}"),
                "world": json.loads(dp["world"] or "{}"), "entities": ents}

    def labelled_decisions(self, confirmed_only=False):
        """Every decision point with a chosen action: (record, taken_key, counted)."""
        out = []
        for (did,) in self.con.execute("SELECT decision_id FROM decision_points").fetchall():
            t = self.con.execute(
                "SELECT context_kind,context_id,action_type,action_key,counted,refusal"
                " FROM action_taken WHERE decision_id=?", (did,)).fetchone()
            if t is None:
                continue
            if t[5] == "awaiting_execution":
                continue
            if confirmed_only and not t[4]:
                continue
            out.append((self.read_decision(did), (t[0], str(t[1]), t[2], str(t[3])), bool(t[4])))
        return out

    @staticmethod
    def _flag(v):
        """None stays None (unknown); anything else becomes a hard 0/1."""
        return None if v is None else (1 if v else 0)

    def write_interrupt(self, row):
        """One interrupt-screen decision."""
        camp = row.get("campaign") or {}
        opts = row.get("options") or {}
        counted = row.get("counted")
        if counted is None and row.get("confirmed") is not None:
            counted = bool(row.get("executed")) and bool(row.get("confirmed"))
        row = dict(row, counted=counted)
        self.con.execute(
            "INSERT INTO interrupt_decisions(ts,campaign_id,turn,kind,root,root_context,"
            "n_options,options_json,chosen,chosen_context,executed,confirmed,counted,refusal,"
            "latency_ms,campaign_json,tree_json,policy,world_json,panel_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row.get("ts") or time.time(),
             self.campaign_key(camp.get("faction"), camp.get("campaign_uuid")),
             int(camp.get("turn") or 0),
             row.get("screen"), row.get("root"), row.get("root_context"),
             len(opts), json.dumps(opts, default=str),
             row.get("chosen"), row.get("chosen_context"),
             self._flag(row.get("executed")), self._flag(row.get("confirmed")),
             self._flag(row.get("counted")),
             row.get("refusal"), row.get("latency_ms"),
             json.dumps(camp, default=str),
             json.dumps(row["tree"], default=str) if row.get("tree") else None,
             row.get("policy"),
             json.dumps(row.get("world") or {}, default=str) if row.get("world") else None,
             json.dumps(row.get("panel") or {}, default=str) if row.get("panel") else None))
        self.con.commit()

    def interrupt_rows(self):
        """Every recorded interrupt-screen decision."""
        out = []
        for (iid, ts, camp, turn, kind, opts, chosen, executed, confirmed, counted, refusal,
             cjson, wjson, pjson) in self.con.execute(
                "SELECT interrupt_id,ts,campaign_id,turn,kind,options_json,chosen,"
                "executed,confirmed,counted,refusal,campaign_json,world_json,panel_json"
                " FROM interrupt_decisions ORDER BY interrupt_id"):
            try:
                options = json.loads(opts) if opts else {}
            except Exception:
                options = {}
            try:
                campaign = json.loads(cjson) if cjson else {}
            except Exception:
                campaign = {}
            out.append({"interrupt_id": iid, "ts": ts, "campaign_id": camp, "turn": turn,
                        "screen": kind, "options": options, "chosen": chosen,
                        "executed": executed, "confirmed": confirmed, "counted": counted,
                        "refusal": refusal, "campaign": campaign,
                        "world": (json.loads(wjson) if wjson else {}),
                        "panel": (json.loads(pjson) if pjson else {})})
        return out

    def target_series(self):
        """{campaign_id: {turn: {income, settlements, power_rank, allies, vassals, lord_level}}}."""
        out = {}
        for camp, turn, inc, setl, allies, vass, rank, lvl in self.con.execute(
                "SELECT campaign_id,turn,income,settlements,allies,vassals,power_rank,lord_level"
                " FROM target_rows"):
            out.setdefault(camp, {})[int(turn)] = {
                "income": inc or 0.0, "settlements": setl or 0.0,
                # inverted: lower rank is stronger
                "power_rank": (-rank if rank is not None else -50.0),
                "allies": allies or 0.0, "vassals": vass or 0.0,
                "lord_level": lvl or 0.0}
        return out

    def write_entity_target_rows(self, campaign_id, turn, rows):
        """[{context_kind, context_id, value}] for one turn. Exactly-once per key."""
        n = 0
        for r in rows or []:
            if r.get("value") is None:
                continue
            cur = self.con.execute(
                "INSERT OR IGNORE INTO entity_target_rows"
                "(campaign_id,turn,context_kind,context_id,value,ts) VALUES(?,?,?,?,?,?)",
                (campaign_id, int(turn or 0), r["context_kind"], str(r["context_id"]),
                 float(r["value"]), time.time()))
            n += cur.rowcount or 0
        self.con.commit()
        return n

    def entity_series(self):
        """{campaign_id: {(context_kind, context_id): {turn: value}}}."""
        out = {}
        for camp, turn, kind, cid, val in self.con.execute(
                "SELECT campaign_id,turn,context_kind,context_id,value FROM entity_target_rows"):
            if val is None:
                continue
            out.setdefault(camp, {}).setdefault((kind, str(cid)), {})[int(turn)] = float(val)
        return out

    def close(self):
        try:
            self.con.close()
        except Exception:
            pass


if __name__ == "__main__":
    import tempfile
    d = tempfile.mkdtemp()
    s = DecisionStore(d)
    print("first target write:", s.write_target_row({"campaign_id": "c", "turn": 1, "income": 100,
                                                     "settlements": 1, "allies": 0, "vassals": 0,
                                                     "power_rank": 144}))
    print("duplicate ignored :", s.write_target_row({"campaign_id": "c", "turn": 1, "income": 999,
                                                     "settlements": 9, "allies": 9, "vassals": 9,
                                                     "power_rank": 9}))
    snap = {"ts": time.time(), "campaign": {"faction": "c", "turn": 1, "treasury": 2000},
            "world": {"armies": [{"cqi": 56, "x": 10, "y": 10}], "settlements": [], "hostiles": []},
            "entities": [
                {"context_kind": "lord", "context_id": "56", "state": {"units": 5, "ap_pct": 100},
                 "offers": [{"action_type": "stance", "key": "MARCH", "available": True},
                            {"action_type": "attack_army", "key": "cqi:99", "available": True,
                             "params": {"target_cqi": 99}},
                            {"action_type": "noop", "key": "noop", "available": True}]},
                {"context_kind": "province", "context_id": "reg_a", "state": {"free_slots": 2},
                 "offers": [{"action_type": "building", "key": "b1", "available": True},
                            {"action_type": "noop", "key": "noop", "available": True}]},
                {"context_kind": "campaign", "context_id": "c", "state": {"treasury": 2000},
                 "offers": [{"action_type": "end_turn", "key": "end_turn", "available": True}]}]}
    d1 = s.write_decision(snap)
    print("attach_scores:", s.attach_scores(d1, [
        {"context_kind": "lord", "context_id": "56", "action_type": "attack_army",
         "key": "cqi:99", "score": 0.9, "exploit": 0.8, "explore": 0.4, "rank": 1}]), "offers scored")
    s.attach_taken(d1, {"context_kind": "lord", "context_id": "56", "action_type": "attack_army",
                        "key": "cqi:99", "executed": True, "confirmed": True, "counted": True,
                        "confirm": {"signal": "pre_battle_popup"}})
    d2 = s.write_decision(snap, decision_seq=1)
    s.attach_taken(d2, {"context_kind": "province", "context_id": "reg_a", "action_type": "building",
                        "key": "b1", "executed": True, "confirmed": False, "counted": False,
                        "refusal": "command_silently_refused", "confirm": {}})
    print("summary:", s.summary())
    lab = s.labelled_decisions()
    print("labelled decisions:", len(lab), "(the unconfirmed one is VOIDED -> 1, not 2)")
    for rec, taken in lab:
        print("   decision %s turn %s taken=%s  entities=%d offers=%d"
              % (rec["decision_id"], rec["turn"], taken, len(rec["entities"]),
                 sum(len(e["offers"]) for e in rec["entities"])))
    print("target series:", s.target_series())
    s.close()
