CREATE SCHEMA dict;
CREATE SCHEMA corpus;
CREATE SCHEMA ref;
CREATE SCHEMA ops;
CREATE SCHEMA analytics2;
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
        id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        key           TEXT NOT NULL UNIQUE,
        is_reference  BOOLEAN NOT NULL,
        ref_build_id  INTEGER,
        note          TEXT,
        CHECK (is_reference = (ref_build_id IS NOT NULL))
      )$q$, f);
  END LOOP;
END $$;
CREATE TABLE dict.enum (
  enum_id   INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  domain    TEXT NOT NULL,
  key       TEXT NOT NULL,
  UNIQUE (domain, key)
);
CREATE TABLE dict.action_type (
  id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key TEXT NOT NULL UNIQUE
);
CREATE TABLE dict.action (
  action_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  action_type_id INTEGER NOT NULL,
  action_key     TEXT NOT NULL,
  UNIQUE (action_type_id, action_key)
);
CREATE TABLE dict.confirm_signal (
  id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key TEXT NOT NULL UNIQUE
);
CREATE TABLE dict.rite_reason (
  id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key TEXT NOT NULL UNIQUE
);
CREATE TABLE dict.game_version (
  id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key TEXT NOT NULL UNIQUE
);
CREATE TABLE dict.selector (
  id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key TEXT NOT NULL UNIQUE
);
CREATE TABLE dict.occupation_option (
  id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key TEXT NOT NULL UNIQUE
);
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
CREATE TABLE corpus.campaign (
  campaign_id        INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_key       TEXT NOT NULL UNIQUE,
  faction_id         INTEGER NOT NULL,
  campaign_map_id    INTEGER,
  presave_radius     SMALLINT,
  selector_id        INTEGER,
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
  outcome_id         INTEGER,
  defeated           BOOLEAN,
  CHECK (presave_radius IS NULL OR presave_radius > 0)
)WITH (fillfactor = 80);
CREATE TABLE corpus.snapshot (
  snapshot_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id   INTEGER NOT NULL,
  kind_id       INTEGER NOT NULL,
  ts            DOUBLE PRECISION NOT NULL,
  turn          SMALLINT NOT NULL,
  version_id    SMALLINT NOT NULL,
  CHECK (turn >= 0)
);
CREATE TABLE corpus.decision (
  decision_id     BIGINT PRIMARY KEY,
  decision_uuid   UUID NOT NULL UNIQUE,
  n_entities      SMALLINT NOT NULL,
  n_offers        SMALLINT,
  CHECK (n_entities BETWEEN 1 AND 64)
)WITH (fillfactor = 80);
CREATE TABLE corpus.decision_timing (
  decision_id        BIGINT PRIMARY KEY,
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
  interrupt_id        BIGINT PRIMARY KEY,
  prev_decision_id    BIGINT,
  ts_recorded         DOUBLE PRECISION NOT NULL,
  state_at_id         INTEGER NOT NULL,
  kind_id             INTEGER NOT NULL,
  root                TEXT NOT NULL,
  dilemma_id          INTEGER,
  incident_id         INTEGER,
  root_context        TEXT,
  region_id           INTEGER,
  chosen              TEXT NOT NULL,
  answer              TEXT,
  policy_id           INTEGER,
  executed            BOOLEAN,
  confirmed           BOOLEAN,
  counted             BOOLEAN,
  refusal_id          INTEGER,
  latency_ms          INTEGER NOT NULL,
  legacy_interrupt_id INTEGER UNIQUE,
  CHECK (counted IS NULL OR counted = (executed AND confirmed))
);
CREATE TABLE corpus.snapshot_campaign (
  snapshot_id        BIGINT PRIMARY KEY,
  faction_id         INTEGER NOT NULL,
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
  game_version_id    INTEGER,
  defeated           BOOLEAN,
  difficulty         SMALLINT,
  leader             TEXT,
  selector_id        INTEGER,
  presave_radius     SMALLINT,
  campaign_map_id    INTEGER,
  eval_ms            SMALLINT,
  resource_set_id    BIGINT,
  hero_count_set_id  BIGINT,
  effect_bundle_set_id BIGINT,
  CHECK (power_rank <= 0)
);
CREATE TABLE corpus.snapshot_read_failure (
  snapshot_id  BIGINT NOT NULL,
  message      TEXT NOT NULL,
  n            INTEGER NOT NULL,
  PRIMARY KEY (snapshot_id, message)
);
CREATE TABLE corpus.snapshot_world (
  snapshot_id          BIGINT PRIMARY KEY,
  region_set_id        BIGINT,
  settlement_set_id    BIGINT,
  ruin_set_id          BIGINT,
  enemy_agent_set_id   BIGINT,
  war_graph_set_id     BIGINT,
  relation_set_id      BIGINT,
  stationed_set_id     BIGINT,
  citizenry            INTEGER[],
  diplo_unseen         INTEGER[],
  diplo_schema         SMALLINT,
  diplo_hostile_rows   SMALLINT,
  reach_char_cqis      INTEGER[],
  reach_region_ids     INTEGER[]
);
CREATE TABLE corpus.world_army (
  snapshot_id       BIGINT NOT NULL,
  ord               SMALLINT NOT NULL,
  cqi               INTEGER,
  subtype_id        INTEGER,
  agent_type_id     INTEGER,
  is_leader         BOOLEAN,
  has_army          BOOLEAN,
  is_general        BOOLEAN,
  rank              SMALLINT,
  x                 INTEGER,
  y                 INTEGER,
  ap_pct            DOUBLE PRECISION,
  stance_id         INTEGER,
  hp                DOUBLE PRECISION,
  units             SMALLINT,
  region_owner_id   INTEGER,
  region_id         INTEGER,
  province_id       INTEGER,
  in_own_territory  BOOLEAN NOT NULL,
  ap_remaining      INTEGER,
  ap_per_turn       INTEGER,
  PRIMARY KEY (snapshot_id, ord),
  CHECK (x IS NULL OR x BETWEEN 0 AND 65535),
  CHECK (y IS NULL OR y BETWEEN 0 AND 65535)
);
CREATE TABLE corpus.world_hostile (
  snapshot_id         BIGINT NOT NULL,
  ord                 SMALLINT NOT NULL,
  kind_id             INTEGER NOT NULL,
  faction_id          INTEGER NOT NULL,
  visible             BOOLEAN,
  cqi                 INTEGER,
  subtype_id          INTEGER,
  agent_type_id       INTEGER,
  province_id         INTEGER,
  region_id           INTEGER,
  x                   INTEGER NOT NULL,
  y                   INTEGER NOT NULL,
  dist                INTEGER NOT NULL,
  is_armed_citizenry  BOOLEAN,
  units               SMALLINT,
  hp                  DOUBLE PRECISION,
  stance_id           INTEGER,
  PRIMARY KEY (snapshot_id, ord)
);
CREATE TABLE corpus.character (
  character_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id        INTEGER NOT NULL,
  cqi                INTEGER NOT NULL,
  first_snapshot_id  BIGINT NOT NULL,
  last_snapshot_id   BIGINT NOT NULL,
  UNIQUE (campaign_id, cqi)
)WITH (fillfactor = 80);
CREATE TABLE corpus.snapshot_entity (
  snapshot_id   BIGINT NOT NULL,
  entity_seq    SMALLINT NOT NULL,
  kind_id       INTEGER NOT NULL,
  character_id  INTEGER,
  region_id     INTEGER,
  PRIMARY KEY (snapshot_id, entity_seq),
  CHECK ((character_id IS NOT NULL)::int + (region_id IS NOT NULL)::int <= 1)
);
CREATE TABLE corpus.char_state (
  snapshot_id             BIGINT NOT NULL,
  character_id            INTEGER NOT NULL,
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
  stance_id               INTEGER NOT NULL,
  subtype_id              INTEGER,
  region_id               INTEGER,
  x                       INTEGER,
  y                       INTEGER,
  garrisoned              BOOLEAN NOT NULL,
  besieging               BOOLEAN NOT NULL,
  acted                   BOOLEAN NOT NULL,
  is_leader               BOOLEAN NOT NULL,
  wounded                 BOOLEAN,
  is_agent                BOOLEAN,
  can_embed               BOOLEAN,
  agent_type_id           INTEGER,
  skill_set_id            BIGINT NOT NULL,
  hidden_skill_ids        INTEGER[] NOT NULL,
  stance_set_id           BIGINT,
  recruitable_set_id      BIGINT,
  unit_card_set_id        BIGINT NOT NULL,
  pending_queue_set_id    BIGINT,
  pending_recruit_unit_ids INTEGER[] NOT NULL,
  equipped_set_id         BIGINT NOT NULL,
  horde_slot_set_id       BIGINT,
  merc_pool_set_id        BIGINT,
  reach_chars_true        INTEGER[] NOT NULL,
  reach_setts_true        INTEGER[] NOT NULL,
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
CREATE TABLE corpus.char_state_ext (
  snapshot_id                 BIGINT NOT NULL,
  character_id                INTEGER NOT NULL,
  trait_set_id                BIGINT,
  trait_progress_set_id       BIGINT,
  xp                          INTEGER NOT NULL,
  xp_next_level               INTEGER NOT NULL,
  subterfuge                  SMALLINT NOT NULL,
  zeal                        SMALLINT NOT NULL,
  authority                   SMALLINT NOT NULL,
  resurrection_turns          SMALLINT NOT NULL,
  upkeep                      INTEGER NOT NULL,
  background_skill_id         INTEGER,
  hidden_skill_state_set_id   BIGINT NOT NULL,
  effect_bundle_set_id        BIGINT NOT NULL,
  force_effect_bundle_set_id  BIGINT NOT NULL,
  armory_item_ids             INTEGER[] NOT NULL,
  PRIMARY KEY (snapshot_id, character_id)
);
CREATE TABLE corpus.province_state (
  snapshot_id           BIGINT NOT NULL,
  region_id             INTEGER NOT NULL,
  entity_seq            SMALLINT NOT NULL,
  settlement_present    BOOLEAN NOT NULL,
  province_id           INTEGER,
  complete_owner        BOOLEAN,
  max_slots             SMALLINT,
  free_slots            SMALLINT,
  can_set_edict         BOOLEAN,
  selected_edict_id     INTEGER,
  active_edict_id       INTEGER,
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
  built_set_id          BIGINT,
  locked_slots          SMALLINT[],
  building_now_set_id   BIGINT,
  corruption_set_id     BIGINT,
  buildable_set_id      BIGINT,
  edict_ids             INTEGER[],
  slot_state_set_id     BIGINT,
  effect_bundle_set_id  BIGINT,
  plague_bundle_set_id  BIGINT,
  PRIMARY KEY (snapshot_id, region_id),
  UNIQUE (snapshot_id, entity_seq),
  CHECK (settlement_present OR province_id IS NULL)
);
CREATE TABLE corpus.campaign_state (
  snapshot_id          BIGINT PRIMARY KEY,
  entity_seq           SMALLINT NOT NULL,
  tech_set_id          BIGINT NOT NULL,
  rite_set_id          BIGINT NOT NULL,
  lord_pool_set_id     BIGINT NOT NULL,
  anc_pool_set_id      BIGINT NOT NULL,
  equipped_all_set_id  BIGINT NOT NULL,
  mission_set_id       BIGINT,
  current_research_id  INTEGER,
  research_points      SMALLINT
);
CREATE TABLE corpus.skill_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  skill_id INTEGER NOT NULL,
  status_id INTEGER NOT NULL,
  level SMALLINT NOT NULL,
  total_levels SMALLINT NOT NULL,
  tier SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, skill_id)
);
CREATE TABLE corpus.stance_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  stance_id INTEGER NOT NULL,
  active BOOLEAN NOT NULL,
  can_activate BOOLEAN NOT NULL,
  can_afford BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, stance_id)
);
CREATE TABLE corpus.recruitable_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  unit_id INTEGER NOT NULL,
  state TEXT NOT NULL,
  cost INTEGER NOT NULL,
  disabled BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, unit_id)
);
CREATE TABLE corpus.hidden_skill_state_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  skill_id INTEGER NOT NULL,
  level SMALLINT NOT NULL,
  total_levels SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.trait_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  trait_id INTEGER NOT NULL,
  level SMALLINT NOT NULL,
  threshold_points SMALLINT NOT NULL,
  points SMALLINT NOT NULL,
  chaos_realm BOOLEAN NOT NULL,
  level_key_id INTEGER NOT NULL,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.trait_progress_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  trait_id INTEGER NOT NULL,
  points SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, trait_id)
);
CREATE TABLE corpus.item_slot_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  index SMALLINT NOT NULL,
  ancillary_id INTEGER,
  name TEXT NOT NULL,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.horde_slot_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  slot_index SMALLINT NOT NULL,
  slot_id TEXT NOT NULL,
  building_id INTEGER NOT NULL,
  empty BOOLEAN NOT NULL,
  available BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, slot_index, building_id)
);
CREATE TABLE corpus.merc_pool_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  action_id INTEGER NOT NULL,
  unit_id INTEGER NOT NULL,
  avail SMALLINT NOT NULL,
  cost INTEGER NOT NULL,
  can BOOLEAN,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, action_id, unit_id)
);
CREATE TABLE corpus.pending_queue_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  unit_id INTEGER NOT NULL,
  turns_left SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.effect_bundle_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  effect_bundle_id INTEGER NOT NULL,
  turns_remaining SMALLINT,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.unit_card_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  unit_id INTEGER NOT NULL,
  strength_pct DOUBLE PRECISION NOT NULL,
  category_id INTEGER NOT NULL,
  xp SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.buildable_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  slot_index SMALLINT,
  building_id INTEGER NOT NULL,
  active BOOLEAN NOT NULL,
  empty BOOLEAN NOT NULL,
  can_upgrade BOOLEAN NOT NULL,
  cost INTEGER NOT NULL,
  upkeep INTEGER NOT NULL,
  level SMALLINT NOT NULL,
  can_afford_resources BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.slot_state_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  index SMALLINT NOT NULL,
  damaged BOOLEAN NOT NULL,
  can_repair BOOLEAN NOT NULL,
  repairing BOOLEAN NOT NULL,
  can_dismantle BOOLEAN NOT NULL,
  refund INTEGER,
  queued BOOLEAN NOT NULL,
  queued_building_id INTEGER,
  empty BOOLEAN NOT NULL,
  building_id INTEGER,
  health SMALLINT,
  max_health SMALLINT,
  ruined BOOLEAN NOT NULL,
  repair_cost INTEGER NOT NULL,
  upgrading BOOLEAN NOT NULL,
  dismantling BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, index)
);
CREATE TABLE corpus.built_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  slot_index SMALLINT NOT NULL,
  building_id INTEGER NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, slot_index)
);
CREATE TABLE corpus.building_now_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  slot_index SMALLINT NOT NULL,
  building_id INTEGER NOT NULL,
  turns_left SMALLINT NOT NULL,
  paused BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, slot_index)
);
CREATE TABLE corpus.resource_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  resource_id INTEGER NOT NULL,
  value INTEGER,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, resource_id)
);
CREATE TABLE corpus.hero_count_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  agent_type_id INTEGER NOT NULL,
  n SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, agent_type_id)
);
CREATE TABLE corpus.tech_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  tech_node_id INTEGER NOT NULL,
  researched BOOLEAN NOT NULL,
  can_research BOOLEAN NOT NULL,
  cost INTEGER NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, tech_node_id)
);
CREATE TABLE corpus.rite_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  index SMALLINT NOT NULL,
  ritual_id INTEGER NOT NULL,
  can_perform BOOLEAN NOT NULL,
  reason_id INTEGER,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, index)
);
CREATE TABLE corpus.lord_pool_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  subtype_id INTEGER NOT NULL,
  n SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, subtype_id)
);
CREATE TABLE corpus.lord_pool_candidate (
  set_id BIGINT NOT NULL,
  subtype_id SMALLINT NOT NULL,
  ord SMALLINT NOT NULL,
  can BOOLEAN NOT NULL,
  agent BOOLEAN,
  bg_skill_id INTEGER,
  cand_subtype_id INTEGER NOT NULL,
  trait_ids INTEGER[],
  PRIMARY KEY (set_id, subtype_id, ord)
);
CREATE TABLE corpus.mission_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  mission_id INTEGER NOT NULL,
  status_id INTEGER NOT NULL,
  turns_remaining SMALLINT,
  is_quest BOOLEAN NOT NULL,
  is_victory BOOLEAN NOT NULL,
  completed BOOLEAN NOT NULL,
  cancelled BOOLEAN NOT NULL,
  pending BOOLEAN NOT NULL,
  category TEXT,
  issuer_id INTEGER,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.region_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  region_id INTEGER NOT NULL,
  x INTEGER,
  y INTEGER,
  province_id INTEGER,
  owner_id INTEGER,
  capital BOOLEAN NOT NULL,
  abandoned BOOLEAN NOT NULL,
  adjacent INTEGER[] NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, region_id)
);
CREATE TABLE corpus.settlement_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  region_id INTEGER,
  capital BOOLEAN,
  units SMALLINT,
  x INTEGER,
  y INTEGER,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.ruin_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  region_id INTEGER NOT NULL,
  x INTEGER,
  y INTEGER,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, region_id)
);
CREATE TABLE corpus.enemy_agent_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  cqi INTEGER NOT NULL,
  x INTEGER,
  y INTEGER,
  faction_id INTEGER NOT NULL,
  at_war BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord)
);
CREATE TABLE corpus.war_graph_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  faction_id INTEGER NOT NULL,
  at_war_with INTEGER[] NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, faction_id)
);
CREATE TABLE corpus.relation_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  faction_id INTEGER NOT NULL,
  at_war BOOLEAN NOT NULL,
  allied BOOLEAN NOT NULL,
  trade BOOLEAN NOT NULL,
  their_vassal BOOLEAN NOT NULL,
  standing SMALLINT NOT NULL,
  excluded BOOLEAN NOT NULL,
  mil_ally BOOLEAN NOT NULL,
  def_ally BOOLEAN NOT NULL,
  nap BOOLEAN NOT NULL,
  mil_access BOOLEAN NOT NULL,
  our_master BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, faction_id)
);
CREATE TABLE corpus.stationed_set_member (
  set_id BIGINT NOT NULL,
  ord SMALLINT NOT NULL,
  region_id INTEGER NOT NULL,
  cqi INTEGER,
  PRIMARY KEY (set_id, ord),
  UNIQUE (set_id, region_id)
);
CREATE TABLE corpus.offer (
  decision_id   BIGINT NOT NULL,
  offer_seq     SMALLINT NOT NULL,
  entity_seq    SMALLINT NOT NULL,
  action_id     INTEGER NOT NULL,
  slot_index    SMALLINT,
  score         REAL,
  exploit       REAL,
  rank          SMALLINT,
  pct_global    REAL,
  gnn_impact    REAL,
  gnn_rank      SMALLINT,
  ggnn_score    REAL,
  ggnn_rank     SMALLINT,
  PRIMARY KEY (decision_id, offer_seq)
);
CREATE TABLE corpus.taken (
  decision_id        BIGINT PRIMARY KEY,
  campaign_id        INTEGER NOT NULL,
  offer_seq          SMALLINT,
  entity_seq         SMALLINT,
  action_id          INTEGER NOT NULL,
  policy_id          INTEGER NOT NULL,
  ts                 DOUBLE PRECISION NOT NULL,
  executed           BOOLEAN NOT NULL,
  confirmed          BOOLEAN NOT NULL,
  counted            BOOLEAN NOT NULL,
  refusal_id         INTEGER,
  confirm_signal_id  INTEGER,
  latency_ms         INTEGER,
  snapshot_ms        INTEGER,
  gates_ms           INTEGER,
  execute_ms         INTEGER,
  confirm_ms         INTEGER,
  confirm_wasted_ms  INTEGER,
  polls              SMALLINT,
  total_ms           INTEGER,
  prechecks_passed   BOOLEAN,
  failed_precheck_id INTEGER,
  doomed             TEXT,
  stderr             TEXT,
  CHECK (counted = (executed AND confirmed) OR refusal_id IS NOT NULL)
)WITH (fillfactor = 80);
CREATE TABLE corpus.interrupt_option (
  interrupt_id  BIGINT NOT NULL,
  ord           SMALLINT NOT NULL,
  option_key    TEXT NOT NULL,
  text          TEXT,
  option_id     TEXT,
  answer        TEXT,
  payload       TEXT[],
  exploit       REAL,
  score         REAL,
  gnn           REAL,
  PRIMARY KEY (interrupt_id, ord),
  UNIQUE (interrupt_id, option_key)
);
CREATE TABLE corpus.interrupt_battle_panel (
  interrupt_id       BIGINT PRIMARY KEY,
  ally_cqi           INTEGER,
  enemy_cqi          INTEGER,
  n_ally_armies      SMALLINT,
  n_enemy_armies     SMALLINT,
  result_state       TEXT,
  result_text        TEXT,
  casualties_state   TEXT,
  casualties_text    TEXT
);
CREATE TABLE corpus.interrupt_diplo_panel (
  interrupt_id       BIGINT PRIMARY KEY,
  attitude           SMALLINT,
  attitude_label     TEXT,
  race               TEXT,
  reliability        TEXT[],
  strength_ranks     SMALLINT[],
  settlements        SMALLINT,
  demands            TEXT[],
  offers             TEXT[],
  treaties           TEXT[],
  amount_demanded    INTEGER,
  amount_offered     INTEGER
);
CREATE TABLE corpus.diplomacy_event (
  event_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id     INTEGER NOT NULL,
  turn            SMALLINT NOT NULL,
  ts              DOUBLE PRECISION NOT NULL,
  ts_recorded     DOUBLE PRECISION NOT NULL,
  kind_id         INTEGER NOT NULL,
  channel_id      INTEGER,
  faction_id      INTEGER,
  term_ids        INTEGER[],
  gift_id         INTEGER,
  ok              BOOLEAN,
  failed_at       TEXT,
  success_chance  REAL,
  accepted        BOOLEAN,
  chosen          TEXT,
  answer          TEXT,
  executed        BOOLEAN,
  confirmed       BOOLEAN,
  policy_id       INTEGER,
  proposer        TEXT,
  speech          TEXT,
  attitude        TEXT,
  pair_at_war     BOOLEAN,
  pair_allied     BOOLEAN,
  pair_trade      BOOLEAN,
  pair_our_master BOOLEAN,
  pair_their_vassal BOOLEAN,
  pair_standing   SMALLINT,
  turns_played    SMALLINT,
  ended_by        TEXT[],
  tracked_faction_ids INTEGER[]
);
CREATE TABLE corpus.postmortem (
  postmortem_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id         INTEGER,
  ts                  DOUBLE PRECISION NOT NULL,
  when_text           TEXT,
  run_dir             TEXT NOT NULL,
  faction_id          INTEGER,
  turns_played        SMALLINT,
  turn_at_death       SMALLINT,
  outcome_id          INTEGER NOT NULL,
  defeated            BOOLEAN NOT NULL,
  error               TEXT,
  ended_by            TEXT[],
  seconds             REAL,
  actions             INTEGER,
  confirmed           INTEGER,
  policy              TEXT,
  code_version        TEXT,
  wh3_running         BOOLEAN,
  picked_ts           DOUBLE PRECISION,
  plausibility_verdict TEXT,
  growth_evaluable    BOOLEAN,
  growth_grew         BOOLEAN,
  growth_reason       TEXT,
  growth_turn         SMALLINT,
  growth_min_gain     REAL
);
CREATE TABLE corpus.postmortem_growth_metric (
  postmortem_id  INTEGER NOT NULL,
  label          TEXT NOT NULL,
  then_value     REAL,
  now_value      REAL,
  window_turns   SMALLINT,
  PRIMARY KEY (postmortem_id, label)
);
CREATE TABLE corpus.ucb_pick (
  pick_id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts               DOUBLE PRECISION NOT NULL,
  c                REAL NOT NULL,
  k                REAL,
  scale            REAL,
  total_plays      INTEGER NOT NULL,
  campaign_map_id  INTEGER NOT NULL,
  faction_id       INTEGER NOT NULL,
  n                INTEGER NOT NULL,
  mean             REAL,
  explore          REAL,
  score            REAL,
  tied             SMALLINT NOT NULL,
  blend            REAL,
  entropy          REAL,
  std              REAL,
  adjust           REAL
);
CREATE TABLE corpus.ucb_pick_row (
  pick_id          INTEGER NOT NULL,
  rank             SMALLINT NOT NULL,
  campaign_map_id  INTEGER NOT NULL,
  faction_id       INTEGER NOT NULL,
  n                INTEGER,
  mean             REAL,
  explore          REAL,
  score            REAL,
  chosen           BOOLEAN NOT NULL,
  blend            REAL,
  entropy          REAL,
  std              REAL,
  adjust           REAL,
  PRIMARY KEY (pick_id, rank)
);
CREATE TABLE corpus.rpc_request (
  rpc_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  req_id      UUID NOT NULL UNIQUE,
  kind        TEXT NOT NULL,
  ts          DOUBLE PRECISION NOT NULL,
  payload     TEXT NOT NULL
);
CREATE TABLE corpus.rpc_response (
  req_id       UUID PRIMARY KEY,
  ts           DOUBLE PRECISION NOT NULL,
  snapshot_id  BIGINT,
  payload      TEXT,
  error        TEXT
);
CREATE TABLE ops.launch (
  launch_id     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts            DOUBLE PRECISION NOT NULL,
  code_version  TEXT NOT NULL,
  argv          TEXT[] NOT NULL,
  note          TEXT
);
CREATE TABLE ops.trial (
  trial               TEXT PRIMARY KEY,
  ts                  DOUBLE PRECISION NOT NULL,
  when_text           TEXT,
  session             TEXT,
  generation          INTEGER,
  started             DOUBLE PRECISION,
  running             BOOLEAN,
  feature_version     TEXT,
  code_version        TEXT,
  snapshots           INTEGER NOT NULL,
  campaigns           INTEGER,
  turns_total         INTEGER,
  turns_per_campaign  REAL,
  campaigns_per_hour  REAL,
  campaign_hours      REAL,
  baseline            TEXT,
  sett_mean REAL,
  sett_total INTEGER,
  sett_per_turn REAL,
  sett_campaigns_measured INTEGER,
  sett_campaigns_gained INTEGER,
  sett_campaigns_lost INTEGER,
  ll_mean REAL,
  ll_total INTEGER,
  ll_per_turn REAL,
  ll_campaigns_measured INTEGER,
  ll_campaigns_gained INTEGER,
  ll_campaigns_lost INTEGER,
  timing_s_per_campaign REAL,
  timing_s_per_turn REAL,
  corpus_rows         INTEGER,
  corpus_n_decisions  INTEGER,
  fit_trained         BOOLEAN,
  fit_rows            INTEGER,
  fit_mae_in_sample   REAL,
  archived            BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE ops.trial_campaign (
  trial TEXT NOT NULL,
  campaign_id INTEGER NOT NULL,
  PRIMARY KEY (trial, campaign_id)
);
CREATE TABLE ops.trial_policy (
  trial TEXT NOT NULL,
  scope TEXT NOT NULL,
  policy_id INTEGER NOT NULL,
  weight REAL NOT NULL,
  PRIMARY KEY (trial, scope, policy_id)
);
CREATE TABLE ops.trial_outcome (
  trial TEXT NOT NULL,
  outcome_id INTEGER NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (trial, outcome_id)
);
CREATE TABLE ops.bus_call_stat (
  channel    TEXT NOT NULL,
  key_sha    BYTEA NOT NULL,
  key        TEXT NOT NULL,
  calls BIGINT NOT NULL,
  hits BIGINT NOT NULL,
  empties BIGINT NOT NULL,
  timeouts BIGINT NOT NULL,
  errors BIGINT NOT NULL,
  total_ms   DOUBLE PRECISION NOT NULL,
  last_ts    DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (channel, key_sha)
)WITH (fillfactor = 70);
CREATE TABLE ops.manifest (
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
CREATE TABLE ops.manifest_pack (
  build_id   INTEGER NOT NULL,
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
CREATE TABLE ops.table_meta (
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
CREATE TABLE ops.column_meta (
  tbl            TEXT NOT NULL,
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
CREATE TABLE analytics2.state (
  tenant           TEXT PRIMARY KEY,
  formula_version  SMALLINT NOT NULL,
  watermark        BIGINT NOT NULL,
  built_ts         DOUBLE PRECISION,
  last_run_ts      DOUBLE PRECISION,
  last_run_seconds REAL,
  last_error       TEXT
);
CREATE TABLE analytics2.model_agreement (
  decision_id   BIGINT NOT NULL,
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
CREATE TABLE analytics2.agreement_summary (
  pair TEXT NOT NULL,
  scope TEXT NOT NULL,
  comparable INTEGER,
  rho_median REAL,
  rho_mean REAL,
  tau_median REAL,
  rbo_median REAL,
  top1_rate REAL,
  missing_b INTEGER,
  no_scores INTEGER,
  PRIMARY KEY (pair, scope)
);
CREATE TABLE analytics2.agreement_hist (
  pair TEXT NOT NULL,
  bucket SMALLINT NOT NULL,
  lo REAL,
  hi REAL,
  n INTEGER,
  PRIMARY KEY (pair, bucket)
);
CREATE TABLE analytics2.agreement_series (
  pair TEXT NOT NULL,
  axis TEXT NOT NULL,
  seq INTEGER NOT NULL,
  from_decision BIGINT,
  to_decision BIGINT,
  from_ts DOUBLE PRECISION,
  decisions INTEGER,
  rho_median REAL,
  gate TEXT,
  trial TEXT,
  generation INTEGER,
  retrained BOOLEAN,
  bucket_size INTEGER,
  PRIMARY KEY (pair, axis, seq)
);
CREATE TABLE analytics2.agreement_breakdown (
  pair TEXT NOT NULL,
  dim TEXT NOT NULL,
  key TEXT NOT NULL,
  decisions INTEGER,
  rho_median REAL,
  top1_rate REAL,
  PRIMARY KEY (pair, dim, key)
);
CREATE TABLE analytics2.acquisition (
  campaign_id           INTEGER NOT NULL,
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
CREATE TABLE analytics2.item_event (
  event_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id  INTEGER NOT NULL,
  character_id INTEGER NOT NULL,
  ancillary_id INTEGER NOT NULL,
  kind         TEXT NOT NULL,
  snapshot_id  BIGINT NOT NULL,
  turn         SMALLINT NOT NULL,
  CHECK (kind IN ('on', 'off'))
);
