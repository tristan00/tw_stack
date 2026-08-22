from __future__ import annotations


MAX_ENTITIES_PER_DECISION = 1 << 16
MAX_OFFERS_PER_DECISION = 1 << 20

SCORE_FIELDS = ("score", "exploit", "rank", "pct_global", "pct_local",
                "gnn_impact", "gnn_rank")

MODEL_SCORE_FIELDS = ("score", "rank")

RANKED_ARMS = ("greedy_catboost", "marwil_gnn", "greedy_gnn")
RANK_SOURCE = {"greedy_catboost": ("scores", SCORE_FIELDS.index("rank")),
               "marwil_gnn": ("scores", SCORE_FIELDS.index("gnn_rank")),
               "greedy_gnn": ("model_scores", MODEL_SCORE_FIELDS.index("rank"))}
PAIRS = tuple((a, b) for i, a in enumerate(RANKED_ARMS) for b in RANKED_ARMS[i + 1:])


def pair_key(a, b):
    return "%s|%s" % (a, b)


def pair_of(key):
    a, _, b = str(key or "").partition("|")
    return (a, b) if (a, b) in PAIRS else None

SCHEMA_VERSION = "3"

PRAGMAS = (
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

-- Per-offer scores of the arms that came after the packed `scores` row was laid out: one
-- packed (score, rank) row per (decision, arm). Additive -- the legacy layout above is
-- never re-shaped, so every older decision keeps reading exactly as it was written.
CREATE TABLE IF NOT EXISTS model_scores(
  decision_id INTEGER NOT NULL, model TEXT NOT NULL, packed BLOB NOT NULL,
  PRIMARY KEY(decision_id, model)) WITHOUT ROWID;

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

CREATE TABLE IF NOT EXISTS rpc_requests(
  rpc_id INTEGER PRIMARY KEY AUTOINCREMENT,
  req_id TEXT UNIQUE, kind TEXT NOT NULL, ts REAL NOT NULL, payload TEXT);

CREATE TABLE IF NOT EXISTS rpc_responses(
  req_id TEXT PRIMARY KEY, ts REAL NOT NULL,
  decision_id INTEGER, payload TEXT, error TEXT) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS postmortems(
  postmortem_id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_key TEXT, ts REAL, run_dir TEXT,
  faction TEXT, turn INTEGER, outcome TEXT, defeated INTEGER,
  reason TEXT, payload TEXT);


CREATE TABLE IF NOT EXISTS diplomacy_events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, campaign_key TEXT, turn INTEGER, kind TEXT, payload TEXT);

-- What the UCB start selector saw and did, one row per pick plus the whole ranking it
-- scored. The session logs the same table, but logs rotate and this is the record of
-- WHY a start was played: c, the plays it divided by, and every start's mean and
-- explore term at that instant. explore and score are NULL for an unplayed start,
-- whose bonus is infinite. blend, entropy and std are the scored terms behind the
-- score (blend + explore); rows written before they existed carry NULL there.
-- A pick's ts is the campaign's picked_ts: join a pick to the campaign it produced on it.
CREATE TABLE IF NOT EXISTS ucb_picks(
  pick_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL, c REAL, total_plays INTEGER,
  campaign_map TEXT, faction TEXT,
  n INTEGER, mean REAL, explore REAL, score REAL, tied INTEGER,
  blend REAL, entropy REAL, std REAL);

CREATE TABLE IF NOT EXISTS ucb_pick_rows(
  pick_id INTEGER NOT NULL, rank INTEGER NOT NULL,
  campaign_map TEXT, faction TEXT,
  n INTEGER, mean REAL, explore REAL, score REAL, chosen INTEGER,
  blend REAL, entropy REAL, std REAL,
  PRIMARY KEY(pick_id, rank));

CREATE INDEX IF NOT EXISTS ix_dec_campaign ON decisions(campaign_id, decision_id);
CREATE INDEX IF NOT EXISTS ix_dec_turn ON decisions(turn);
CREATE INDEX IF NOT EXISTS ix_diplo_ev ON diplomacy_events(campaign_key, event_id);
CREATE INDEX IF NOT EXISTS ix_ucb_picks_ts ON ucb_picks(ts);
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
           o.offer_seq * {NS} + 6) AS gnn_rank,
       f32((SELECT packed FROM model_scores WHERE decision_id=o.decision_id
             AND model='greedy_gnn'), o.offer_seq * {MS} + 0) AS ggnn_score,
       f32((SELECT packed FROM model_scores WHERE decision_id=o.decision_id
             AND model='greedy_gnn'), o.offer_seq * {MS} + 1) AS ggnn_rank
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

-- The ONLY definitions of per-turn state. Both are projections of the decision snapshots:
-- no writer, no second collection, nothing stored that can drift. turn_bounds is the single
-- place "first/last snapshot of a turn" is encoded; decision_id order is chronological
-- (single-writer AUTOINCREMENT, one campaign at a time, one decision at a time).
-- Measurement semantics:
--   delta within turn N up to a decision  = decision's snapshot - turn_open[N]
--   what turn N's player decisions did    = turn_close[N] - turn_open[N]
--   turn over turn                        = turn_open[N+1] - turn_open[N]
-- turn_close is the state at the turn's LAST DECISION, not "after all actions": a turn
-- ended by end_turn reflects every real action; a single-decision turn (wounded lord) has
-- turn_close == turn_open; an action-cap turn's final action lands in turn_open[N+1].
-- power_rank keeps the snapshot convention throughout: HIGHER IS BETTER, peak = MAX.
DROP VIEW IF EXISTS turn_bounds;
CREATE VIEW turn_bounds AS
SELECT campaign_id, turn,
       MIN(decision_id) AS open_id, MAX(decision_id) AS close_id
FROM decisions GROUP BY campaign_id, turn;

DROP VIEW IF EXISTS turn_open;
CREATE VIEW turn_open AS
SELECT c.campaign_key AS campaign_id, d.turn AS turn, d.ts AS ts,
       d.decision_id AS decision_id,
       CAST(json_extract(unz(b.z), '$.income')      AS REAL) AS income,
       CAST(json_extract(unz(b.z), '$.settlements') AS REAL) AS settlements,
       CAST(json_extract(unz(b.z), '$.allies')      AS REAL) AS allies,
       CAST(json_extract(unz(b.z), '$.vassals')     AS REAL) AS vassals,
       CAST(json_extract(unz(b.z), '$.power_rank')  AS REAL) AS power_rank,
       CAST(json_extract(unz(b.z), '$.lord_level')  AS REAL) AS lord_level
FROM turn_bounds tb
JOIN decisions d ON d.decision_id = tb.open_id
LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
LEFT JOIN blobs b ON b.blob_id = d.campaign_blob;

DROP VIEW IF EXISTS turn_close;
CREATE VIEW turn_close AS
SELECT c.campaign_key AS campaign_id, d.turn AS turn, d.ts AS ts,
       d.decision_id AS decision_id,
       CAST(json_extract(unz(b.z), '$.income')      AS REAL) AS income,
       CAST(json_extract(unz(b.z), '$.settlements') AS REAL) AS settlements,
       CAST(json_extract(unz(b.z), '$.allies')      AS REAL) AS allies,
       CAST(json_extract(unz(b.z), '$.vassals')     AS REAL) AS vassals,
       CAST(json_extract(unz(b.z), '$.power_rank')  AS REAL) AS power_rank,
       CAST(json_extract(unz(b.z), '$.lord_level')  AS REAL) AS lord_level
FROM turn_bounds tb
JOIN decisions d ON d.decision_id = tb.close_id
LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
LEFT JOIN blobs b ON b.blob_id = d.campaign_blob;

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

-- The ONE definition of "campaigns recorded per start": campaigns with two or more
-- decisions, keyed by map and faction. One decision is a failed launch, not a run --
-- it can only ever score zero -- so it is not counted here, in campaign_gains, or in
-- the run's campaign total. The UI starts screen and the session's --width sampler
-- both read this view; a second counter would drift.
DROP VIEW IF EXISTS start_counts;
CREATE VIEW start_counts AS
SELECT c.campaign_map AS campaign_map, c.faction AS faction, COUNT(*) AS n
FROM campaigns c
WHERE EXISTS (SELECT 1 FROM decisions d WHERE d.campaign_id = c.campaign_id
               LIMIT 1 OFFSET 1)
GROUP BY c.campaign_map, c.faction;

-- The ONE definition of the reward metrics: per campaign, the
-- campaign's first snapshot -> peak delta of settlements, lord level, allies and
-- vassals, over EVERY decision snapshot. Turns are not a measurement boundary here:
-- end_turn is an ordinary action the sampler may or may not draw, so "the first/last
-- decision of a turn" is an artifact of where end_turn landed, not a state worth
-- measuring. Reading turn_open alone hid every gain made and lost between two of its
-- rows -- 25 of 565 campaigns at the time this was fixed.
-- A campaign with a single decision is not a measurement: its peak IS its first
-- snapshot, so it can only ever score zero and would drag every average it lands in
-- toward zero. They are dropped here, matching campaign_growth.py, which has always
-- required two or more decisions before it reports a delta.
-- analytics/generations.py buckets these by model generation; the session's --ucb
-- start selector averages their sum per start. A second computation would drift.
-- The `LIMIT -1` is a no-op limit that blocks the query flattener: without it SQLite
-- inlines the subquery and calls unz() once per extracted column, decompressing every
-- campaign blob four times over -- 0.475s instead of 0.135s for identical rows.
DROP VIEW IF EXISTS campaign_gains;
CREATE VIEW campaign_gains AS
SELECT campaign_map, faction, campaign_key, MIN(ts) AS first_ts,
       IFNULL(MAX(turn), 0) AS turns_reached,
       IFNULL(MAX(settlements), 0) - IFNULL(MIN(fs), 0) AS settlements_gained,
       IFNULL(MAX(lord_level), 0) - IFNULL(MIN(fl), 0) AS levels_gained,
       IFNULL(MAX(allies), 0) - IFNULL(MIN(fa), 0) AS allies_gained,
       IFNULL(MAX(vassals), 0) - IFNULL(MIN(fv), 0) AS vassals_gained
FROM (
  SELECT campaign_map, faction, campaign_key, ts, turn,
         settlements, lord_level, allies, vassals,
         FIRST_VALUE(settlements) OVER w AS fs,
         FIRST_VALUE(lord_level) OVER w AS fl,
         FIRST_VALUE(allies) OVER w AS fa,
         FIRST_VALUE(vassals) OVER w AS fv
  FROM (
    SELECT c.campaign_map AS campaign_map, c.faction AS faction,
           c.campaign_key AS campaign_key, d.decision_id AS decision_id, d.ts AS ts,
           d.turn AS turn,
           CAST(json_extract(j, '$.settlements') AS REAL) AS settlements,
           CAST(json_extract(j, '$.lord_level') AS REAL) AS lord_level,
           CAST(json_extract(j, '$.allies') AS REAL) AS allies,
           CAST(json_extract(j, '$.vassals') AS REAL) AS vassals
    FROM (SELECT decision_id, campaign_id, ts, turn, unz(b.z) AS j
          FROM decisions d LEFT JOIN blobs b ON b.blob_id = d.campaign_blob
          LIMIT -1) d
    LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
  )
  WINDOW w AS (PARTITION BY campaign_key ORDER BY decision_id)
)
GROUP BY campaign_key
HAVING COUNT(*) >= 2;
""".format(ES=MAX_ENTITIES_PER_DECISION, OS=MAX_OFFERS_PER_DECISION,
           NS=len(SCORE_FIELDS), MS=len(MODEL_SCORE_FIELDS))


LAYOUT_INVARIANT = (
    "SELECT count(*) FROM decisions d WHERE d.n_offers != "
    "(SELECT count(*) FROM offers o WHERE o.decision_id = d.decision_id)")
