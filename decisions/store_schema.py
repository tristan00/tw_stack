from __future__ import annotations

"""The v2 decision store: interned actions, content-addressed blobs, clustered rows.

Measured on the archived corpus (22,136 decisions / 9,013,360 offers / 4.78 GB), the v1
layout projects to ~54 GB at the 250k-decision target and materialises 9.28 GB of RSS on a
full read -- ~107 GB at target, which does not run. Three things cause it, and each has a
fix that is pure storage, visible to nothing above the store:

  1. The action payload repeats. There are 474,685 distinct
     (context_kind, context_id, action_type, action_key, params) tuples among 9,013,360
     offers -- 19x, growing to ~28x at target (vocabulary exponent 0.842, six points).
     `actions` interns them; an offer row becomes an integer.
  2. The JSON blobs repeat. `world` is 58.2% byte-identical to the previous decision of
     the same campaign: 2.48x from content-addressing, 6.16x from zlib on top.
  3. `interrupt_decisions.tree_json` was 886 MB, 18.5% of the file, and had zero readers.
     It is not written.

`entities` and `offers` are WITHOUT ROWID and clustered on (decision_id, seq), so the rows
*are* the index -- that deletes ix_offer_dp and ix_offer_key outright, measured at 90 B a
row, 8.5 GB at target.

GATING IS A LAYOUT INVARIANT. offer_seq is assigned after gating: 0..n_available-1 are the
candidates and gated offers follow, so the candidate set is a contiguous prefix and a
training walk never touches the 64.6% of rows it must ignore. `available` survives as one
byte purely so the invariant is checkable:

    SELECT count(*) FROM offers WHERE (offer_seq < n_available) != (available = 1);  -- 0

Gated offers are still stored verbatim. A gate reason is a diagnostic, and the whole point
of keeping the payload is that an unprojected field is a re-derivation and not a wipe.

auto_vacuum=INCREMENTAL must be set before the first table exists; it cannot be turned on
afterwards without a full VACUUM.
"""

# Synthetic-id packing for the compatibility views. A decision may not exceed these, and
# write_decision raises rather than let two rows collide on a fabricated id.
MAX_ENTITIES_PER_DECISION = 1 << 16
MAX_OFFERS_PER_DECISION = 1 << 20

# score, exploit, rank, pct_global, pct_local, gnn_impact, gnn_rank
SCORE_FIELDS = ("score", "exploit", "rank", "pct_global", "pct_local",
                "gnn_impact", "gnn_rank")

SCHEMA_VERSION = "2"

PRAGMAS = (
    # Irreversible after the first table. Ordered first for that reason.
    "PRAGMA auto_vacuum=INCREMENTAL",
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
)

DDL = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

-- Which build of the collector produced a decision. v1 could not answer this, which is
-- why the old corpus cannot be migrated: the reader would have to guess.
CREATE TABLE IF NOT EXISTS collector_versions(
  version_id INTEGER PRIMARY KEY AUTOINCREMENT,
  collector_sha TEXT NOT NULL UNIQUE, git_sha TEXT, started_ts REAL, note TEXT);

CREATE TABLE IF NOT EXISTS blobs(
  blob_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sha TEXT NOT NULL UNIQUE, n INTEGER NOT NULL, z BLOB NOT NULL);

CREATE TABLE IF NOT EXISTS actions(
  action_id INTEGER PRIMARY KEY AUTOINCREMENT,
  context_kind TEXT NOT NULL, context_id TEXT NOT NULL,
  action_type TEXT NOT NULL, action_key TEXT NOT NULL, params TEXT NOT NULL,
  UNIQUE(context_kind, context_id, action_type, action_key, params));

-- campaign_map is WHICH MAP was played: wh3_main_combi (Immortal Empires, 534 factions),
-- wh3_main_chaos (Realm of Chaos), a custom key, or NULL for rows recorded before it was
-- collected. Immortal Empires and a small map are different environments -- different
-- faction counts, topology, end-turn cost and diplomatic structure -- so pooling them
-- unlabelled would repeat the mixed-schema mistake in a form no later reader could unpick.
-- With the column, collecting across configurations is a deliberate choice rather than a
-- silent one, and every consumer can group or filter on it.
CREATE TABLE IF NOT EXISTS campaigns(
  campaign_id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_key TEXT NOT NULL UNIQUE, faction TEXT, campaign_map TEXT,
  first_decision_id INTEGER, last_decision_id INTEGER,
  outcome TEXT, defeated INTEGER, turns INTEGER);

CREATE TABLE IF NOT EXISTS decisions(
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(campaign_id),
  ts REAL, turn INTEGER, decision_seq INTEGER NOT NULL DEFAULT 0,
  policy TEXT, version_id INTEGER REFERENCES collector_versions(version_id),
  n_entities INTEGER NOT NULL, n_offers INTEGER NOT NULL,
  campaign_blob INTEGER REFERENCES blobs(blob_id),
  world_blob INTEGER REFERENCES blobs(blob_id),
  timings TEXT);

CREATE TABLE IF NOT EXISTS entities(
  decision_id INTEGER NOT NULL, entity_seq INTEGER NOT NULL,
  context_kind TEXT NOT NULL, context_id TEXT NOT NULL,
  features_blob INTEGER NOT NULL REFERENCES blobs(blob_id),
  PRIMARY KEY(decision_id, entity_seq)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS offers(
  decision_id INTEGER NOT NULL, offer_seq INTEGER NOT NULL,
  entity_seq INTEGER NOT NULL, action_id INTEGER NOT NULL REFERENCES actions(action_id),
  PRIMARY KEY(decision_id, offer_seq)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS taken(
  decision_id INTEGER PRIMARY KEY,
  offer_seq INTEGER, entity_seq INTEGER, action_id INTEGER,
  ts REAL, executed INTEGER NOT NULL, confirmed INTEGER NOT NULL, counted INTEGER NOT NULL,
  refusal TEXT, confirm_signal TEXT, confirm_before TEXT, confirm_after TEXT,
  latency_ms INTEGER, policy TEXT, timing TEXT, diagnostics TEXT);

-- Separate so it can be dropped without touching the corpus: scores are a property of the
-- model that ranked a decision, not of the decision.
CREATE TABLE IF NOT EXISTS scores(
  decision_id INTEGER PRIMARY KEY, packed BLOB NOT NULL) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS interrupts(
  interrupt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, campaign_id INTEGER REFERENCES campaigns(campaign_id), turn INTEGER,
  kind TEXT NOT NULL, root TEXT, root_context TEXT,
  n_options INTEGER, options_json TEXT, chosen TEXT, chosen_context TEXT,
  executed INTEGER, confirmed INTEGER, counted INTEGER, refusal TEXT, latency_ms INTEGER,
  campaign_blob INTEGER REFERENCES blobs(blob_id),
  world_blob INTEGER REFERENCES blobs(blob_id),
  panel_blob INTEGER REFERENCES blobs(blob_id),
  policy TEXT);

CREATE TABLE IF NOT EXISTS target_rows(
  campaign_id TEXT NOT NULL, turn INTEGER NOT NULL, ts REAL,
  income REAL, settlements REAL, allies REAL, vassals REAL, power_rank REAL, lord_level REAL,
  PRIMARY KEY(campaign_id, turn));

CREATE TABLE IF NOT EXISTS entity_target_rows(
  campaign_id TEXT NOT NULL, turn INTEGER NOT NULL,
  context_kind TEXT NOT NULL, context_id TEXT NOT NULL,
  value REAL, ts REAL,
  PRIMARY KEY(campaign_id, turn, context_kind, context_id));

-- The whole world's war graph, one row per campaign turn, INCLUDING factions we have never
-- met. It is deliberately NOT in the decision record: the record is what the model may see,
-- and a model trained on relationships the player cannot observe would lean at training time
-- on information it will not have at play time -- the kind of failure that scores fine
-- offline and is silently wrong in the game.
--
-- `known_factions` is the filter, carried in the row itself rather than enforced by a
-- separate guard. Any consumer holds the met-set for that exact turn next to the data, so
-- clipping is a join and not a convention someone has to remember. The met set changes every
-- turn, which is precisely why it is stored per row instead of derived later.
--
-- Written once per TURN, not per action: two factions that are not us cannot change their
-- relationship while we are taking actions -- the AI moves between turns. PRIMARY KEY makes
-- the repeat writes within a turn a no-op.
CREATE TABLE IF NOT EXISTS diplo_state(
  campaign_id TEXT NOT NULL, turn INTEGER NOT NULL, ts REAL,
  known_blob INTEGER NOT NULL REFERENCES blobs(blob_id),  -- the met set at this turn
  war_blob INTEGER NOT NULL REFERENCES blobs(blob_id),    -- [{faction,at_war_with:[]}], ALL factions
  PRIMARY KEY(campaign_id, turn)) WITHOUT ROWID;

-- ------------------------------------------------------------------ the request channel
--
-- The advisor and the recorder are separate processes, and this is how they talk. It was
-- two append-only jsonl files, and the reader scanned its file FROM BYTE 0 on every single
-- call: journal._await read and JSON-parsed the entire responses log to find one req_id.
-- That is O(everything written so far) against a file that only grows. Measured on the run
-- that exposed it: 11.85 ms per MB, a 234 MB responses log, ~3000 ms per request by the end
-- of the day, and 3.37 hours of a 24.19-hour run -- 13.9% of wall clock -- spent scanning
-- text. The same record read out of this database measures 0.30 ms.
--
-- Here the reply lookup is a primary-key probe. It costs the same on hour 24 as on minute
-- one, which is the property the file never had.
CREATE TABLE IF NOT EXISTS rpc_requests(
  rpc_id INTEGER PRIMARY KEY AUTOINCREMENT,
  req_id TEXT UNIQUE, kind TEXT NOT NULL, ts REAL NOT NULL, payload TEXT);

-- A reply carries NO record. The recorder has already written the decision by the time it
-- answers, so the advisor reads it back by decision_id instead of being handed a 52 KB
-- copy through the channel. That copy is what inflated the responses log in the first
-- place, and it was introduced to avoid a database read that costs a third of a
-- millisecond.
CREATE TABLE IF NOT EXISTS rpc_responses(
  req_id TEXT PRIMARY KEY, ts REAL NOT NULL,
  decision_id INTEGER, payload TEXT, error TEXT) WITHOUT ROWID;

-- ------------------------------------------------------------- application data, not logs
--
-- These three were jsonl files that the dashboard re-parsed on request. They are corpus
-- data -- a campaign's outcome is the label every training run needs -- and §3.5 of the
-- pre-wipe spec already recorded "campaign outcome was never in the DB at all" as one of
-- the three reasons the v1 corpus could not migrate. It is in the DB now.
-- campaign_key is nullable and NOT the primary key on purpose. The postmortem log carried
-- only a faction and a wall clock, so the dashboard had to guess which campaign each
-- ending belonged to -- a time-window match that resolved 119 of 136 and silently dropped
-- the rest. The key is recorded at write time now, but an ending we genuinely cannot key
-- must still be storable and countable rather than discarded to make a join work.
CREATE TABLE IF NOT EXISTS postmortems(
  postmortem_id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_key TEXT, ts REAL, run_dir TEXT,
  faction TEXT, turn INTEGER, outcome TEXT, defeated INTEGER,
  reason TEXT, payload TEXT);

-- The training trial ledger is NOT here. It spans every run directory, and this database
-- is one run directory's corpus -- filing a global ledger inside it would scope it wrong.
-- It lives in metrics_db.py, which is a database at the level the data actually belongs to.

-- The diplomacy event tail. Was run/diplomacy.jsonl. This is the per-event stream the
-- campaign view shows; diplo_state above is the per-turn world graph and stays separate.
CREATE TABLE IF NOT EXISTS diplomacy_events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, campaign_key TEXT, turn INTEGER, kind TEXT, payload TEXT);

CREATE INDEX IF NOT EXISTS ix_dec_campaign ON decisions(campaign_id, decision_id);
CREATE INDEX IF NOT EXISTS ix_dec_turn ON decisions(turn);
CREATE INDEX IF NOT EXISTS ix_diplo_ev ON diplomacy_events(campaign_key, event_id);
"""


VIEWS = """
DROP VIEW IF EXISTS decision_points;
CREATE VIEW decision_points AS
SELECT d.decision_id AS decision_id, d.ts AS ts, c.campaign_key AS campaign_id,
       d.turn AS turn, d.decision_seq AS decision_seq, d.policy AS policy,
       d.n_entities AS n_entities, d.n_offers AS n_offers,
       unz(bc.z) AS campaign, unz(bw.z) AS world, d.timings AS timings
FROM decisions d
LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
LEFT JOIN blobs bc ON bc.blob_id = d.campaign_blob
LEFT JOIN blobs bw ON bw.blob_id = d.world_blob;

DROP VIEW IF EXISTS entity_snapshots;
CREATE VIEW entity_snapshots AS
SELECT (e.decision_id * {ES} + e.entity_seq) AS snapshot_id, e.decision_id AS decision_id,
       e.context_kind AS context_kind, e.context_id AS context_id,
       unz(b.z) AS features
FROM entities e JOIN blobs b ON b.blob_id = e.features_blob;

DROP VIEW IF EXISTS action_offers;
CREATE VIEW action_offers AS
SELECT (o.decision_id * {OS} + o.offer_seq) AS offer_id, o.decision_id AS decision_id,
       (o.decision_id * {ES} + o.entity_seq) AS snapshot_id,
       a.context_kind AS context_kind, a.context_id AS context_id,
       a.action_type AS action_type, a.action_key AS action_key,
       a.params AS params,
       -- Correlated subqueries, NOT a join. `scores` holds one packed row per decision
       -- averaging 15 KB, so joining it probed a fat row once per offer: a plain
       -- `count(*)` over this view spent 163 seconds re-reading the same blob 553 times
       -- per decision. A subquery in the select list is evaluated only when the column is
       -- actually selected, so a query that does not ask for scores does not pay for them.
       f32((SELECT packed FROM scores WHERE decision_id=o.decision_id),
           o.offer_seq * {NS} + 0) AS score,
       f32((SELECT packed FROM scores WHERE decision_id=o.decision_id),
           o.offer_seq * {NS} + 1) AS exploit,
       f32((SELECT packed FROM scores WHERE decision_id=o.decision_id),
           o.offer_seq * {NS} + 2) AS rank,
       f32((SELECT packed FROM scores WHERE decision_id=o.decision_id),
           o.offer_seq * {NS} + 3) AS pct_global,
       f32((SELECT packed FROM scores WHERE decision_id=o.decision_id),
           o.offer_seq * {NS} + 4) AS pct_local,
       f32((SELECT packed FROM scores WHERE decision_id=o.decision_id),
           o.offer_seq * {NS} + 5) AS gnn_impact,
       f32((SELECT packed FROM scores WHERE decision_id=o.decision_id),
           o.offer_seq * {NS} + 6) AS gnn_rank
FROM offers o
LEFT JOIN actions a ON a.action_id = o.action_id;

DROP VIEW IF EXISTS action_taken;
CREATE VIEW action_taken AS
SELECT t.decision_id AS taken_id, t.decision_id AS decision_id,
       (t.decision_id * {ES} + t.entity_seq) AS snapshot_id,
       (t.decision_id * {OS} + t.offer_seq) AS offer_id,
       t.ts AS ts, a.context_kind AS context_kind, a.context_id AS context_id,
       a.action_type AS action_type, a.action_key AS action_key,
       t.executed AS executed, t.confirmed AS confirmed, t.counted AS counted,
       t.refusal AS refusal, t.confirm_signal AS confirm_signal,
       t.confirm_before AS confirm_before, t.confirm_after AS confirm_after,
       t.latency_ms AS latency_ms, t.policy AS policy, t.timing AS timing,
       t.diagnostics AS diagnostics
FROM taken t LEFT JOIN actions a ON a.action_id = t.action_id;

DROP VIEW IF EXISTS interrupt_decisions;
CREATE VIEW interrupt_decisions AS
SELECT i.interrupt_id AS interrupt_id, i.ts AS ts, c.campaign_key AS campaign_id,
       i.turn AS turn, i.kind AS kind, i.root AS root, i.root_context AS root_context,
       i.n_options AS n_options, i.options_json AS options_json,
       i.chosen AS chosen, i.chosen_context AS chosen_context,
       unz(bc.z) AS campaign_json, i.executed AS executed, i.confirmed AS confirmed,
       i.counted AS counted, i.refusal AS refusal, i.latency_ms AS latency_ms,
       NULL AS tree_json, i.policy AS policy,
       unz(bw.z) AS world_json, unz(bp.z) AS panel_json
FROM interrupts i
LEFT JOIN campaigns c ON c.campaign_id = i.campaign_id
LEFT JOIN blobs bc ON bc.blob_id = i.campaign_blob
LEFT JOIN blobs bw ON bw.blob_id = i.world_blob
LEFT JOIN blobs bp ON bp.blob_id = i.panel_blob;
""".format(ES=MAX_ENTITIES_PER_DECISION, OS=MAX_OFFERS_PER_DECISION,
           NS=len(SCORE_FIELDS))


# Every stored offer is a candidate, so the invariant is a count rather than a
# partition: n_offers must equal the number of rows actually written.
LAYOUT_INVARIANT = (
    "SELECT count(*) FROM decisions d WHERE d.n_offers != "
    "(SELECT count(*) FROM offers o WHERE o.decision_id = d.decision_id)")
