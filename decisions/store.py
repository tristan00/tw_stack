r"""store.py -- the v7 decision store (SQLite, one file per run dir).

The unit of training data is a DECISION POINT, and in v7 a decision point is FACTION-WIDE: the
advisor is shown every action every entity could take right now, in one list, and picks ONE. So:

  decision_points   one row per pick          (turn, seq, policy)
  entity_snapshots  one row per ENTITY in that decision point -- that entity's RAW state, read
                    from the game at the SAME instant as its offers
  action_offers     one row per OFFER, pointing at the entity snapshot it belongs to
  action_taken      the one offer that was chosen + hard evidence of whether it happened
  target_rows       one row per turn -- the reward inputs

Raw state, not features: the advisor featurizes from these records (advisor_v7/features.py), so the
trainer can featurize the same records the same way later. Nothing derived is stored.

Why state hangs off the ENTITY and not the offer: within one decision point the campaign block is
shared by every row, but each entity has its own state, its own province and its own position --
so an entity's ~80 offers all differ from another entity's, while sharing that entity's one state
row. Normalizing there stores one copy per entity instead of one per offer.

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


class IncompatibleStore(RuntimeError):
    """This decisions.sqlite predates the current schema and cannot be read."""

_DDL = """
CREATE TABLE IF NOT EXISTS target_rows(
  campaign_id TEXT NOT NULL, turn INTEGER NOT NULL, ts REAL,
  income REAL, settlements REAL, allies REAL, vassals REAL, power_rank REAL,
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
  latency_ms INTEGER, policy TEXT);

CREATE INDEX IF NOT EXISTS ix_dp ON decision_points(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_snap_dp ON entity_snapshots(decision_id);
CREATE INDEX IF NOT EXISTS ix_offer_dp ON action_offers(decision_id);
CREATE INDEX IF NOT EXISTS ix_taken_dp ON action_taken(decision_id);
"""


class DecisionStore:
    # columns added after a DB may already exist. CREATE TABLE IF NOT EXISTS does NOT add them to
    # an existing file, so a run started before the column landed would break every reader with
    # "no such column" -- migrate explicitly instead.
    _MIGRATIONS = (("decision_points", "campaign", "TEXT"),
                   ("decision_points", "world", "TEXT"),
                   ("decision_points", "timings", "TEXT"),
                   ("action_offers", "score", "REAL"),
                   ("action_offers", "exploit", "REAL"),
                   ("action_offers", "explore", "REAL"),
                   ("action_offers", "rank", "INTEGER"))

    # tables whose shape changed in the faction-wide redesign; a DB predating it cannot be read by
    # the new queries and must be skipped rather than half-migrated into something misleading.
    _REQUIRED = (("action_offers", "decision_id"), ("entity_snapshots", "decision_id"))

    def _assert_compatible(self):
        for table, col in self._REQUIRED:
            exists = self.con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                continue                       # fresh DB: the DDL will create it correctly
            cols = {r[1] for r in self.con.execute("PRAGMA table_info(%s)" % table)}
            if col not in cols:
                raise IncompatibleStore(
                    "%s predates the faction-wide decision-point schema (%s.%s missing). Its rows "
                    "have a different shape and cannot be read by the current queries."
                    % (self.path, table, col))

    def __init__(self, run_dir):
        # ⚠ CAMPAIGN IDENTITY. The game exposes no unique per-playthrough id: cm:get_campaign_name()
        # is the campaign TYPE ("main_warhammer") and the faction name repeats on every restart, so
        # keying on either would merge two separate Nagarythe playthroughs into one turn series and
        # silently corrupt the reward target. The recorder opens a fresh run dir per campaign, so
        # the run-dir stamp IS the unique campaign key -- faction@rundir.
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

    # ---------------------------------------------------------------- writes
    def campaign_key(self, faction, uuid=None):
        """This playthrough's identity.

        Prefers the uuid MINTED INSIDE THE CAMPAIGN (collect.campaign_uuid), which is tied to the
        campaign itself and so survives a recorder restart. Falls back to faction@rundir when the
        mint is unavailable -- still unique, just tied to the directory instead of the save.
        """
        return str(uuid) if uuid else "%s@%s" % (faction, self.run_id)

    def write_target_row(self, row):
        """Exactly-once per (campaign, turn). Returns True if this call inserted it."""
        cur = self.con.execute(
            "INSERT OR IGNORE INTO target_rows(campaign_id,turn,ts,income,settlements,allies,vassals,power_rank)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (self.campaign_key(row.get("campaign_id"), row.get("campaign_uuid")),
             int(row.get("turn") or 0), row.get("ts") or time.time(),
             row.get("income"), row.get("settlements"), row.get("allies"),
             row.get("vassals"), row.get("power_rank")))
        self.con.commit()
        return cur.rowcount > 0

    def write_decision(self, snapshot, decision_seq=0, policy=None):
        """Persist one FACTION-WIDE decision point exactly as the recorder read it.

        `snapshot` is collect.snapshot()'s output:
            {ts, campaign{...}, world{armies,settlements,hostiles},
             entities:[{context_kind, context_id, state, offers}]}
        Nothing is derived here -- the raw state and the raw world go in verbatim, because the
        advisor must featurize from these records and the trainer must featurize identically later.
        Returns the decision_id.
        """
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
        """Millisecond phase timings for one action, so a slow session can be read rather than
        guessed at: request -> collected -> received -> scored -> instructed -> verified."""
        if not timings:
            return 0
        self.con.execute("UPDATE decision_points SET timings=? WHERE decision_id=?",
                         (json.dumps(timings, default=str), decision_id))
        self.con.commit()
        return 1

    def attach_scores(self, decision_id, scores):
        """The advisor's ranking, stored next to the offers it ranked.
        `scores` = [{context_kind, context_id, action_type, key, score, exploit, explore, rank}]."""
        n = 0
        for s in scores or []:
            n += self.con.execute(
                "UPDATE action_offers SET score=?,exploit=?,explore=?,rank=? WHERE decision_id=? "
                "AND context_kind=? AND context_id=? AND action_type=? AND action_key=?",
                (s.get("score"), s.get("exploit"), s.get("explore"), s.get("rank"), decision_id,
                 s.get("context_kind"), str(s.get("context_id")), s.get("action_type"),
                 str(s.get("key")))).rowcount
        self.con.commit()
        return n

    def attach_taken(self, decision_id, taken, policy=None):
        """STREAMS 3+4: the chosen offer and the evidence of what actually happened.

        Called once with the pick (executed/confirmed unknown yet) or -- the normal case -- once
        with the launcher's finished ActionRecord. A second call for the same decision REPLACES the
        row, so a pick logged before execution is upgraded in place by its verification.
        """
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

    # ---------------------------------------------------------------- reads
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
        """One stored decision point in the shape the advisor featurizes (see journal.read_decision).
        Same shape for training and for prediction -- that is what keeps the two featurizations
        identical, so a model never sees columns at fit time that it cannot see at decision time."""
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

    def labelled_decisions(self):
        """Every VALID decision point with its label: (record, taken_key).

        A decision point is valid only when its taken action was CONFIRMED -- if we cannot prove
        what really happened, every label in that point is suspect, so the whole point is VOIDED.
        taken_key = (context_kind, context_id, action_type, action_key).
        """
        out = []
        for (did,) in self.con.execute("SELECT decision_id FROM decision_points").fetchall():
            t = self.con.execute(
                "SELECT context_kind,context_id,action_type,action_key,counted FROM action_taken"
                " WHERE decision_id=?", (did,)).fetchone()
            if t is None or not t[4]:
                continue                       # observation-only, or voided: unconfirmed
            out.append((self.read_decision(did), (t[0], str(t[1]), t[2], str(t[3]))))
        return out

    def target_series(self):
        """{campaign_id: {turn: {income, settlements, power_rank, allies, vassals}}} -- the reward
        inputs the trainer turns into the per-turn value target."""
        out = {}
        for camp, turn, inc, setl, allies, vass, rank in self.con.execute(
                "SELECT campaign_id,turn,income,settlements,allies,vassals,power_rank FROM target_rows"):
            out.setdefault(camp, {})[int(turn)] = {
                "income": inc or 0.0, "settlements": setl or 0.0,
                # rank is "lower = stronger" -> store INVERTED so every part is high=good
                "power_rank": (-rank if rank is not None else -50.0),
                "allies": allies or 0.0, "vassals": vass or 0.0}
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
