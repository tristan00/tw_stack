from __future__ import annotations

"""Read and write the decision corpus.

The storage layout is decisions/store_schema.py; read its docstring for why it looks the way
it does. This file is the API, and the API is deliberately unchanged from v1: everything
above the store still speaks (decision, entities, offers), and every reader outside this
package still speaks v1 SQL through the compatibility views. Open the database with
decisions.dbopen.connect() so those views have the functions they are written in terms of.

A v1 database is not upgraded in place and never will be. The mechanics would be about a
minute; the semantics do not survive. 0.705% of recent v1 offers collide on the identity
tuple their labels were attached by -- the disambiguating params were never part of the
key -- so 1,643 of ~4,200 recent decisions have a genuinely ambiguous taken row. Collector
version cannot be reconstructed without guessing, which is the failure `collector_versions`
exists to end. And campaign outcome was never in the database at all. Opening a v1 file
raises IncompatibleStore, pointing at the archive.
"""

import hashlib
import json
import os
import sqlite3
import struct
import sys
import time
import zlib

from decisions import dbopen
from decisions import store_schema as S

DB_NAME = "decisions.sqlite"
ZLEVEL = 6


class IncompatibleStore(RuntimeError):
    pass


class _SnapshotRead:

    def __init__(self, con):
        self.con = con
        self.entered = False

    def __enter__(self):
        try:
            self.con.execute("BEGIN DEFERRED")
            self.entered = True
        except sqlite3.OperationalError as e:
            sys.stderr.write("store: could not open a read snapshot (%s) -- reads may be torn\n"
                             % repr(e)[:100])
        return self

    def __exit__(self, *exc):
        if self.entered:
            try:
                self.con.execute("COMMIT")
            except sqlite3.OperationalError:
                pass
        return False


def _dumps(o):
    """Canonical JSON: interning and content-addressing both depend on two equal payloads
    producing one identical string, so key order is not allowed to drift."""
    return json.dumps(o, default=str, sort_keys=True, separators=(",", ":"))


class DecisionStore:

    def __init__(self, run_dir, readonly=False):
        self.run_id = os.path.basename(str(run_dir).rstrip("/\\"))
        self.path = os.path.join(run_dir, DB_NAME)
        self.readonly = bool(readonly)
        self._blob_cache = {}
        self._action_cache = {}
        self._campaign_cache = {}
        if self.readonly:
            if not os.path.exists(self.path):
                raise IncompatibleStore("no %s in %s" % (DB_NAME, run_dir))
            self.con = dbopen.connect(self.path, readonly=True)
            self._assert_compatible()
            return
        fresh = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
        self.con = dbopen.connect(self.path, readonly=False)
        if fresh:
            # auto_vacuum can only be chosen before the first table exists.
            for p in S.PRAGMAS:
                self.con.execute(p)
        else:
            self.con.execute("PRAGMA journal_mode=WAL")
        self._assert_compatible(fresh=fresh)
        self.con.executescript(S.DDL)
        self._add_missing_columns()
        self.con.executescript(S.VIEWS)
        self.con.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('schema_version',?)",
                         (S.SCHEMA_VERSION,))
        self.con.commit()

    # Columns added to an EXISTING database. `CREATE TABLE IF NOT EXISTS` is a no-op once
    # the table exists, so a new column in store_schema reaches a fresh db and silently
    # misses every db already on disk -- the reader then sees the column in the DDL, queries
    # it, and gets an error or a null it misreads as data.
    #
    # Additive only, and only where NULL is a truthful answer for old rows: campaign_map is
    # null for campaigns recorded before the map was collected, which is exactly right --
    # we genuinely do not know which map those were played on.
    _ADD_COLUMNS = (("campaigns", "campaign_map", "TEXT"),)

    def _add_missing_columns(self):
        for table, col, decl in self._ADD_COLUMNS:
            have = {r[1] for r in self.con.execute("PRAGMA table_info(%s)" % table)}
            if col not in have:
                self.con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))

    # ------------------------------------------------------------------ compatibility

    def _assert_compatible(self, fresh=False):
        have = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not have and fresh:
            return
        if "decision_points" in have or ("action_offers" in have and "offers" not in have):
            raise IncompatibleStore(
                "%s is a v1 decision store. It is not upgraded in place: 0.705%% of its "
                "offers collide on the identity its labels were attached by, its collector "
                "version cannot be reconstructed, and campaign outcome was never recorded. "
                "Archive it and start a new run directory." % self.path)
        if have and "offers" not in have and "meta" not in have:
            raise IncompatibleStore("%s is not a decision store" % self.path)

    def snapshot_read(self):
        return _SnapshotRead(self.con)

    def _assert_writable(self, what):
        if self.readonly:
            raise IncompatibleStore("%s called on a read-only store (%s)" % (what, self.path))

    def close(self):
        try:
            self.con.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ interning

    def _blob(self, text):
        """Content-address a JSON payload. `world` is 58.2% byte-identical to the previous
        decision of the same campaign, so most calls here are a cache hit and no write."""
        if text is None:
            return None
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        hit = self._blob_cache.get(sha)
        if hit is not None:
            return hit
        row = self.con.execute("SELECT blob_id FROM blobs WHERE sha=?", (sha,)).fetchone()
        if row is None:
            cur = self.con.execute(
                "INSERT INTO blobs(sha,n,z) VALUES(?,?,?)",
                (sha, len(text), dbopen.pack(text, ZLEVEL)))
            bid = cur.lastrowid
        else:
            bid = row[0]
        if len(self._blob_cache) > 4096:
            self._blob_cache.clear()
        self._blob_cache[sha] = bid
        return bid

    def _action_id(self, ck, cid, atype, akey, params_text):
        k = (ck, cid, atype, akey, params_text)
        hit = self._action_cache.get(k)
        if hit is not None:
            return hit
        self.con.execute(
            "INSERT OR IGNORE INTO actions(context_kind,context_id,action_type,action_key,"
            "params) VALUES(?,?,?,?,?)", k)
        aid = self.con.execute(
            "SELECT action_id FROM actions WHERE context_kind=? AND context_id=? AND "
            "action_type=? AND action_key=? AND params=?", k).fetchone()[0]
        if len(self._action_cache) > 200000:
            self._action_cache.clear()
        self._action_cache[k] = aid
        return aid

    def campaign_key(self, faction, uuid=None):
        return str(uuid) if uuid else "%s@%s" % (faction, self.run_id)

    def _campaign_id(self, key, faction=None, campaign_map=None):
        hit = self._campaign_cache.get(key)
        if hit is not None:
            # The map arrives with the first decision but the campaign row may have been
            # created by an earlier write (a target row, say) that did not carry it. Fill it
            # in once rather than leaving the column null for the campaign's whole life.
            if campaign_map:
                self.con.execute(
                    "UPDATE campaigns SET campaign_map=? "
                    "WHERE campaign_id=? AND (campaign_map IS NULL OR campaign_map='')",
                    (campaign_map, hit))
            return hit
        self.con.execute(
            "INSERT OR IGNORE INTO campaigns(campaign_key,faction,campaign_map) VALUES(?,?,?)",
            (key, faction, campaign_map))
        cid = self.con.execute("SELECT campaign_id FROM campaigns WHERE campaign_key=?",
                               (key,)).fetchone()[0]
        self._campaign_cache[key] = cid
        return cid

    def register_collector(self, collector_sha, git_sha=None, note=None):
        """Which build produced the rows that follow. v1 could not say, and that alone
        makes its corpus unmigratable."""
        self._assert_writable("register_collector")
        self.con.execute(
            "INSERT OR IGNORE INTO collector_versions(collector_sha,git_sha,started_ts,note)"
            " VALUES(?,?,?,?)", (collector_sha, git_sha, time.time(), note))
        row = self.con.execute("SELECT version_id FROM collector_versions WHERE collector_sha=?",
                               (collector_sha,)).fetchone()
        self.con.commit()
        self._version_id = row[0]
        return row[0]

    _version_id = None

    # ------------------------------------------------------------------ writes

    def write_decision(self, snapshot, decision_seq=0, policy=None):
        self._assert_writable("write_decision")
        camp = snapshot.get("campaign") or {}
        ents = snapshot.get("entities") or []
        if any(e.get("offers") for e in ents):
            raise ValueError(
                "write_decision was handed offers. The recorder stores STATE; the advisor "
                "generates and gates the options and hands the survivors back through "
                "attach_options. An offer arriving here means inference has leaked back "
                "into the collector.")
        if len(ents) >= S.MAX_ENTITIES_PER_DECISION:
            raise ValueError("decision has %d entities; the view id packing allows %d"
                             % (len(ents), S.MAX_ENTITIES_PER_DECISION - 1))
        cid = self._campaign_id(self.campaign_key(camp.get("faction"),
                                                  camp.get("campaign_uuid")),
                                camp.get("faction"), camp.get("campaign_map"))
        cur = self.con.execute(
            "INSERT INTO decisions(campaign_id,ts,turn,decision_seq,policy,version_id,"
            "n_entities,n_offers,campaign_blob,world_blob)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cid, snapshot.get("ts") or time.time(), int(camp.get("turn") or 0),
             int(decision_seq), policy, self._version_id, len(ents), 0,
             self._blob(_dumps(camp)), self._blob(_dumps(snapshot.get("world") or {}))))
        did = cur.lastrowid

        self.con.executemany(
            "INSERT INTO entities(decision_id,entity_seq,context_kind,context_id,"
            "features_blob) VALUES(?,?,?,?,?)",
            [(did, ei, e.get("context_kind"), str(e.get("context_id")),
              self._blob(_dumps(e.get("state") or {})))
             for ei, e in enumerate(ents)])

        self.con.execute(
            "UPDATE campaigns SET first_decision_id=COALESCE(first_decision_id,?),"
            "last_decision_id=?, turns=MAX(COALESCE(turns,0),?) WHERE campaign_id=?",
            (did, did, int(camp.get("turn") or 0), cid))
        self.con.commit()
        return did

    def attach_options(self, decision_id, options):
        """Store the options the advisor's gate let through. Only survivors reach here.

        A gated candidate is not stored -- not as a row with a reason, not as anything.
        An action the agent could not have taken is not part of the decision it faced, and
        a corpus that carries them makes every count downstream mean something else.
        """
        self._assert_writable("attach_options")
        ents = {(k, str(i)): seq for seq, (k, i) in enumerate(
            self.con.execute("SELECT context_kind,context_id FROM entities"
                             " WHERE decision_id=? ORDER BY entity_seq", (decision_id,)))}
        rows = []
        for o in options or []:
            ei = ents.get((o.get("context_kind"), str(o.get("context_id"))))
            if ei is None:
                raise ValueError("option %s:%s names an entity the decision does not have"
                                 % (o.get("context_kind"), o.get("context_id")))
            rows.append((decision_id, len(rows), ei,
                         self._action_id(o.get("context_kind"), str(o.get("context_id")),
                                         o.get("action_type"), str(o.get("key")),
                                         _dumps(o.get("params") or {}))))
        if len(rows) >= S.MAX_OFFERS_PER_DECISION:
            raise ValueError("decision has %d options; the view id packing allows %d"
                             % (len(rows), S.MAX_OFFERS_PER_DECISION - 1))
        self.con.executemany(
            "INSERT INTO offers(decision_id,offer_seq,entity_seq,action_id)"
            " VALUES(?,?,?,?)", rows)
        self.con.execute("UPDATE decisions SET n_offers=? WHERE decision_id=?",
                         (len(rows), decision_id))
        self.con.commit()
        return len(rows)

    def attach_timings(self, decision_id, timings):
        if not timings:
            return 0
        self._assert_writable("attach_timings")
        self.con.execute("UPDATE decisions SET timings=? WHERE decision_id=?",
                         (_dumps(timings), decision_id))
        self.con.commit()
        return 1

    def _seq_by_identity(self, decision_id):
        """(context_kind, context_id, action_type, action_key) -> offer_seq."""
        out = {}
        for seq, ck, cid, at, ak in self.con.execute(
                "SELECT o.offer_seq,a.context_kind,a.context_id,a.action_type,a.action_key"
                " FROM offers o JOIN actions a ON a.action_id=o.action_id"
                " WHERE o.decision_id=?", (decision_id,)):
            out.setdefault((ck, str(cid), at, str(ak)), seq)
        return out

    def attach_scores(self, decision_id, scores):
        """Packed float32, one row per decision, indexed by offer_seq.

        v1 wrote seven float columns on all 9M offer rows and updated them by identity
        tuple -- which matched *every* colliding row, 0.705% of recent offers. Here a
        score lands at a position, and the position is unique by construction.
        """
        if not scores:
            return 0
        self._assert_writable("attach_scores")
        n_offers = self.con.execute("SELECT n_offers FROM decisions WHERE decision_id=?",
                                    (decision_id,)).fetchone()
        if n_offers is None:
            return 0
        n_offers = int(n_offers[0])
        ns = len(S.SCORE_FIELDS)
        buf = bytearray(struct.pack("<%df" % (n_offers * ns),
                                    *([float("nan")] * (n_offers * ns))))
        row = self.con.execute("SELECT packed FROM scores WHERE decision_id=?",
                               (decision_id,)).fetchone()
        if row is not None and len(row[0]) == len(buf):
            buf = bytearray(row[0])
        seqs = self._seq_by_identity(decision_id)
        n = 0
        for s in scores:
            seq = seqs.get((s.get("context_kind"), str(s.get("context_id")),
                            s.get("action_type"), str(s.get("key"))))
            if seq is None:
                continue
            for j, f in enumerate(S.SCORE_FIELDS):
                v = s.get(f)
                struct.pack_into("<f", buf, (seq * ns + j) * 4,
                                 float("nan") if v is None else float(v))
            n += 1
        self.con.execute("INSERT OR REPLACE INTO scores(decision_id,packed) VALUES(?,?)",
                         (decision_id, bytes(buf)))
        self.con.commit()
        return n

    def attach_taken(self, decision_id, taken, policy=None):
        self._assert_writable("attach_taken")
        ck, cid = taken.get("context_kind"), str(taken.get("context_id"))
        atype, akey = taken.get("action_type"), str(taken.get("key"))
        row = self.con.execute(
            "SELECT o.offer_seq,o.entity_seq,o.action_id FROM offers o"
            " JOIN actions a ON a.action_id=o.action_id WHERE o.decision_id=?"
            " AND a.context_kind=? AND a.context_id=? AND a.action_type=? AND a.action_key=?"
            " ORDER BY o.offer_seq LIMIT 1",
            (decision_id, ck, cid, atype, akey)).fetchone()
        seq, ent_seq, action_id = row if row else (None, None, None)
        if action_id is None:
            # The action was played but never offered. Intern it anyway so the label keeps
            # its identity instead of becoming a null join.
            action_id = self._action_id(ck, cid, atype, akey,
                                        _dumps(taken.get("params") or {}))
        conf = taken.get("confirm") or {}
        self.con.execute(
            "INSERT OR REPLACE INTO taken(decision_id,offer_seq,entity_seq,action_id,ts,"
            "executed,confirmed,counted,refusal,confirm_signal,confirm_before,confirm_after,"
            "latency_ms,policy,timing,diagnostics)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (decision_id, seq, ent_seq, action_id, taken.get("ts") or time.time(),
             1 if taken.get("executed") else 0, 1 if taken.get("confirmed") else 0,
             1 if taken.get("counted") else 0, taken.get("refusal"), conf.get("signal"),
             _dumps(conf.get("before")), _dumps(conf.get("after")),
             conf.get("latency_ms"), policy or taken.get("policy"),
             _dumps(taken.get("timing")),
             _dumps({"stderr": taken.get("stderr"), "prechecks": taken.get("prechecks"),
                     "execute_error": taken.get("execute_error"),
                     "doomed": taken.get("doomed"), "params": taken.get("params")})))
        self.con.commit()
        return seq is not None

    def write_target_row(self, row):
        self._assert_writable("write_target_row")
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

    def write_entity_target_rows(self, campaign_id, turn, rows):
        self._assert_writable("write_entity_target_rows")
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

    def write_diplo_state(self, campaign_id, turn, known_factions, war_graph):
        """The whole world's war graph for one turn, met-set included in the row.

        Deliberately outside the decision record. The record is what the model may see, and
        this contains relationships between factions the player has never met; training on
        those would teach the model to lean on information it does not have at play time.
        `known_factions` travels WITH the data so clipping is a join rather than a rule
        someone has to remember -- the met set changes every turn, so it cannot be
        reconstructed afterwards.

        INSERT OR IGNORE, so calling this on every action is harmless: only the first write
        of a turn lands. Two factions that are not us cannot change their relationship while
        we are mid-turn taking actions.
        """
        self._assert_writable("write_diplo_state")
        if not campaign_id or war_graph is None:
            return False
        kb = self._blob(json.dumps(known_factions or [], sort_keys=True))
        wb = self._blob(json.dumps(war_graph or [], sort_keys=True))
        cur = self.con.execute(
            "INSERT OR IGNORE INTO diplo_state(campaign_id,turn,ts,known_blob,war_blob)"
            " VALUES(?,?,?,?,?)",
            (campaign_id, int(turn or 0), time.time(), kb, wb))
        self.con.commit()
        return cur.rowcount > 0

    @staticmethod
    def _flag(v):
        return None if v is None else (1 if v else 0)

    def write_interrupt(self, row):
        """`tree` is accepted and dropped. interrupt_decisions.tree_json was 886 MB, 18.5%
        of the v1 database, and had no readers anywhere in the repo.

        ONLY `panel` survives as a payload -- it is the single blob column here. Any other
        key a caller invents is silently discarded, which has already cost once: battle
        results were filed under `battle` and both rows landed with panel_blob NULL. Put
        screen payloads under `panel`.
        """
        self._assert_writable("write_interrupt")
        camp = row.get("campaign") or {}
        opts = row.get("options") or {}
        counted = row.get("counted")
        if counted is None and row.get("confirmed") is not None:
            counted = bool(row.get("executed")) and bool(row.get("confirmed"))
        self.con.execute(
            "INSERT INTO interrupts(ts,campaign_id,turn,kind,root,root_context,n_options,"
            "options_json,chosen,chosen_context,executed,confirmed,counted,refusal,"
            "latency_ms,campaign_blob,world_blob,panel_blob,policy)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row.get("ts") or time.time(),
             self._campaign_id(self.campaign_key(camp.get("faction"),
                                                 camp.get("campaign_uuid")),
                               camp.get("faction")),
             int(camp.get("turn") or 0),
             row.get("screen"), row.get("root"), row.get("root_context"),
             len(opts), _dumps(opts), row.get("chosen"), row.get("chosen_context"),
             self._flag(row.get("executed")), self._flag(row.get("confirmed")),
             self._flag(counted), row.get("refusal"), row.get("latency_ms"),
             self._blob(_dumps(camp)),
             self._blob(_dumps(row["world"])) if row.get("world") else None,
             self._blob(_dumps(row["panel"])) if row.get("panel") else None,
             row.get("policy")))
        self.con.commit()

    # ------------------------------------------------------------------ reads

    def summary(self):
        q = lambda s: self.con.execute(s).fetchone()[0]
        return {"target_rows": q("SELECT COUNT(*) FROM target_rows"),
                "decisions": q("SELECT COUNT(*) FROM decisions"),
                "snapshots": q("SELECT COUNT(*) FROM entities"),
                "offers": q("SELECT COUNT(*) FROM offers"),
                "taken": q("SELECT COUNT(*) FROM taken"),
                "counted": q("SELECT COUNT(*) FROM taken WHERE counted=1"),
                "unconfirmed": q("SELECT COUNT(*) FROM taken WHERE executed=1 AND confirmed=0")}

    def max_decision_id(self):
        r = self.con.execute("SELECT MAX(decision_id) FROM decisions").fetchone()
        return int(r[0]) if r and r[0] is not None else 0

    def _entities_and_offers(self, where="", args=()):
        """Both clustered scans, merged on decision_id. Entities come back in entity_seq
        order and each entity's offers in offer_seq order, so action-node order (and
        therefore the taken mask) is a property of the layout rather than of an index."""
        ents_by_dec = {}
        for did, ei, ck, cid, feats in self.con.execute(
                "SELECT e.decision_id,e.entity_seq,e.context_kind,e.context_id,unz(b.z)"
                " FROM entities e JOIN blobs b ON b.blob_id=e.features_blob" + where +
                " ORDER BY e.decision_id,e.entity_seq", args):
            ents_by_dec.setdefault(did, []).append(
                {"snapshot_id": did * S.MAX_ENTITIES_PER_DECISION + ei,
                 "context_kind": ck, "context_id": cid,
                 "state": json.loads(feats or "{}"), "offers": []})
        w = where.replace("e.decision_id", "o.decision_id") if where else ""
        for did, seq, ei, at, ak, params in self.con.execute(
                "SELECT o.decision_id,o.offer_seq,o.entity_seq,a.action_type,a.action_key,"
                "a.params FROM offers o"
                " JOIN actions a ON a.action_id=o.action_id" + w +
                " ORDER BY o.decision_id,o.offer_seq", args):
            ents = ents_by_dec.get(did)
            if ents is None or ei >= len(ents):
                continue
            ents[ei]["offers"].append(
                {"offer_id": did * S.MAX_OFFERS_PER_DECISION + seq,
                 "action_type": at, "key": ak,
                 "params": json.loads(params or "{}")})
        return ents_by_dec

    def read_decision(self, decision_id):
        row = self.con.execute(
            "SELECT d.turn,c.campaign_key,unz(bc.z),unz(bw.z) FROM decisions d"
            " JOIN campaigns c ON c.campaign_id=d.campaign_id"
            " LEFT JOIN blobs bc ON bc.blob_id=d.campaign_blob"
            " LEFT JOIN blobs bw ON bw.blob_id=d.world_blob"
            " WHERE d.decision_id=?", (decision_id,)).fetchone()
        if row is None:
            raise KeyError("decision %s not in the store" % decision_id)
        turn, ckey, cjson, wjson = row
        ents = self._entities_and_offers(" WHERE e.decision_id=?", (decision_id,))
        return {"decision_id": decision_id, "turn": turn, "campaign_id": ckey,
                "campaign": json.loads(cjson or "{}"), "world": json.loads(wjson or "{}"),
                "entities": ents.get(decision_id, [])}

    def decision_index(self):
        """[(decision_id, campaign_key, ts)] in decision_id order -- no blobs, no offers.

        The cheap half of a join an interrupt has to make: a blocking screen records no
        world of its own worth building a graph from (world_state on a popped panel yields
        no relations, no citizenry and no war_graph -- 0 of 347 archived rows carry any of
        the three), so it borrows the last real DECISION snapshot instead. This is over
        `decisions`, which is exactly why an interrupt that follows another interrupt walks
        past it to a real snapshot: interrupts are not in this table. 137 of 347 rows need
        that, and the chain runs 5 deep behind a battle.

        Pair with read_decision() for the few hundred that are actually wanted, rather than
        labelled_decisions(), which drags every entity and offer in the corpus along.
        """
        return [(int(did), ck, ts or 0.0) for did, ck, ts in self.con.execute(
            "SELECT d.decision_id,c.campaign_key,d.ts FROM decisions d"
            " JOIN campaigns c ON c.campaign_id=d.campaign_id"
            " ORDER BY d.decision_id")]

    def taken_map(self, confirmed_only=False):
        """{decision_id: ((kind, id, type, key), counted)} for every labelled decision."""
        out = {}
        for did, ck, cid, at, ak, counted, refusal in self.con.execute(
                "SELECT t.decision_id,a.context_kind,a.context_id,a.action_type,a.action_key,"
                "t.counted,t.refusal FROM taken t LEFT JOIN actions a ON a.action_id=t.action_id"):
            if refusal == "awaiting_execution":
                continue
            if confirmed_only and not counted:
                continue
            out[did] = ((ck, str(cid), at, str(ak)), bool(counted))
        return out

    def labelled_decisions(self, confirmed_only=False, after=None, before=None):
        """Rows in decision_id order. `after` (exclusive) and `before` (inclusive) bound
        decision_id so a caller can read one shard instead of the whole corpus."""
        rng, args = "", []
        if after is not None:
            rng += " AND decision_id>?"
            args.append(int(after))
        if before is not None:
            rng += " AND decision_id<=?"
            args.append(int(before))

        taken = {}
        for did, ck, cid, at, ak, counted, refusal in self.con.execute(
                "SELECT t.decision_id,a.context_kind,a.context_id,a.action_type,a.action_key,"
                "t.counted,t.refusal FROM taken t"
                " LEFT JOIN actions a ON a.action_id=t.action_id"
                + ((" WHERE 1" + rng.replace("decision_id", "t.decision_id")) if rng else ""),
                args):
            if refusal == "awaiting_execution":
                continue
            if confirmed_only and not counted:
                continue
            taken[did] = ((ck, str(cid), at, str(ak)), bool(counted))
        if not taken:
            return []

        w = (" WHERE 1" + rng.replace("decision_id", "e.decision_id")) if rng else ""
        ents_by_dec = self._entities_and_offers(w, args)

        out = []
        for did, turn, ckey, cjson, wjson in self.con.execute(
                "SELECT d.decision_id,d.turn,c.campaign_key,unz(bc.z),unz(bw.z)"
                " FROM decisions d JOIN campaigns c ON c.campaign_id=d.campaign_id"
                " LEFT JOIN blobs bc ON bc.blob_id=d.campaign_blob"
                " LEFT JOIN blobs bw ON bw.blob_id=d.world_blob"
                + ((" WHERE 1" + rng.replace("decision_id", "d.decision_id")) if rng else "")
                + " ORDER BY d.decision_id", args):
            hit = taken.get(did)
            if hit is None:
                continue
            out.append(({"decision_id": did, "turn": turn, "campaign_id": ckey,
                         "campaign": json.loads(cjson or "{}"),
                         "world": json.loads(wjson or "{}"),
                         "entities": ents_by_dec.get(did, [])}, hit[0], hit[1]))
        return out

    def campaign_snapshots(self):
        out = []
        for ckey, ts, cjson, wjson in self.con.execute(
                "SELECT c.campaign_key,d.ts,unz(bc.z),unz(bw.z) FROM decisions d"
                " JOIN campaigns c ON c.campaign_id=d.campaign_id"
                " LEFT JOIN blobs bc ON bc.blob_id=d.campaign_blob"
                " LEFT JOIN blobs bw ON bw.blob_id=d.world_blob"
                " ORDER BY d.decision_id"):
            try:
                c = json.loads(cjson) if cjson else {}
            except Exception:
                c = {}
            try:
                w = json.loads(wjson) if wjson else {}
            except Exception:
                w = {}
            out.append((ckey, ts or 0.0, c, w))
        return out

    def action_sequence(self):
        return self.con.execute(
            "SELECT c.campaign_key,t.ts,a.action_type FROM taken t"
            " JOIN decisions d ON d.decision_id=t.decision_id"
            " JOIN campaigns c ON c.campaign_id=d.campaign_id"
            " LEFT JOIN actions a ON a.action_id=t.action_id"
            " WHERE a.action_type != 'noop'"
            " AND (t.refusal IS NULL OR t.refusal != 'awaiting_execution')"
            " ORDER BY t.decision_id").fetchall()

    def interrupt_rows(self):
        out = []
        for (iid, ts, camp, turn, kind, opts, chosen, executed, confirmed, counted, refusal,
             cjson, wjson, pjson) in self.con.execute(
                "SELECT i.interrupt_id,i.ts,c.campaign_key,i.turn,i.kind,i.options_json,"
                "i.chosen,i.executed,i.confirmed,i.counted,i.refusal,unz(bc.z),unz(bw.z),"
                "unz(bp.z) FROM interrupts i"
                " LEFT JOIN campaigns c ON c.campaign_id=i.campaign_id"
                " LEFT JOIN blobs bc ON bc.blob_id=i.campaign_blob"
                " LEFT JOIN blobs bw ON bw.blob_id=i.world_blob"
                " LEFT JOIN blobs bp ON bp.blob_id=i.panel_blob"
                " ORDER BY i.interrupt_id"):
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
        out = {}
        for camp, turn, inc, setl, allies, vass, rank, lvl in self.con.execute(
                "SELECT campaign_id,turn,income,settlements,allies,vassals,power_rank,lord_level"
                " FROM target_rows"):
            out.setdefault(camp, {})[int(turn)] = {
                "income": inc or 0.0, "settlements": setl or 0.0,
                "power_rank": (-rank if rank is not None else -50.0),
                "allies": allies or 0.0, "vassals": vass or 0.0,
                "lord_level": lvl or 0.0}
        return out

    def entity_series(self):
        out = {}
        for camp, turn, kind, cid, val in self.con.execute(
                "SELECT campaign_id,turn,context_kind,context_id,value FROM entity_target_rows"):
            if val is None:
                continue
            out.setdefault(camp, {}).setdefault((kind, str(cid)), {})[int(turn)] = float(val)
        return out

    # ------------------------------------------------------------------ checks

    def layout_violations(self):
        """`offer_seq < n_available` must mean exactly `available`. If this is ever
        non-zero the contiguous-prefix read is silently returning the wrong candidates."""
        return self.con.execute(S.LAYOUT_INVARIANT).fetchone()[0]
