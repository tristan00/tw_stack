# 3. Physical DDL (part c: reference manifest/loc, migration checkpoint, analytics, views, partitioning/retention, index inventory)

## 3.11 Reference schema (static part; the 1,520 pack tables are generated, 04 §4.2)

```sql
CREATE TABLE ref.manifest (
  build_id        INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  started_ts      DOUBLE PRECISION NOT NULL,
  finished_ts     DOUBLE PRECISION,
  status          TEXT NOT NULL,
  exe_version     TEXT NOT NULL,
  steam_build_id  BIGINT,
  schema_sha256   BYTEA NOT NULL,
  schema_version  SMALLINT NOT NULL,
  fingerprint     BYTEA NOT NULL,
  n_tables        SMALLINT,
  n_rows          INTEGER,
  n_loc           INTEGER,
  build_seconds   REAL,
  error           TEXT,
  CHECK (status IN ('building', 'live', 'failed', 'superseded')),
  CHECK (octet_length(schema_sha256) = 32 AND octet_length(fingerprint) = 32)
);
CREATE UNIQUE INDEX manifest_live ON ref.manifest (status) WHERE status = 'live';

CREATE TABLE ref.manifest_pack (
  build_id   INTEGER NOT NULL REFERENCES ref.manifest,
  pack       TEXT NOT NULL,
  sha256     BYTEA NOT NULL,
  size       BIGINT NOT NULL,
  mtime      DOUBLE PRECISION NOT NULL,
  load_order SMALLINT NOT NULL,
  pack_type  SMALLINT NOT NULL,
  n_db_files SMALLINT NOT NULL,
  n_loc_files SMALLINT NOT NULL,
  PRIMARY KEY (build_id, pack)
);

CREATE TABLE ref.table_meta (
  tbl            TEXT PRIMARY KEY,
  pack_table     TEXT NOT NULL UNIQUE,
  version        SMALLINT NOT NULL,
  source_pack    TEXT NOT NULL,
  n_rows         INTEGER NOT NULL,
  n_cols         SMALLINT NOT NULL,
  key_cols       TEXT[] NOT NULL,
  key_unique     BOOLEAN NOT NULL,
  raw_bytes      INTEGER NOT NULL
);

CREATE TABLE ref.column_meta (
  tbl            TEXT NOT NULL REFERENCES ref.table_meta,
  col            TEXT NOT NULL,
  ca_order       SMALLINT NOT NULL,
  ron_type       TEXT NOT NULL,
  pg_type        TEXT NOT NULL,
  is_key         BOOLEAN NOT NULL,
  is_reference   BOOLEAN NOT NULL,
  ref_tbl        TEXT,
  ref_col        TEXT,
  ref_resolved   REAL,
  fk_declared    BOOLEAN NOT NULL,
  default_value  TEXT,
  description    TEXT,
  PRIMARY KEY (tbl, col)
);

CREATE TABLE ref.loc (
  tbl      TEXT NOT NULL,
  col      TEXT NOT NULL,
  key      TEXT NOT NULL,
  loc_key  TEXT NOT NULL UNIQUE,
  text     TEXT NOT NULL,
  PRIMARY KEY (tbl, col, key)
);
```

Generated pack tables (one per `db/<table>/` folder, name = folder stem minus `_tables`, e.g. `ref.character_skills`): columns in `ca_order` with the RON name (identifiers quoted; the 8 placeholder names of `schema_db.json` are replaced by the RON names, R5 A), types per 04 §4.3, `PRIMARY KEY (<is_key columns>)` because every key is unique in data (R5 B: 0 duplicates in 1,520 tables), `FOREIGN KEY` for every `is_reference` relation whose target table exists in the pack, whose target column is that table's key or a UNIQUE column, and whose non-empty values resolve ≥ 99.9% (R5 C: 2,450 relations at 100%; 3 at 99.89-99.92% are **not** declared; 323 targets absent; 6 non-key targets → declared only for the 4 whose target column is unique; the 11 type-mismatch relations are declared after the cast rule of 04 §4.4). Absent `OptionalStringU8` values are NULL; `StringU8` empty strings stay `''` (R5 A decoder note; type_census). Build target is schema `ref_build`, swapped to `ref` in one transaction (04 §4.6).

## 3.12 Migration checkpoint and verification tables

```sql
CREATE SCHEMA migrate;
CREATE TABLE migrate.checkpoint (
  stage        TEXT NOT NULL,
  range_lo     BIGINT NOT NULL,
  range_hi     BIGINT NOT NULL,
  state        TEXT NOT NULL,
  worker       TEXT,
  started_ts   DOUBLE PRECISION,
  finished_ts  DOUBLE PRECISION,
  rows_in      BIGINT,
  rows_out     BIGINT,
  error        TEXT,
  PRIMARY KEY (stage, range_lo),
  CHECK (state IN ('pending', 'running', 'done', 'failed')),
  CHECK (range_hi >= range_lo)
);
CREATE TABLE migrate.mismatch (
  stage        TEXT NOT NULL,
  snapshot_id  BIGINT NOT NULL,
  role         TEXT NOT NULL,
  entity_seq   SMALLINT,
  path         TEXT NOT NULL,
  expected     TEXT,
  actual       TEXT,
  PRIMARY KEY (stage, snapshot_id, role, entity_seq, path)
);
CREATE TABLE migrate.id_map (
  old_table   TEXT NOT NULL,
  old_id      BIGINT NOT NULL,
  new_id      BIGINT NOT NULL,
  PRIMARY KEY (old_table, old_id)
);
```

`migrate.id_map` holds `interrupts.interrupt_id → snapshot_id` (decisions keep their ids; `campaigns.campaign_id` kept; `postmortems`, `diplomacy_events`, `ucb_picks` keep their ids by explicit `OVERRIDING SYSTEM VALUE` inserts so external references in logs stay valid).

## 3.13 Analytics (surviving tenants; 08 §8.6 for the dissolved ones)

```sql
CREATE TABLE analytics.state (
  tenant           TEXT PRIMARY KEY,
  formula_version  SMALLINT NOT NULL,
  watermark        BIGINT NOT NULL,
  built_ts         DOUBLE PRECISION,
  last_run_ts      DOUBLE PRECISION,
  last_run_seconds REAL,
  last_error       TEXT
);

CREATE TABLE analytics.model_agreement (
  decision_id   BIGINT NOT NULL REFERENCES corpus.decision,
  pair          TEXT NOT NULL,
  status        TEXT NOT NULL,
  n             SMALLINT,
  rho           REAL,
  tau           REAL,
  rbo           REAL,
  top1_agree    BOOLEAN,
  top5_overlap  REAL,
  top10_overlap REAL,
  taken_rank_a  SMALLINT,
  taken_rank_b  SMALLINT,
  ts            DOUBLE PRECISION NOT NULL,
  campaign_id   INTEGER NOT NULL,
  policy_id     SMALLINT,
  action_type_id SMALLINT,
  entity_kind_id SMALLINT,
  PRIMARY KEY (decision_id, pair)
);
CREATE INDEX model_agreement_pair_ts ON analytics.model_agreement (pair, ts) WHERE status = 'ok';

CREATE TABLE analytics.agreement_summary   (pair TEXT NOT NULL, scope TEXT NOT NULL, comparable INTEGER, rho_median REAL, rho_mean REAL, tau_median REAL, rbo_median REAL, top1_rate REAL, missing_b INTEGER, no_scores INTEGER, PRIMARY KEY (pair, scope));
CREATE TABLE analytics.agreement_hist      (pair TEXT NOT NULL, bucket SMALLINT NOT NULL, lo REAL, hi REAL, n INTEGER, PRIMARY KEY (pair, bucket));
CREATE TABLE analytics.agreement_series    (pair TEXT NOT NULL, axis TEXT NOT NULL, seq INTEGER NOT NULL, from_decision BIGINT, to_decision BIGINT, from_ts DOUBLE PRECISION, decisions INTEGER, rho_median REAL, gate TEXT, trial TEXT, generation INTEGER, retrained BOOLEAN, bucket_size INTEGER, PRIMARY KEY (pair, axis, seq));
CREATE TABLE analytics.agreement_breakdown (pair TEXT NOT NULL, dim TEXT NOT NULL, key TEXT NOT NULL, decisions INTEGER, rho_median REAL, top1_rate REAL, PRIMARY KEY (pair, dim, key));

CREATE TABLE analytics.acquisition (
  campaign_id           INTEGER NOT NULL REFERENCES corpus.campaign,
  family                TEXT NOT NULL,
  key_id                INTEGER NOT NULL,
  ctx                   TEXT NOT NULL,
  sub_id                SMALLINT,
  kind_id               SMALLINT,
  first_seen_snapshot   BIGINT NOT NULL,
  first_seen_turn       SMALLINT NOT NULL,
  acquired_snapshot     BIGINT,
  acquired_turn         SMALLINT,
  ranks                 SMALLINT,
  PRIMARY KEY (campaign_id, family, key_id, ctx)
);
CREATE INDEX acquisition_family_key ON analytics.acquisition (family, key_id) INCLUDE (campaign_id, acquired_turn, ranks);

CREATE TABLE analytics.item_event (
  event_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id  INTEGER NOT NULL REFERENCES corpus.campaign,
  character_id INTEGER NOT NULL REFERENCES corpus.character,
  ancillary_id SMALLINT NOT NULL REFERENCES dict.ancillary,
  kind         TEXT NOT NULL,
  snapshot_id  BIGINT NOT NULL,
  turn         SMALLINT NOT NULL,
  CHECK (kind IN ('on', 'off'))
);
CREATE INDEX item_event_campaign ON analytics.item_event (campaign_id, character_id, snapshot_id);
CREATE INDEX item_event_ancillary ON analytics.item_event (ancillary_id, kind);
```

`acquisition.key_id` is the family's dictionary id (`family` ∈ research → `dict.tech_node`, building → `dict.building`, skills → `dict.skill`, traits → `dict.trait`, items → `dict.ancillary`, settlement → `dict.region`); `sub_id` = character subtype (`dict.agent_subtype`) for skills/traits. Both tenants are incremental by `snapshot_id` watermark and read member tables only (08 §8.6). `decision_features/resources/heroes`, all `game_turn*`, `campaign_growth`, `model_generations`, `campaign_endings`, `growth_summary`, `item_event_state` are not created (C12; R3 C.1/F: no consumer, or replaced by the views below).

## 3.14 Views replacing today's `pg_schema` views and the dissolved tenants

```sql
CREATE VIEW corpus.turn_bounds AS
  SELECT s.campaign_id, s.turn, MIN(s.snapshot_id) AS open_id, MAX(s.snapshot_id) AS close_id
  FROM corpus.snapshot s JOIN corpus.decision d ON d.decision_id = s.snapshot_id
  GROUP BY s.campaign_id, s.turn;

CREATE VIEW corpus.turn_open AS
  SELECT b.campaign_id, b.turn, b.open_id AS decision_id, sc.income, sc.settlements, sc.allies, sc.vassals, sc.power_rank, sc.lord_level, s.ts
  FROM corpus.turn_bounds b JOIN corpus.snapshot s ON s.snapshot_id = b.open_id
  JOIN corpus.snapshot_campaign sc ON sc.snapshot_id = b.open_id;

CREATE VIEW corpus.campaign_gains AS
  SELECT campaign_id, campaign_key, first_ts, last_ts, n_decisions,
         first_settlements, peak_settlements, peak_settlements - first_settlements AS settlements_gained,
         first_lord_level, peak_lord_level, peak_lord_level - first_lord_level AS levels_gained,
         allies_max, vassals_max, turns
  FROM corpus.campaign WHERE first_snapshot_id IS NOT NULL;

CREATE VIEW corpus.start_counts AS
  SELECT campaign_map_id, faction_id, COUNT(*) AS n FROM corpus.campaign
  WHERE n_decisions > 0 GROUP BY campaign_map_id, faction_id;

CREATE VIEW corpus.campaign_ending AS
  SELECT p.campaign_id, c.campaign_key, p.ts, p.faction_id, p.outcome_id, p.when_text, p.error,
         p.plausibility_verdict AS verdict, p.growth_reason, p.growth_turn, p.growth_min_gain
  FROM corpus.postmortem p JOIN corpus.campaign c USING (campaign_id)
  WHERE p.postmortem_id = (SELECT MAX(postmortem_id) FROM corpus.postmortem q WHERE q.campaign_id = p.campaign_id);

CREATE VIEW analytics.model_generation AS
  SELECT trial, generation, ts AS seg_from_ts,
         LEAD(ts) OVER (ORDER BY ts) AS seg_to_ts, campaigns, corpus_n_decisions AS corpus_decisions
  FROM ops.trial WHERE NOT archived;
```

`decision_points`, `entity_snapshots`, `action_offers`, `action_taken`, `turn_close`, `interrupt_decisions` (R1 A.18) are not recreated: their consumers (R3) are rewritten in 08 against base tables; the synthetic `snapshot_id = decision_id*65536+entity_seq` and `offer_id = decision_id*2^20+offer_seq` packings die with them (the API keeps emitting `offer_id` computed in SQL for URL stability: `decision_id * 1048576 + offer_seq`, 08 §8.4).

## 3.15 Partitioning and retention

- **No partitioning.** The largest table (`corpus.offer`) is projected at 1.2 GB heap + 0.5 GB PK after migration and ≈ 0.14 GB/day (06); every hot query is an index range on `decision_id`/`snapshot_id`, and partition pruning would buy nothing while adding DDL to the write path. Re-evaluate at 100 GB (≈ 2 years at the measured rate, 06 §6.5).
- **Retention.** The corpus is append-only and kept whole (training uses a 1000-campaign window but the API and analytics read all campaigns). `rpc_request/rpc_response`: rows older than 900 s are deleted by the recorder every 600 ticks as today (R4 G) — kept, index on `ts`. `ops.bus_call_stat`: upsert-only, bounded by distinct keys (205k). `migrate.*`: dropped after cutover validation (11). No autovacuum-hostile pattern remains: the only UPDATEs are `campaign` (1 row/decision), `character` (last_snapshot_id, ≤ 3 rows/decision), `decision.n_offers` (1/decision), `taken` (1 UPDATE per decision at verification) — all on fillfactor-80 tables so they are HOT updates.
- **TOAST.** No column is expected to exceed 2 KB except `taken.stderr` (p50 1.3 KB, p95 6.9 KB, max 20.9 KB; M2 C) and `rpc_request.payload`; both use the default `EXTENDED` storage (pglz); `stderr` is read by one manual tool, so its compression cost is irrelevant on the write path (≈ 2 KB pglz ≈ 20 µs).

## 3.16 Index inventory (all indexes; each traces to a catalogued path, 01)

| index | serves |
|---|---|
| PKs on every table | equality/range on the row key; `offer`, `*_set_member`, `world_*`, `char_state`, `province_state` PKs are the range scans of L1/T3/T7 |
| `snapshot_campaign_ts (campaign_id, ts, snapshot_id)` | state-at-T, `prev_decision_id` resolution (W5), L8, T2 |
| `snapshot_campaign_turn (campaign_id, turn, snapshot_id) INCLUDE (kind_id)` | `turn_bounds`/`turn_open` (T1, L7, A3) index-only |
| `snapshot_ts (ts)` | `throughput` (A1), timeline (U7) |
| `campaign_faction_map`, `campaign_first_ts`, `campaign_first_snapshot` | starts (A6, L5), window floor/keys (T9), `_camp_meta` |
| `snapshot_campaign_scalars (snapshot_id) INCLUDE (...)` | T1/T4/A7 index-only scalar reads without touching the heap |
| `snapshot_entity_character`, `snapshot_entity_region` (partial) | latest state per character/region (A4, A5, A6, U3) |
| `char_state_character (character_id, snapshot_id DESC)` | latest lord/hero per campaign (A4, A6 `_start_snapshots`), item_events (U3) |
| `province_state_region (region_id, snapshot_id DESC)` | acquisitions building family (U3) |
| `interrupt_prev_decision`, `interrupt_kind` | T2, T5, L7, A10 |
| `offer_action (action_id, decision_id)` | `campaign_research` offered-set per turn, `start_firsts` offered CTE, `campaign_items` (A5, A6) |
| `taken_campaign (campaign_id, decision_id) INCLUDE (...)` | every per-campaign taken aggregate (A2, A3, A6, A11) index-only |
| `taken_ts`, `taken_action` | `throughput`, `actions_summary`, `forcing`, `catalog_overtime` (A1, A10, A11, A7) |
| `diplomacy_event_campaign/_ts`, `postmortem_campaign/_ts`, `ucb_pick_ts` | A3, A2, A12, U6, U7 |
| `state_set (kind, hash)` UNIQUE | W1 set lookup |
| member `UNIQUE (set_id, <key>)` | integrity only; lookups by set_id use the PK prefix |
| `model_agreement_pair_ts` (partial) | agreement rollups and series (U4, A11) |
| `acquisition_family_key`, `item_event_*` | A7, A6 |
| `rpc_request_ts`, `rpc_response_ts` | prune (W11) |
| `manifest_live` (partial unique) | one live build (04) |

Removed relative to today (R6 C): `ix_rpc_req_id` (duplicate PK), `ix_dec_gains` (8-column covering index replaced by `campaign` columns), `ix_dec_campaign` (prefix of the turn index), the 741 MB `actions` 5-column UNIQUE index, `offers.ix_offers_action` (246 MB → `offer_action` on 24.8M rows is kept but on 4+8 bytes), the 40 unused analytics indexes.
