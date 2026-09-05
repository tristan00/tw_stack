# 3. Physical DDL (part b: per-collection storage, set members, offers, taken, interrupts, side tables, ops)

## 3.5 Per-collection storage decision (C4)

Rule, applied from change_rate.out (376 campaigns / 17,113 decisions, reproduced within 1.5 points on a second sample, M1 G) and M1 E byte shares:

- **SET** (deduplicated ordered value in `state_set` + member table, referenced by `set_id`) when `same%` between consecutive snapshots of the same entity ≥ 80% — every such collection also has a dedup ratio (distinct values / plain rows) ≤ 16%, so member rows are ≤ 1/6 of per-snapshot rows.
- **ARRAY** on the owning row when the collection is a pure list of identifiers or coordinates (no per-element attributes) — regardless of change rate: an array element costs 2-4 bytes against a 24-byte tuple header per member row.
- **ROWS** per snapshot when `same%` < 80% and the elements carry attributes (a set would be re-created for most snapshots: dedup ratio 25-100%).

| collection (role) | mean len | same% / in-turn | dedup ratio | byte share of role (M1 E) | storage | member table / column |
|---|---|---|---|---|---|---|
| lord.skills | 50.7 | 97.0 / 97.0 | 3.0% | 48% of lord | SET | `skill_set_member` |
| hero.skills | 31.8 | 98.8 / 98.8 | 2.1% | 59% of hero | SET | `skill_set_member` |
| lord.hidden_skills / hero | 2.7 / 3.6 | 100 / 100 | 1.0% / 0.9% | 3% of hero | ARRAY | `char_state.hidden_skill_ids` |
| lord/hero.hidden_skill_states | 0.6 | 99.5 / 99.5 | 3.1% | -- | SET | `hidden_skill_state_set_member` |
| lord/hero.traits | 0.0 | 99.9 | 3.5% | -- | SET | `trait_set_member` |
| lord/hero.trait_progress | 0.8 / 0.1 | 98.3 / 99.9 | 8.9% | -- | SET | `trait_progress_set_member` |
| lord.recruitable | 3.4 | 98.1 / 99.2 | 0.8% | 3% | SET | `recruitable_set_member` |
| lord/hero.reach_chars | 10.1 / 9.8 | 72.1 / 78.8 | 23.2% | ≤ 3% | ARRAY (true keys; key universe once per snapshot) | `char_state.reach_chars_true`, `snapshot_world.reach_char_cqis` |
| lord/hero.reach_setts | 9.7 / 8.9 | 80.8 / 86.6 | 10.2% | 3% / 6% | ARRAY (same reason; pure ids) | `char_state.reach_setts_true`, `snapshot_world.reach_region_ids` |
| lord/hero.equipped | 0.8 / 0.2 | 95.8 / 98.6 | 4.8% | -- | SET (positional: `ord` + `index`) | `item_slot_set_member` |
| lord.armory | 0.0 | 100 | -- | -- | ARRAY | `char_state_ext.armory_item_ids` |
| lord.horde_slots | 3.9 (83 when present) | 99.5 / 99.6 | 8.0% | 4% | SET | `horde_slot_set_member` |
| lord.merc_pools | 1.4 pools × 11.3 | 95.1 / 97.6 | 4.2% | 11% | SET | `merc_pool_set_member` |
| lord/hero.move_tiles | 13.6 | 4.8 / 5.3 | 100% | 10% / 19% | ARRAY (coordinates; rays per character) | `char_state.move_x/move_y/reach_rays/reach_max` |
| lord.unit_cards | 6.8 | 86.5 / 92.1 | 15.8% | 6% | SET (positional `ord`; key dup 64%) | `unit_card_set_member` |
| lord/hero.pending_queue | 0.1 | 96.9 / 97.4 | 18.7% | -- | SET (positional) | `pending_queue_set_member` |
| lord.pending_recruit_keys | 0.2 | 93.5 / 95.0 | 16.3% | -- | ARRAY (dup 68%: positional array) | `char_state.pending_recruit_unit_ids` |
| lord/hero/province/campaign effect_bundles, force_effect_bundles, plague_bundles | 0.0-1.5 | 96.5-100 | 1.8-5% | -- | SET (one shape) | `effect_bundle_set_member` |
| lord.stances | 8.8 | 78.6 / 81.7 | 3.3% | 8% | SET — same% below the 80% line by 1.4 points but dedup 3.3%: only 7,523 distinct values in 226k rows; a per-snapshot table would be 30× larger | `stance_set_member` |
| province.buildable | 5.9 | 92.3 / 95.1 | 7.5% | 28% of province | SET | `buildable_set_member` |
| province.slot_states | 5.9 | 94.8 / 96.7 | 4.8% | 44% | SET | `slot_state_set_member` |
| province.built | 2.1 | 98.2 / 100 | 2.0% | -- | SET | `built_set_member` |
| province.building_now | 0.3 | 93.6 / 96.7 | 11% | -- | SET | `building_now_set_member` |
| province.corruption | 7.0 | 94.8 / 99.0 | 2.1% | 7% | SET (same shape as resources) | `resource_set_member` |
| province.edicts | 4.1 | 100 / 100 | 0.1% | 5% | ARRAY | `province_state.edict_ids` |
| province.locked_slots | 3.4 | 99.8 / 100 | 0.07% | -- | ARRAY | `province_state.locked_slots` |
| campaign.tech | 56.6 | 93.3 / 95.6 | 3.8% | 34% of campaign entity | SET | `tech_set_member` |
| campaign.lord_pools | 24.4 subtypes (25.3 candidates) | 95.5 / 97.6 | 4.6% | 40% | SET (two-level) | `lord_pool_set_member`, `lord_pool_candidate` |
| campaign.rites | 16.7 | 95.4 / 97.2 | 8.0% | 17% | SET | `rite_set_member` |
| campaign.missions | 0.9 (v36+) | 99.2 / 99.4 | 6.7% | -- | SET | `mission_set_member` |
| campaign.anc_pool / equipped_all | 2.5 / 1.6 | 96.0 / 91.6 | 4.2% / 7.5% | -- | SET (positional) | `item_slot_set_member` |
| CB.resources | 5.4 | 91.7 / 96.1 | 8.0% | 26% of CB | SET | `resource_set_member` |
| CB.hero_type_counts | 1.1 | 98.8 / 99.3 | 0.5% | -- | SET | `hero_count_set_member` |
| CB.read_failures | 0.0 | 100 | -- | -- | ROWS (never non-empty in sample; must survive if it is) | `snapshot_read_failure` |
| world.armies | 4.7 | 47.1 / 52.7 | 51.5% | 18% of WB | ROWS | `world_army` |
| world.hostiles | 8.1 | 73.6 / 82.4 | 25.0% | 14% | ROWS | `world_hostile` |
| world.regions | 14.7 | 92.6 / 96.7 | 6.3% | 41% | SET | `region_set_member` |
| world.relations | 9.6 | 84.4 / 94.4 | 13.6% | 18% | SET — the brief lists relations among per-snapshot candidates; the measurement (84.4% same, 22,350 distinct values in 164,224 rows) puts it above the line, and a set is 7× fewer rows | `relation_set_member` |
| world.settlements | 2.0 | 89.7 / 90.4 | 7.2% | -- | SET | `settlement_set_member` |
| world.ruins | 3.0 | 96.6 / 98.5 | 3.3% | -- | SET | `ruin_set_member` |
| world.enemy_agents | 0.6 | 95.9 / 99.0 | 11.7% | -- | SET | `enemy_agent_set_member` |
| world.war_graph | 4.2 | 93.9 / 96.6 | 5.9% | -- | SET | `war_graph_set_member` |
| world.stationed | 2.0 | 92.7 / 92.5 | 4.0% | -- | SET | `stationed_set_member` |
| world.citizenry / diplo_unseen | 1.9 / 0.0 | 97.1 / 99.8 | 3.1% | -- | ARRAY | `snapshot_world.citizenry`, `.diplo_unseen` |

Projected member rows: dedup projection 13.6M rows corpus-wide (change_rate.out corpus-scale, an over-estimate per M1 G flaw 2) against 97.3M plain rows; the ROWS collections (armies 4.7 + hostiles 8.1 per snapshot) add 2.6M rows; arrays add none. 06 sizes each table.

## 3.6 Set member tables

`kind` codes: 1 skill, 2 stance, 3 recruitable, 4 hidden_skill_state, 5 trait, 6 trait_progress, 7 item_slot, 8 horde_slot, 9 merc_pool, 10 pending_queue, 11 effect_bundle, 12 unit_card, 13 buildable, 14 slot_state, 15 built, 16 building_now, 17 resource, 18 hero_count, 19 tech, 20 rite, 21 lord_pool, 22 mission, 23 region, 24 settlement, 25 ruin, 26 enemy_agent, 27 war_graph, 28 relation, 29 stationed. Same-shaped collections share a kind (item_slot: equipped/anc_pool/equipped_all; effect_bundle: 5 sources; resource: resources/corruption) so identical values share a set. The empty set per kind is inserted by DDL. Member field order below is the canonical encoding order (2.7).

```sql
INSERT INTO corpus.state_set (kind, hash, n)
SELECT k, sha256(('\x' || lpad(to_hex(k), 4, '0'))::bytea || '\x00000000'::bytea), 0 FROM generate_series(1, 29) k;

CREATE TABLE corpus.skill_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  skill_id INTEGER NOT NULL REFERENCES dict.skill, status_id INTEGER NOT NULL REFERENCES dict.enum,
  level SMALLINT NOT NULL, total_levels SMALLINT NOT NULL, tier SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, skill_id));
CREATE TABLE corpus.stance_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  stance_id INTEGER NOT NULL REFERENCES dict.stance, active BOOLEAN NOT NULL, can_activate BOOLEAN NOT NULL, can_afford BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, stance_id));
CREATE TABLE corpus.recruitable_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  unit_id INTEGER NOT NULL REFERENCES dict.unit, state TEXT NOT NULL, cost INTEGER NOT NULL, disabled BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, unit_id));
CREATE TABLE corpus.hidden_skill_state_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  skill_id INTEGER NOT NULL REFERENCES dict.skill, level SMALLINT NOT NULL, total_levels SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.trait_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  trait_id INTEGER NOT NULL REFERENCES dict.trait, level SMALLINT NOT NULL, threshold_points SMALLINT NOT NULL,
  points SMALLINT NOT NULL, chaos_realm BOOLEAN NOT NULL, level_key_id INTEGER NOT NULL REFERENCES dict.trait_level,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.trait_progress_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  trait_id INTEGER NOT NULL REFERENCES dict.trait, points SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, trait_id));
CREATE TABLE corpus.item_slot_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  index SMALLINT NOT NULL, ancillary_id INTEGER REFERENCES dict.ancillary, name TEXT NOT NULL,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.horde_slot_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  slot_index SMALLINT NOT NULL, slot_id TEXT NOT NULL, building_id INTEGER NOT NULL REFERENCES dict.building,
  empty BOOLEAN NOT NULL, available BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, slot_index, building_id));
CREATE TABLE corpus.merc_pool_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  action_id INTEGER NOT NULL REFERENCES dict.enum, unit_id INTEGER NOT NULL REFERENCES dict.unit,
  avail SMALLINT NOT NULL, cost INTEGER NOT NULL, can BOOLEAN,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, action_id, unit_id));
CREATE TABLE corpus.pending_queue_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  unit_id INTEGER NOT NULL REFERENCES dict.unit, turns_left SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.effect_bundle_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  effect_bundle_id INTEGER NOT NULL REFERENCES dict.effect_bundle, turns_remaining SMALLINT,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.unit_card_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  unit_id INTEGER NOT NULL REFERENCES dict.unit, strength_pct DOUBLE PRECISION NOT NULL,
  category_id INTEGER NOT NULL REFERENCES dict.unit_category, xp SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.buildable_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  slot_index SMALLINT, building_id INTEGER NOT NULL REFERENCES dict.building, active BOOLEAN NOT NULL, empty BOOLEAN NOT NULL,
  can_upgrade BOOLEAN NOT NULL, cost INTEGER NOT NULL, upkeep INTEGER NOT NULL, level SMALLINT NOT NULL, can_afford_resources BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.slot_state_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  index SMALLINT NOT NULL, damaged BOOLEAN NOT NULL, can_repair BOOLEAN NOT NULL, repairing BOOLEAN NOT NULL,
  can_dismantle BOOLEAN NOT NULL, refund INTEGER, queued BOOLEAN NOT NULL, queued_building_id INTEGER REFERENCES dict.building,
  empty BOOLEAN NOT NULL, building_id INTEGER REFERENCES dict.building, health SMALLINT, max_health SMALLINT,
  ruined BOOLEAN NOT NULL, repair_cost INTEGER NOT NULL, upgrading BOOLEAN NOT NULL, dismantling BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, index));
CREATE TABLE corpus.built_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  slot_index SMALLINT NOT NULL, building_id INTEGER NOT NULL REFERENCES dict.building,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, slot_index));
CREATE TABLE corpus.building_now_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  slot_index SMALLINT NOT NULL, building_id INTEGER NOT NULL REFERENCES dict.building, turns_left SMALLINT NOT NULL, paused BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, slot_index));
CREATE TABLE corpus.resource_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  resource_id INTEGER NOT NULL REFERENCES dict.pooled_resource, value INTEGER,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, resource_id));
CREATE TABLE corpus.hero_count_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  agent_type_id INTEGER NOT NULL REFERENCES dict.agent_type, n SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, agent_type_id));
CREATE TABLE corpus.tech_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  tech_node_id INTEGER NOT NULL REFERENCES dict.tech_node, researched BOOLEAN NOT NULL, can_research BOOLEAN NOT NULL, cost INTEGER NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, tech_node_id));
CREATE TABLE corpus.rite_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  index SMALLINT NOT NULL, ritual_id INTEGER NOT NULL REFERENCES dict.ritual, can_perform BOOLEAN NOT NULL,
  reason_id INTEGER REFERENCES dict.rite_reason,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, index));
CREATE TABLE corpus.lord_pool_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  subtype_id INTEGER NOT NULL REFERENCES dict.agent_subtype, n SMALLINT NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, subtype_id));
CREATE TABLE corpus.lord_pool_candidate (
  set_id BIGINT NOT NULL, subtype_id SMALLINT NOT NULL, ord SMALLINT NOT NULL,
  can BOOLEAN NOT NULL, agent BOOLEAN, bg_skill_id INTEGER REFERENCES dict.skill,
  cand_subtype_id INTEGER NOT NULL REFERENCES dict.agent_subtype, trait_ids INTEGER[],
  PRIMARY KEY (set_id, subtype_id, ord),
  FOREIGN KEY (set_id, subtype_id) REFERENCES corpus.lord_pool_set_member (set_id, subtype_id));
CREATE TABLE corpus.mission_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  mission_id INTEGER NOT NULL REFERENCES dict.mission, status_id INTEGER NOT NULL REFERENCES dict.enum, turns_remaining SMALLINT,
  is_quest BOOLEAN NOT NULL, is_victory BOOLEAN NOT NULL, completed BOOLEAN NOT NULL, cancelled BOOLEAN NOT NULL, pending BOOLEAN NOT NULL,
  category TEXT, issuer_id INTEGER REFERENCES dict.mission_issuer,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.region_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  region_id INTEGER NOT NULL REFERENCES dict.region, x INTEGER, y INTEGER, province_id INTEGER REFERENCES dict.province,
  owner_id INTEGER REFERENCES dict.faction, capital BOOLEAN NOT NULL, abandoned BOOLEAN NOT NULL, adjacent SMALLINT[] NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, region_id));
CREATE TABLE corpus.settlement_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  region_id INTEGER REFERENCES dict.region, capital BOOLEAN, units SMALLINT, x INTEGER, y INTEGER,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.ruin_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  region_id INTEGER NOT NULL REFERENCES dict.region, x INTEGER, y INTEGER,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, region_id));
CREATE TABLE corpus.enemy_agent_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  cqi INTEGER NOT NULL, x INTEGER, y INTEGER, faction_id INTEGER NOT NULL REFERENCES dict.faction, at_war BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord));
CREATE TABLE corpus.war_graph_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  faction_id INTEGER NOT NULL REFERENCES dict.faction, at_war_with SMALLINT[] NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, faction_id));
CREATE TABLE corpus.relation_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  faction_id INTEGER NOT NULL REFERENCES dict.faction, at_war BOOLEAN NOT NULL, allied BOOLEAN NOT NULL, trade BOOLEAN NOT NULL,
  their_vassal BOOLEAN NOT NULL, standing SMALLINT NOT NULL, excluded BOOLEAN NOT NULL, mil_ally BOOLEAN NOT NULL,
  def_ally BOOLEAN NOT NULL, nap BOOLEAN NOT NULL, mil_access BOOLEAN NOT NULL, our_master BOOLEAN NOT NULL,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, faction_id));
CREATE TABLE corpus.stationed_set_member (
  set_id BIGINT NOT NULL REFERENCES corpus.state_set, ord SMALLINT NOT NULL,
  region_id INTEGER NOT NULL REFERENCES dict.region, cqi INTEGER,
  PRIMARY KEY (set_id, ord), UNIQUE (set_id, region_id));
```

UNIQUE constraints are declared only where M1 measured 100% key uniqueness (skills, stances, recruitable, trait_progress, horde_slots (slot_index,key), merc_pools per (pool,key), slot_states index, built, building_now, resources, hero_type_counts, tech, rites index, lord_pools subtype, regions, ruins, war_graph, relations, stationed). `unit_cards`, `pending_queue`, `equipped`/`anc_pool`/`equipped_all` (key dup 7-28%), `buildable` (key dup 3.6%), `hidden_skill_states`, `traits`, `effect_bundles`, `settlements`, `enemy_agents`, `missions` (dup 0.4%) carry only `ord`. `recruitable.state` stays TEXT (one value `active`, M1 A.3; a dictionary for one value is noise).

Dict-keyed collections are ordered by sorted key at write (`sort_keys=True` makes JSON order irrelevant; the canonical encoding orders `resources`, `corruption`, `hero_type_counts`, `built`, `building_now`, `stationed`, `lord_pools`, `trait_progress` by key text) so identical values hash identically.

## 3.7 Offers and taken

```sql
CREATE TABLE corpus.offer (
  decision_id   BIGINT NOT NULL REFERENCES corpus.decision,
  offer_seq     SMALLINT NOT NULL,
  entity_seq    SMALLINT NOT NULL,
  action_id     INTEGER NOT NULL REFERENCES dict.action,
  slot_index    SMALLINT,
  score         REAL,
  exploit       REAL,
  rank          SMALLINT,
  pct_global    REAL,
  gnn_impact    REAL,
  gnn_rank      SMALLINT,
  ggnn_score    REAL,
  ggnn_rank     SMALLINT,
  PRIMARY KEY (decision_id, offer_seq),
  FOREIGN KEY (decision_id, entity_seq) REFERENCES corpus.snapshot_entity (snapshot_id, entity_seq)
);
CREATE INDEX offer_action ON corpus.offer (action_id, decision_id);

CREATE TABLE corpus.taken (
  decision_id        BIGINT PRIMARY KEY REFERENCES corpus.decision,
  campaign_id        INTEGER NOT NULL REFERENCES corpus.campaign,
  offer_seq          SMALLINT,
  entity_seq         SMALLINT,
  action_id          INTEGER NOT NULL REFERENCES dict.action,
  policy_id          INTEGER NOT NULL REFERENCES dict.enum,
  ts                 DOUBLE PRECISION NOT NULL,
  executed           BOOLEAN NOT NULL,
  confirmed          BOOLEAN NOT NULL,
  counted            BOOLEAN NOT NULL,
  refusal_id         INTEGER REFERENCES dict.enum,
  confirm_signal_id  INTEGER REFERENCES dict.confirm_signal,
  latency_ms         INTEGER,
  snapshot_ms        INTEGER,
  gates_ms           INTEGER,
  execute_ms         INTEGER,
  confirm_ms         INTEGER,
  confirm_wasted_ms  INTEGER,
  polls              SMALLINT,
  total_ms           INTEGER,
  prechecks_passed   BOOLEAN,
  failed_precheck_id INTEGER REFERENCES dict.enum,
  doomed             TEXT,
  stderr             TEXT,
  CHECK (counted = (executed AND confirmed) OR refusal_id IS NOT NULL)
) WITH (fillfactor = 80);
CREATE INDEX taken_campaign ON corpus.taken (campaign_id, decision_id) INCLUDE (action_id, policy_id, latency_ms);
CREATE INDEX taken_ts ON corpus.taken (ts);
CREATE INDEX taken_action ON corpus.taken (action_id);
```

`offer.score` etc. are `REAL`: the model outputs are float32 predictions and rank shares in [0,1] (M2 A.2); `REAL` halves the score bytes against today's 8 `DOUBLE PRECISION` columns while `pct_global` at 4-byte precision still distinguishes 1/572 ranks. `taken.stderr` is the executor's captured stderr text — opaque, read only by `debugging/timeline.py` (R3 D) — a value, not a structure (C2). `confirm_before`/`confirm_after`, `diagnostics.params`, `execute_error` (NULL on 3,025/3,025, M2 C) are dropped: no reader (R2, R3). `taken.policy` is NOT NULL (never NULL today, M2 A.3); the placeholder row written at pick time has `refusal = awaiting_execution`, `executed = confirmed = counted = false`.

## 3.8 Interrupt options and panels

```sql
CREATE TABLE corpus.interrupt_option (
  interrupt_id  BIGINT NOT NULL REFERENCES corpus.interrupt,
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
  interrupt_id       BIGINT PRIMARY KEY REFERENCES corpus.interrupt,
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
  interrupt_id       BIGINT PRIMARY KEY REFERENCES corpus.interrupt,
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
```

Retained panel keys are exactly the set read by `interrupt_model.py:57-130` and `memory.py:153-178` (R2 B5, R4 H.2); `armies`, `faction_names`, `resources`, `rewards`, `rows`, `sections`, `terms`, `amounts`, `dismiss_visible`, `name` have no reader and are dropped (C2). `dilemma` options' `subtree` (77% of `options_json` bytes, no reader, R4 C.9) is dropped; `payload` (effect texts, read by `interrupt_model._row`) is kept as a text array. The occupation panel's `region` goes to `interrupt.region_id`.

## 3.9 Side tables: diplomacy events, postmortems, ucb picks

```sql
CREATE TABLE corpus.diplomacy_event (
  event_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id     INTEGER NOT NULL REFERENCES corpus.campaign,
  turn            SMALLINT NOT NULL,
  ts              DOUBLE PRECISION NOT NULL,
  ts_recorded     DOUBLE PRECISION NOT NULL,
  kind_id         INTEGER NOT NULL REFERENCES dict.enum,
  channel_id      INTEGER REFERENCES dict.enum,
  faction_id      INTEGER REFERENCES dict.faction,
  term_ids        SMALLINT[],
  gift_id         INTEGER REFERENCES dict.enum,
  ok              BOOLEAN,
  failed_at       TEXT,
  success_chance  REAL,
  accepted        BOOLEAN,
  chosen          TEXT,
  answer          TEXT,
  executed        BOOLEAN,
  confirmed       BOOLEAN,
  policy_id       INTEGER REFERENCES dict.enum,
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
  tracked_faction_ids SMALLINT[]
);
CREATE INDEX diplomacy_event_campaign ON corpus.diplomacy_event (campaign_id, event_id);
CREATE INDEX diplomacy_event_ts ON corpus.diplomacy_event (ts);

CREATE TABLE corpus.postmortem (
  postmortem_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id         INTEGER REFERENCES corpus.campaign,
  ts                  DOUBLE PRECISION NOT NULL,
  when_text           TEXT,
  run_dir             TEXT NOT NULL,
  faction_id          INTEGER REFERENCES dict.faction,
  turns_played        SMALLINT,
  turn_at_death       SMALLINT,
  outcome_id          INTEGER NOT NULL REFERENCES dict.enum,
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
CREATE INDEX postmortem_campaign ON corpus.postmortem (campaign_id);
CREATE INDEX postmortem_ts ON corpus.postmortem (ts);
CREATE TABLE corpus.postmortem_growth_metric (
  postmortem_id  INTEGER NOT NULL REFERENCES corpus.postmortem,
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
  campaign_map_id  INTEGER NOT NULL REFERENCES dict.campaign_map,
  faction_id       INTEGER NOT NULL REFERENCES dict.faction,
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
CREATE INDEX ucb_pick_ts ON corpus.ucb_pick (ts);
CREATE TABLE corpus.ucb_pick_row (
  pick_id          INTEGER NOT NULL REFERENCES corpus.ucb_pick,
  rank             SMALLINT NOT NULL,
  campaign_map_id  INTEGER NOT NULL REFERENCES dict.campaign_map,
  faction_id       INTEGER NOT NULL REFERENCES dict.faction,
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
ALTER TABLE corpus.campaign ADD FOREIGN KEY (ucb_pick_id) REFERENCES corpus.ucb_pick;
```

`diplomacy_event` columns by kind (R4 D): `deal/outgoing` → faction, term_ids, gift, ok, failed_at, success_chance, accepted, pair_* (from `treaty_before`); `deal/diplomacy_proposal|notice|ally_attacked` → chosen, answer, executed, confirmed, policy, proposer, speech, attitude, pair_* (from `pair`); `pair_checkpoint` → faction, pair_* (NULL row when `pair: null`, 48 rows); `campaign_end` → turns_played, ended_by, tracked_faction_ids. The 74,145 payload keys not listed (`panel.requested/staged/…`, `options`, `facts`, `faction_keys`, `variant`) have no reader (R3 A.2 `diplomacy_tail` reads only channel/faction/terms/speech/success_chance/standing). `postmortem` drops `turn_tail` (70% of bytes), `errors_tail`, `game_logs`, `trajectory`, `recent_battles`, `defeat_row`, `campaign_source`, `growth.metrics` → `postmortem_growth_metric`; readers (`campaign_endings`, `health_extract`, `timeline`) are covered (R3 C.1, D). `campaign.outcome_id/defeated` are copied at W7.

## 3.10 Transport and ops

```sql
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
CREATE INDEX rpc_request_ts ON corpus.rpc_request (ts);
CREATE INDEX rpc_response_ts ON corpus.rpc_response (ts);

CREATE TABLE ops.launch (
  launch_id     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts            DOUBLE PRECISION NOT NULL,
  code_version  TEXT NOT NULL,
  argv          TEXT[] NOT NULL,
  note          TEXT
);
ALTER TABLE corpus.campaign ADD FOREIGN KEY (segment_id) REFERENCES ops.launch;

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
  sett_mean REAL, sett_total INTEGER, sett_per_turn REAL, sett_campaigns_measured INTEGER, sett_campaigns_gained INTEGER, sett_campaigns_lost INTEGER,
  ll_mean REAL, ll_total INTEGER, ll_per_turn REAL, ll_campaigns_measured INTEGER, ll_campaigns_gained INTEGER, ll_campaigns_lost INTEGER,
  timing_s_per_campaign REAL, timing_s_per_turn REAL,
  corpus_rows         INTEGER,
  corpus_n_decisions  INTEGER,
  fit_trained         BOOLEAN,
  fit_rows            INTEGER,
  fit_mae_in_sample   REAL,
  archived            BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE ops.trial_campaign (trial TEXT NOT NULL REFERENCES ops.trial, campaign_id INTEGER NOT NULL REFERENCES corpus.campaign, PRIMARY KEY (trial, campaign_id));
CREATE TABLE ops.trial_policy   (trial TEXT NOT NULL REFERENCES ops.trial, scope TEXT NOT NULL, policy_id INTEGER NOT NULL REFERENCES dict.enum, weight REAL NOT NULL, PRIMARY KEY (trial, scope, policy_id));
CREATE TABLE ops.trial_outcome  (trial TEXT NOT NULL REFERENCES ops.trial, outcome_id INTEGER NOT NULL REFERENCES dict.enum, n INTEGER NOT NULL, PRIMARY KEY (trial, outcome_id));

CREATE TABLE ops.bus_call_stat (
  channel    TEXT NOT NULL,
  key_sha    BYTEA NOT NULL,
  key        TEXT NOT NULL,
  calls BIGINT NOT NULL, hits BIGINT NOT NULL, empties BIGINT NOT NULL, timeouts BIGINT NOT NULL, errors BIGINT NOT NULL,
  total_ms   DOUBLE PRECISION NOT NULL,
  last_ts    DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (channel, key_sha)
) WITH (fillfactor = 70);
```

`rpc_request.payload` is the message body of the advisor→recorder queue: consumed once by the recorder, pruned after 900 s, read by no query (C2: operational, opaque to SQL). Interrupt records no longer carry `tree`/`controls` (07), so the average body drops from 346 KB to ≤ 4 KB. `ops.trial` replaces `metrics.trials/trials_archive` (payload keys per M2 C; `campaign_uuids` → `trial_campaign`, `strategies`/`interrupt_strategies` → `trial_policy`, `outcomes` → `trial_outcome`; `hist`, `policies`, `run_dirs`, `campaign_index` have no reader in R3 B `TrialRow`).
