# 3. Physical DDL (part a: schemas, dictionaries, spine, world, entity states)

Complete, executable PostgreSQL 17 SQL. Files 03a/03b/03c execute in order in one `psql -1 -v ON_ERROR_STOP=1` session against the new database `tw_stack` on D:. Type policy (C3): identifiers and counts/levels/ranks/indices → `SMALLINT`/`INTEGER`/`BIGINT` by measured range (M1 A, M2 B); measures → `DOUBLE PRECISION` (2.9); booleans → `BOOLEAN`; text only for free text; dictionary ids → `SMALLINT` (largest family 5,855 effect bundles, R5; 32,767 cap is 5× headroom, and a family exceeding it fails the INSERT loudly). Every table is `WITHOUT OIDS`, default `fillfactor` 100 except where a row is updated after insert (`campaign`, `character`, `taken`, `decision` — fillfactor 80).

```sql
CREATE SCHEMA dict;
CREATE SCHEMA corpus;
CREATE SCHEMA ref;
CREATE SCHEMA ops;
CREATE SCHEMA analytics;
```

## 3.1 Dictionaries (C1)

One table per identifier family, identical shape. `ref_tbl`/`ref_col` name the reference row the key resolves to; `is_reference = false` rows are the engine pseudo-values and artefacts (M3 B: `rebels`, `ruins`, `wh_main_grn_skull`, `campaign`, dilemma commonprefix artefacts, `''`), inserted explicitly with `note`. `ref_build_id` = the reference build in which the key last resolved (04 §4.5 re-resolves every dictionary after each build and flips `is_reference` when a key disappears from the game data — an explicit fact, not a fallback).

```sql
CREATE TABLE dict.family (
  family      TEXT PRIMARY KEY,
  ref_tbl     TEXT NOT NULL,
  ref_col     TEXT NOT NULL,
  loc_col     TEXT,
  note        TEXT
);
INSERT INTO dict.family (family, ref_tbl, ref_col, loc_col, note) VALUES
 ('faction',        'factions',                             'key',        'screen_name',        'M3: 98.8% + rebels/ruins/truncated'),
 ('region',         'regions',                              'key',        'onscreen',           '100%'),
 ('province',       'provinces',                            'key',        'onscreen',           '100%; '''' is non-reference'),
 ('skill',          'character_skills',                     'key',        'localised_name',     '100%'),
 ('unit',           'main_units',                           'unit',       NULL,                 'loc via land_units.onscreen_name (M3 E)'),
 ('building',       'building_levels',                      'level_name', NULL,                 'loc via building_culture_variants (M3 E)'),
 ('tech_node',      'technology_nodes',                     'key',        NULL,                 'loc via technologies.onscreen_name'),
 ('agent_subtype',  'agent_subtypes',                       'key',        'onscreen_name_override', '586/586 incl. 40 non-recruitable'),
 ('agent_type',     'agents',                               'key',        NULL,                 '9 values'),
 ('ancillary',      'ancillaries',                          'key',        'onscreen_name',      '100%'),
 ('ritual',         'rituals',                              'key',        'display_name',       '100%'),
 ('ritual_chain',   'ritual_chains',                        'key',        'display_name',       ''),
 ('trait',          'character_traits',                     'key',        NULL,                 '100%'),
 ('trait_level',    'character_trait_levels',               'key',        'onscreen_name',      '100%'),
 ('mission',        'missions',                             'key',        'localised_title',    '100%'),
 ('mission_issuer', 'mission_issuers',                      'key',        'on_screen_name',     ''),
 ('edict',          'provincial_initiative_records',        'key',        'localised_name',     '100%'),
 ('effect_bundle',  'effect_bundles',                       'key',        'localised_title',    '100%'),
 ('pooled_resource','pooled_resources',                     'key',        'display_name',       '100%; corruption keys included'),
 ('stance',         'campaign_stances',                     'key',        NULL,                 'engine enum strings, no loc'),
 ('agent_action',   'agent_actions',                        'unique_id',  'localised_action_name', ''),
 ('captive_option', 'campaign_post_battle_captive_options', 'record_key', 'onscreen_name',      ''),
 ('dilemma',        'dilemmas',                             'key',        'localised_title',    'incidents resolved through dict.incident'),
 ('incident',       'incidents',                            'key',        'localised_title',    ''),
 ('armory_item',    'armory_item_variants',                 'key',        NULL,                 ''),
 ('unit_category',  'land_units',                           'category',   NULL,                 'non-key column'),
 ('campaign_map',   'campaigns',                            'campaign_key', NULL,               '2 values'),
 ('culture',        'cultures',                             'key',        NULL,                 'race_of() 3rd token (features.py:41-45)');

DO $$
DECLARE f TEXT;
BEGIN
  FOR f IN SELECT family FROM dict.family LOOP
    EXECUTE format($q$
      CREATE TABLE dict.%I (
        id            SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        key           TEXT NOT NULL UNIQUE,
        is_reference  BOOLEAN NOT NULL,
        ref_build_id  INTEGER,
        note          TEXT,
        CHECK (is_reference = (ref_build_id IS NOT NULL))
      )$q$, f);
  END LOOP;
END $$;

INSERT INTO dict.faction (key, is_reference, note) VALUES
 ('rebels', false, 'engine pseudo-faction (M3 B)'),
 ('ruins',  false, 'collector masking literal collect.py:406-413'),
 ('wh_main_grn_skull', false, 'truncation artefact diplo_stream.py:14'),
 ('campaign', false, 'actions row 37129 context_id artefact');
INSERT INTO dict.province (key, is_reference, note) VALUES ('', false, 'empty province string on 33/3667 sampled province blobs (M1 A.5)');
```

Non-game enumerations captured by the collector or the advisor (bounded, human-authored) use the same shape without a reference table:

```sql
CREATE TABLE dict.enum (
  enum_id   SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  domain    TEXT NOT NULL,
  key       TEXT NOT NULL,
  UNIQUE (domain, key)
);
INSERT INTO dict.enum (domain, key) VALUES
 ('snapshot_kind','decision'),('snapshot_kind','interrupt'),
 ('entity_kind','campaign'),('entity_kind','lord'),('entity_kind','hero'),('entity_kind','province'),
 ('hostile_kind','army'),('hostile_kind','neutral_army'),('hostile_kind','settlement'),('hostile_kind','hero'),('hostile_kind','neutral_hero'),
 ('skill_status','inactive'),('skill_status','locked_due_to_rank'),('skill_status','active'),('skill_status','locked_by_item'),('skill_status','locked_by_skill'),
 ('mission_status','active'),('mission_status','succeeded'),('mission_status','cancelled'),
 ('merc_action','recruit_ror'),('merc_action','raise_dead'),('merc_action','recruit_blessed'),('merc_action','recruit_imperial'),
 ('gift','small'),('gift','medium'),('gift','large'),
 ('diplo_term','declare_war'),('diplo_term','peace'),('diplo_term','nonaggression_pact'),('diplo_term','trade_agreement'),('diplo_term','defensive_alliance'),('diplo_term','soft_access'),('diplo_term','military_alliance'),('diplo_term','vassal'),('diplo_term','confederation'),
 ('refusal','execute_failed'),('refusal','command_silently_refused'),('refusal','pre_check_refused'),('refusal','executed_unconfirmed'),('refusal','campaign_died'),('refusal','snapshot_failed'),('refusal','awaiting_execution'),('refusal','confirm_unreadable_bus_failure'),
 ('policy','random'),('policy','greedy_catboost'),('policy','greedy_gnn'),('policy','marwil_gnn'),('policy','forced_end_turn'),('policy','greedy_catboost_random_fallback'),
 ('interrupt_kind','pre_battle'),('interrupt_kind','battle_results'),('interrupt_kind','occupation'),('interrupt_kind','dilemma'),('interrupt_kind','diplomacy_proposal'),('interrupt_kind','diplomacy_notice'),('interrupt_kind','war_declared'),('interrupt_kind','event_ack'),('interrupt_kind','declare_war_cancel'),('interrupt_kind','ally_attacked'),
 ('state_at','panel'),('state_at','recorder'),
 ('diplo_event_kind','deal'),('diplo_event_kind','pair_checkpoint'),('diplo_event_kind','campaign_end'),
 ('diplo_channel','outgoing'),('diplo_channel','diplomacy_proposal'),('diplo_channel','diplomacy_notice'),('diplo_channel','ally_attacked'),
 ('outcome','stagnant'),('outcome','unhandled_screen'),('outcome','stuck'),('outcome','defeated'),('outcome','error'),('outcome','completed'),
 ('precheck','treasury_floor'),('precheck','cannot_equip'),('precheck','units_panel_not_open_CTD_guard');
```

Open-vocabulary but bounded text values that today are stored as strings on millions of rows get their own small dictionaries (each is `id, key`): `dict.action_type` (32 values, M2 A.3), `dict.action` (`action_id INTEGER`, `(action_type_id, action_key)` UNIQUE; ≈ 700k rows incl. 626k move coordinates, M2 B), `dict.confirm_signal` (24), `dict.rite_reason` (44 distinct loc/markup strings incl. '', M1 A.6), `dict.game_version` (1), `dict.selector` (7), `dict.hostile_faction_name`? — no: hostile factions are `dict.faction`.

```sql
CREATE TABLE dict.action_type (id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, key TEXT NOT NULL UNIQUE);
CREATE TABLE dict.action (
  action_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  action_type_id SMALLINT NOT NULL REFERENCES dict.action_type,
  action_key     TEXT NOT NULL,
  UNIQUE (action_type_id, action_key)
);
CREATE TABLE dict.confirm_signal (id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, key TEXT NOT NULL UNIQUE);
CREATE TABLE dict.rite_reason   (id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, key TEXT NOT NULL UNIQUE);
CREATE TABLE dict.game_version  (id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, key TEXT NOT NULL UNIQUE);
CREATE TABLE dict.selector      (id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, key TEXT NOT NULL UNIQUE);
CREATE TABLE dict.occupation_option (id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, key TEXT NOT NULL UNIQUE);
INSERT INTO dict.rite_reason (key) VALUES ('');
```

## 3.2 Spine: state_set, campaign, collector version, snapshot, decision, interrupt

`state_set` is the shared identity table for every deduplicated collection (2.7); members live in the per-collection tables of 03b §3.5.

```sql
CREATE TABLE corpus.state_set (
  set_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind     SMALLINT NOT NULL,
  hash     BYTEA NOT NULL,
  n        SMALLINT NOT NULL,
  UNIQUE (kind, hash),
  CHECK (octet_length(hash) = 32),
  CHECK (n >= 0)
);

CREATE TABLE corpus.collector_version (
  version_id           SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  collector_sha        TEXT NOT NULL UNIQUE,
  git_sha              TEXT,
  started_ts           DOUBLE PRECISION,
  note                 TEXT,
  emits_campaign_meta  BOOLEAN NOT NULL,
  emits_pending_queue  BOOLEAN NOT NULL,
  emits_v31_block      BOOLEAN NOT NULL,
  emits_missions       BOOLEAN NOT NULL
);
INSERT INTO corpus.collector_version (collector_sha, note, emits_campaign_meta, emits_pending_queue, emits_v31_block, emits_missions) VALUES
 ('legacy:meta0:pq0', 'sentinel: TW_CODE_VERSION unset, no difficulty/leader/selector, no pending_queue', false, false, false, false),
 ('legacy:meta1:pq0', 'sentinel: TW_CODE_VERSION unset, no pending_queue', true, false, false, false),
 ('legacy:meta1:pq1', 'sentinel: TW_CODE_VERSION unset', true, true, false, false);

CREATE TABLE corpus.campaign (
  campaign_id        INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_key       TEXT NOT NULL UNIQUE,
  faction_id         SMALLINT NOT NULL REFERENCES dict.faction,
  campaign_map_id    SMALLINT REFERENCES dict.campaign_map,
  presave_radius     SMALLINT,
  selector_id        SMALLINT REFERENCES dict.selector,
  difficulty         SMALLINT,
  leader             TEXT,
  picked_ts          DOUBLE PRECISION,
  ucb_pick_id        INTEGER,
  segment_id         INTEGER,
  first_snapshot_id  BIGINT,
  last_snapshot_id   BIGINT,
  first_ts           DOUBLE PRECISION,
  last_ts            DOUBLE PRECISION,
  turns              SMALLINT NOT NULL DEFAULT 0,
  n_decisions        INTEGER NOT NULL DEFAULT 0,
  n_interrupts       INTEGER NOT NULL DEFAULT 0,
  n_taken            INTEGER NOT NULL DEFAULT 0,
  n_counted          INTEGER NOT NULL DEFAULT 0,
  first_settlements  SMALLINT,
  peak_settlements   SMALLINT,
  first_lord_level   SMALLINT,
  peak_lord_level    SMALLINT,
  allies_max         SMALLINT,
  vassals_max        SMALLINT,
  outcome_id         SMALLINT REFERENCES dict.enum,
  defeated           BOOLEAN,
  CHECK (presave_radius IS NULL OR presave_radius > 0)
) WITH (fillfactor = 80);
CREATE INDEX campaign_faction_map ON corpus.campaign (campaign_map_id, faction_id, campaign_id);
CREATE INDEX campaign_first_ts ON corpus.campaign (first_ts DESC NULLS LAST);
CREATE INDEX campaign_first_snapshot ON corpus.campaign (first_snapshot_id DESC NULLS LAST);

CREATE TABLE corpus.snapshot (
  snapshot_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id   INTEGER NOT NULL REFERENCES corpus.campaign,
  kind_id       SMALLINT NOT NULL REFERENCES dict.enum,
  ts            DOUBLE PRECISION NOT NULL,
  turn          SMALLINT NOT NULL,
  version_id    SMALLINT NOT NULL REFERENCES corpus.collector_version,
  CHECK (turn >= 0)
);
CREATE INDEX snapshot_campaign_ts ON corpus.snapshot (campaign_id, ts, snapshot_id);
CREATE INDEX snapshot_campaign_turn ON corpus.snapshot (campaign_id, turn, snapshot_id) INCLUDE (kind_id);
CREATE INDEX snapshot_ts ON corpus.snapshot (ts);

CREATE TABLE corpus.decision (
  decision_id     BIGINT PRIMARY KEY REFERENCES corpus.snapshot,
  decision_uuid   UUID NOT NULL UNIQUE,
  n_entities      SMALLINT NOT NULL,
  n_offers        SMALLINT,
  CHECK (n_entities BETWEEN 1 AND 64)
) WITH (fillfactor = 80);

CREATE TABLE corpus.decision_timing (
  decision_id        BIGINT PRIMARY KEY REFERENCES corpus.decision,
  t_request          DOUBLE PRECISION NOT NULL,
  t_received         DOUBLE PRECISION NOT NULL,
  collect_ms         INTEGER NOT NULL,
  store_ms           INTEGER NOT NULL,
  pickup_lag_ms      INTEGER NOT NULL,
  roundtrip_ms       INTEGER NOT NULL,
  trace_ms           INTEGER NOT NULL,
  score_ms           INTEGER NOT NULL,
  housekeep_ms       INTEGER,
  hk_hud_check_ms    INTEGER,
  hk_generate_ms     INTEGER,
  hk_pick_log_ms     INTEGER,
  hk_verify_log_ms   INTEGER,
  hk_active_from_ms  INTEGER,
  hk_post_attack_ms  INTEGER,
  hk_drain_ms        INTEGER,
  hk_resolve_ms      INTEGER
);

CREATE TABLE corpus.interrupt (
  interrupt_id        BIGINT PRIMARY KEY REFERENCES corpus.snapshot,
  prev_decision_id    BIGINT REFERENCES corpus.decision,
  ts_recorded         DOUBLE PRECISION NOT NULL,
  state_at_id         SMALLINT NOT NULL REFERENCES dict.enum,
  kind_id             SMALLINT NOT NULL REFERENCES dict.enum,
  root                TEXT NOT NULL,
  dilemma_id          SMALLINT REFERENCES dict.dilemma,
  incident_id         SMALLINT REFERENCES dict.incident,
  root_context        TEXT,
  region_id           SMALLINT REFERENCES dict.region,
  chosen              TEXT NOT NULL,
  answer              TEXT,
  policy_id           SMALLINT REFERENCES dict.enum,
  executed            BOOLEAN,
  confirmed           BOOLEAN,
  counted             BOOLEAN,
  refusal_id          SMALLINT REFERENCES dict.enum,
  latency_ms          INTEGER NOT NULL,
  legacy_interrupt_id INTEGER UNIQUE,
  CHECK (counted IS NULL OR counted = (executed AND confirmed))
);
CREATE INDEX interrupt_prev_decision ON corpus.interrupt (prev_decision_id);
CREATE INDEX interrupt_kind ON corpus.interrupt (kind_id, interrupt_id);
```

`snapshot.kind_id` + subtype tables: a decision row exists iff kind = decision (enforced by the writer's single transaction and by the verification query in 12; a CHECK across tables is not expressible). `interrupt.chosen` is the option key (button id or dilemma record id, R4 B.2); `root_context` keeps the raw record prefix for dilemma/war_declared/event_ack/declare_war_cancel; `dilemma_id`/`incident_id` hold the resolved key (M3 B: 97.3%; the 4 commonprefix artefacts keep `root_context` only and a `dict.dilemma` non-reference row).

## 3.3 Captured campaign scalars and world (both snapshot kinds)

```sql
CREATE TABLE corpus.snapshot_campaign (
  snapshot_id        BIGINT PRIMARY KEY REFERENCES corpus.snapshot,
  faction_id         SMALLINT NOT NULL REFERENCES dict.faction,
  faction_cqi        SMALLINT NOT NULL,
  turn               SMALLINT NOT NULL,
  income             INTEGER,
  settlements        SMALLINT,
  treasury           INTEGER,
  is_researching     BOOLEAN NOT NULL,
  armies             SMALLINT,
  lord_level         SMALLINT,
  allies             SMALLINT,
  vassals            SMALLINT,
  power_rank         SMALLINT,
  ll_wounded         BOOLEAN,
  game_version_id    SMALLINT REFERENCES dict.game_version,
  defeated           BOOLEAN,
  difficulty         SMALLINT,
  leader             TEXT,
  selector_id        SMALLINT REFERENCES dict.selector,
  presave_radius     SMALLINT,
  campaign_map_id    SMALLINT REFERENCES dict.campaign_map,
  eval_ms            SMALLINT,
  resource_set_id    BIGINT REFERENCES corpus.state_set,
  hero_count_set_id  BIGINT REFERENCES corpus.state_set,
  effect_bundle_set_id BIGINT REFERENCES corpus.state_set,
  CHECK (power_rank <= 0)
);
CREATE INDEX snapshot_campaign_scalars ON corpus.snapshot_campaign (snapshot_id) INCLUDE (turn, settlements, lord_level, allies, vassals, income, power_rank);

CREATE TABLE corpus.snapshot_read_failure (
  snapshot_id  BIGINT NOT NULL REFERENCES corpus.snapshot,
  message      TEXT NOT NULL,
  n            INTEGER NOT NULL,
  PRIMARY KEY (snapshot_id, message)
);

CREATE TABLE corpus.snapshot_world (
  snapshot_id          BIGINT PRIMARY KEY REFERENCES corpus.snapshot,
  region_set_id        BIGINT REFERENCES corpus.state_set,
  settlement_set_id    BIGINT REFERENCES corpus.state_set,
  ruin_set_id          BIGINT REFERENCES corpus.state_set,
  enemy_agent_set_id   BIGINT REFERENCES corpus.state_set,
  war_graph_set_id     BIGINT REFERENCES corpus.state_set,
  relation_set_id      BIGINT REFERENCES corpus.state_set,
  stationed_set_id     BIGINT REFERENCES corpus.state_set,
  citizenry            INTEGER[],
  diplo_unseen         SMALLINT[],
  diplo_schema         SMALLINT,
  diplo_hostile_rows   SMALLINT,
  reach_char_cqis      INTEGER[],
  reach_region_ids     SMALLINT[]
);

CREATE TABLE corpus.world_army (
  snapshot_id       BIGINT NOT NULL REFERENCES corpus.snapshot,
  ord               SMALLINT NOT NULL,
  cqi               INTEGER NOT NULL,
  subtype_id        SMALLINT REFERENCES dict.agent_subtype,
  agent_type_id     SMALLINT REFERENCES dict.agent_type,
  is_leader         BOOLEAN,
  has_army          BOOLEAN,
  is_general        BOOLEAN,
  rank              SMALLINT,
  x                 INTEGER,
  y                 INTEGER,
  ap_pct            DOUBLE PRECISION,
  stance_id         SMALLINT REFERENCES dict.stance,
  hp                DOUBLE PRECISION,
  units             SMALLINT,
  region_owner_id   SMALLINT REFERENCES dict.faction,
  region_id         SMALLINT REFERENCES dict.region,
  province_id       SMALLINT REFERENCES dict.province,
  in_own_territory  BOOLEAN NOT NULL,
  ap_remaining      INTEGER,
  ap_per_turn       INTEGER,
  PRIMARY KEY (snapshot_id, ord),
  CHECK (x IS NULL OR x BETWEEN 0 AND 65535),
  CHECK (y IS NULL OR y BETWEEN 0 AND 65535)
);

CREATE TABLE corpus.world_hostile (
  snapshot_id         BIGINT NOT NULL REFERENCES corpus.snapshot,
  ord                 SMALLINT NOT NULL,
  kind_id             SMALLINT NOT NULL REFERENCES dict.enum,
  faction_id          SMALLINT NOT NULL REFERENCES dict.faction,
  visible             BOOLEAN,
  cqi                 INTEGER,
  subtype_id          SMALLINT REFERENCES dict.agent_subtype,
  agent_type_id       SMALLINT REFERENCES dict.agent_type,
  province_id         SMALLINT REFERENCES dict.province,
  region_id           SMALLINT REFERENCES dict.region,
  x                   INTEGER NOT NULL,
  y                   INTEGER NOT NULL,
  dist                INTEGER NOT NULL,
  is_armed_citizenry  BOOLEAN,
  units               SMALLINT,
  hp                  DOUBLE PRECISION,
  stance_id           SMALLINT REFERENCES dict.stance,
  PRIMARY KEY (snapshot_id, ord)
);
```

`world_hostile` row shapes (R1 B.3, M1 A.2): settlement rows populate `faction, region, units, x, y, dist` (`visible` NULL); army rows populate `faction, visible, cqi, is_armed_citizenry, units, hp, stance, province, x, y, dist`; hero rows `faction, visible, cqi, subtype, agent_type, province, x, y, dist`. Hydration emits exactly the key set of the kind; a column that is NULL inside its kind's key set is emitted as JSON null (e.g. army `province` null on 95 rows). `snapshot_world` columns beyond the six interrupt-world keys are NULL for interrupt snapshots and not emitted (2.8).

## 3.4 Entities: character identity, per-snapshot entity list, character/province/campaign state

```sql
CREATE TABLE corpus.character (
  character_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id        INTEGER NOT NULL REFERENCES corpus.campaign,
  cqi                INTEGER NOT NULL,
  first_snapshot_id  BIGINT NOT NULL,
  last_snapshot_id   BIGINT NOT NULL,
  UNIQUE (campaign_id, cqi)
) WITH (fillfactor = 80);

CREATE TABLE corpus.snapshot_entity (
  snapshot_id   BIGINT NOT NULL REFERENCES corpus.snapshot,
  entity_seq    SMALLINT NOT NULL,
  kind_id       SMALLINT NOT NULL REFERENCES dict.enum,
  character_id  INTEGER REFERENCES corpus.character,
  region_id     SMALLINT REFERENCES dict.region,
  PRIMARY KEY (snapshot_id, entity_seq),
  CHECK ((character_id IS NOT NULL)::int + (region_id IS NOT NULL)::int <= 1)
);
CREATE INDEX snapshot_entity_character ON corpus.snapshot_entity (character_id, snapshot_id) WHERE character_id IS NOT NULL;
CREATE INDEX snapshot_entity_region ON corpus.snapshot_entity (region_id, snapshot_id) WHERE region_id IS NOT NULL;

CREATE TABLE corpus.char_state (
  snapshot_id             BIGINT NOT NULL REFERENCES corpus.snapshot,
  character_id            INTEGER NOT NULL REFERENCES corpus.character,
  entity_seq              SMALLINT NOT NULL,
  is_hero                 BOOLEAN NOT NULL,
  rank                    SMALLINT NOT NULL,
  skill_points            SMALLINT NOT NULL,
  units                   SMALLINT,
  pending_recruits        SMALLINT NOT NULL,
  ap_pct                  DOUBLE PRECISION,
  ap_remaining            INTEGER,
  ap_per_turn             INTEGER,
  hp                      DOUBLE PRECISION,
  loyalty                 SMALLINT,
  stance_id               SMALLINT NOT NULL REFERENCES dict.stance,
  subtype_id              SMALLINT REFERENCES dict.agent_subtype,
  region_id               SMALLINT REFERENCES dict.region,
  x                       INTEGER,
  y                       INTEGER,
  garrisoned              BOOLEAN NOT NULL,
  besieging               BOOLEAN NOT NULL,
  acted                   BOOLEAN NOT NULL,
  is_leader               BOOLEAN NOT NULL,
  wounded                 BOOLEAN,
  is_agent                BOOLEAN,
  can_embed               BOOLEAN,
  agent_type_id           SMALLINT REFERENCES dict.agent_type,
  skill_set_id            BIGINT NOT NULL REFERENCES corpus.state_set,
  hidden_skill_ids        SMALLINT[] NOT NULL,
  trait_set_id            BIGINT NOT NULL REFERENCES corpus.state_set,
  stance_set_id           BIGINT REFERENCES corpus.state_set,
  recruitable_set_id      BIGINT REFERENCES corpus.state_set,
  unit_card_set_id        BIGINT NOT NULL REFERENCES corpus.state_set,
  pending_queue_set_id    BIGINT REFERENCES corpus.state_set,
  pending_recruit_unit_ids SMALLINT[] NOT NULL,
  equipped_set_id         BIGINT NOT NULL REFERENCES corpus.state_set,
  horde_slot_set_id       BIGINT REFERENCES corpus.state_set,
  merc_pool_set_id        BIGINT REFERENCES corpus.state_set,
  trait_progress_set_id   BIGINT NOT NULL REFERENCES corpus.state_set,
  reach_chars_true        INTEGER[] NOT NULL,
  reach_setts_true        SMALLINT[] NOT NULL,
  move_x                  INTEGER[] NOT NULL,
  move_y                  INTEGER[] NOT NULL,
  reach_rays              SMALLINT[],
  reach_max               SMALLINT,
  PRIMARY KEY (snapshot_id, character_id),
  UNIQUE (snapshot_id, entity_seq),
  CHECK (cardinality(move_x) = cardinality(move_y)),
  CHECK (reach_rays IS NULL OR cardinality(reach_rays) = 8),
  CHECK (x IS NULL OR x BETWEEN 0 AND 65535),
  CHECK (y IS NULL OR y BETWEEN 0 AND 65535),
  CHECK (is_hero = (is_agent IS NOT NULL))
);
CREATE INDEX char_state_character ON corpus.char_state (character_id, snapshot_id DESC);

CREATE TABLE corpus.char_state_ext (
  snapshot_id                 BIGINT NOT NULL,
  character_id                INTEGER NOT NULL,
  xp                          INTEGER NOT NULL,
  xp_next_level               INTEGER NOT NULL,
  subterfuge                  SMALLINT NOT NULL,
  zeal                        SMALLINT NOT NULL,
  authority                   SMALLINT NOT NULL,
  resurrection_turns          SMALLINT NOT NULL,
  upkeep                      INTEGER NOT NULL,
  background_skill_id         SMALLINT REFERENCES dict.skill,
  hidden_skill_state_set_id   BIGINT NOT NULL REFERENCES corpus.state_set,
  effect_bundle_set_id        BIGINT NOT NULL REFERENCES corpus.state_set,
  force_effect_bundle_set_id  BIGINT NOT NULL REFERENCES corpus.state_set,
  armory_item_ids             SMALLINT[] NOT NULL,
  PRIMARY KEY (snapshot_id, character_id),
  FOREIGN KEY (snapshot_id, character_id) REFERENCES corpus.char_state
);

CREATE TABLE corpus.province_state (
  snapshot_id           BIGINT NOT NULL REFERENCES corpus.snapshot,
  region_id             SMALLINT NOT NULL REFERENCES dict.region,
  entity_seq            SMALLINT NOT NULL,
  settlement_present    BOOLEAN NOT NULL,
  province_id           SMALLINT REFERENCES dict.province,
  complete_owner        BOOLEAN,
  max_slots             SMALLINT,
  free_slots            SMALLINT,
  can_set_edict         BOOLEAN,
  selected_edict_id     SMALLINT REFERENCES dict.edict,
  active_edict_id       SMALLINT REFERENCES dict.edict,
  public_order          SMALLINT,
  buildings             SMALLINT,
  is_capital            BOOLEAN,
  settlement_level      SMALLINT,
  growth_per_turn       INTEGER,
  gross_income          INTEGER,
  development_points    SMALLINT,
  income                INTEGER,
  has_port              BOOLEAN,
  has_walls             BOOLEAN,
  built_set_id          BIGINT REFERENCES corpus.state_set,
  locked_slots          SMALLINT[],
  building_now_set_id   BIGINT REFERENCES corpus.state_set,
  corruption_set_id     BIGINT REFERENCES corpus.state_set,
  buildable_set_id      BIGINT REFERENCES corpus.state_set,
  edict_ids             SMALLINT[],
  slot_state_set_id     BIGINT REFERENCES corpus.state_set,
  effect_bundle_set_id  BIGINT REFERENCES corpus.state_set,
  plague_bundle_set_id  BIGINT REFERENCES corpus.state_set,
  PRIMARY KEY (snapshot_id, region_id),
  UNIQUE (snapshot_id, entity_seq),
  CHECK (settlement_present OR province_id IS NULL)
);
CREATE INDEX province_state_region ON corpus.province_state (region_id, snapshot_id DESC);

CREATE TABLE corpus.campaign_state (
  snapshot_id          BIGINT PRIMARY KEY REFERENCES corpus.snapshot,
  entity_seq           SMALLINT NOT NULL,
  tech_set_id          BIGINT NOT NULL REFERENCES corpus.state_set,
  rite_set_id          BIGINT NOT NULL REFERENCES corpus.state_set,
  lord_pool_set_id     BIGINT NOT NULL REFERENCES corpus.state_set,
  anc_pool_set_id      BIGINT NOT NULL REFERENCES corpus.state_set,
  equipped_all_set_id  BIGINT NOT NULL REFERENCES corpus.state_set,
  mission_set_id       BIGINT REFERENCES corpus.state_set,
  current_research_id  SMALLINT REFERENCES dict.tech_node,
  research_points      SMALLINT
);
```

Column notes (data profile → constraint): `char_state.units`/`hp`/`loyalty` NULL only for heroes (M1 A.4), `skill_points 0..8`, `rank 1..12`, `loyalty 0..9`, `ap_per_turn 0..4723`; `province_state` nullable scalars are the 33 settlement-less rows (M1 A.5); `selected_edict_id` NULL ⇔ literal `'none'`, `active_edict_id` NULL ⇔ `''` (the two collector literals are hydrated per column); `edict_ids` NULL never occurs (list always present) — NOT NULL is not declared only because the empty list and the 33 degenerate rows are both `'{}'`; `is_hero` distinguishes the `_parse_hero_blob` key set (46 keys) from the lord key set (47) at hydration. `char_state.hidden_skill_ids`/`pending_recruit_unit_ids`/`reach_*`/`move_*` arrays are on the row because they are pure id/coordinate lists with mean length ≤ 14 and they change often (reach 72-81% same, move 3-5%: change_rate.out) — a set table would cost a 24-byte header per element instead of 2-4 bytes.

Set-typed columns reference `corpus.state_set` (3.2); the member tables per set kind are in 03b §3.5 together with the per-collection storage decision table.
