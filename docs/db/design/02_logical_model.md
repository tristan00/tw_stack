# 2. Logical model

## 2.1 Entities and identities

| entity | identity (candidate key) | surrogate | scope of identity | evidence |
|---|---|---|---|---|
| campaign | `campaign_key` = collector `campaign_uuid` (`<faction>_<hex>_<hex>`) | `campaign_id` (kept from today) | global; 0 rows use the `faction@run` fallback (R1 A.5, M3 §0) | one game save played once |
| snapshot | `snapshot_id` (identity); for kind=decision also `decision_uuid` (client-generated, UNIQUE) | `snapshot_id` | global; monotonic in `ts` (M2 §0: 0 inversions) | one capture of the state shape (decision or interrupt), C6 |
| decision | = snapshot with kind `decision` | `decision_id` = `snapshot_id` (migrated 1:1 from today's ids) | -- | the advisor asked for options at this state |
| interrupt | = snapshot with kind `interrupt`; natural key (`campaign_id`, `ts_choice`, `root`, `chosen`) is not unique today (R4 C.6 twins) → surrogate only | `interrupt_id` = `snapshot_id` | -- | a game screen answered |
| character | (`campaign_id`, `cqi`) | `character_id` | per campaign: within a campaign a cqi is unique; across campaigns the same cqi denotes different characters in 452/931 reused values (M3 D) | lord or hero owned by the player; identity across snapshots of one campaign |
| character state | (`snapshot_id`, `character_id`) | none | one row per entity of kind lord/hero per decision snapshot | the entity's `state` dict at that snapshot |
| province state | (`snapshot_id`, `region_id`) | none | region keys are stable across campaigns of a map (M3 D) | the province entity at that snapshot |
| campaign state | (`snapshot_id`) | none | 1 per decision snapshot (present in 206,907/206,907, M1 D) | the campaign entity's collections |
| world army/hostile row | (`snapshot_id`, `ord`) | none | cqi is not unique in `hostiles` (settlement rows have none) and `armies` order is information (M1 A.2) | per-snapshot world lists |
| state set | (`kind`, `hash`) | `set_id` | global, shared by every snapshot with an identical collection value | C5: a deduplicated collection value |
| set member | (`set_id`, `ord`) | none | ordinal = list position; identity only within its set | C5 |
| offer | (`decision_id`, `offer_seq`) | none | `offer_seq` = list position in the advisor's surviving option list; the identity tuple (`entity_seq`, `action_id`, `slot_index`) is **not** unique (building offers duplicate 12.1%, M2 A.1) | one option shown to the policy |
| action | (`action_type`, `action_key`) | `action_id` | global dictionary; today's identity also carried `context_kind/context_id/params` (R1 A.4) — moved to `offer.entity_seq` and derived params (2.6) | the executor's argument |
| taken | (`decision_id`) | none | ≤ 1 per decision (M2 A.1) | the pick and its execution result |
| dictionary row | (`key`) per family | `id` | global | C1 |
| reference row | the pack table's `is_key` columns (unique in data for all 1,520 tables, R5 B) | none except for the 2 non-unique referenced columns (R5 C) | per build | game data |
| loc | (`tbl`, `col`, `key`) ≡ raw loc key (241,972 distinct, R5 F) | none | per build | localised text |
| collector version | `collector_sha` | `version_id` | global; 3 sentinel rows for pre-version history (2.5) | C7 |

## 2.2 Temporal model

- **Point snapshots, no intervals.** A snapshot is the complete state at `ts` (double, epoch seconds, the collector's `time.time()` at end of collect for decisions; the launcher's record time for interrupts — fixing R4 C.1). "State at time T for campaign C" = the snapshot with the greatest `ts ≤ T` in C (index `(campaign_id, ts)`), plus all rows keyed by its `snapshot_id`. No as-of/SCD rows: the per-collection change rates (change_rate.out) show dedup sets reach 3-8% of plain rows for the slow collections, while SCD rows would be 1.3-2× the dedup rows (17.6M vs 13.6M projected) and need interval maintenance on every write. Dedup sets are append-only and kill-safe (a set is inserted before the snapshot that references it, inside the same transaction).
- **Turn model.** `snapshot.turn` is the game turn at capture (NOT NULL). `turn_open(campaign_id, turn)` = MIN(`snapshot_id`) over decision snapshots of that turn; `turn_close` = MAX. Both are index-only queries on `(campaign_id, turn, snapshot_id)` (05 Q-T1). The stored `decision_seq` (recorder-lifetime counter, R1 H.1) is dropped; ordering within a campaign is `snapshot_id`.
- **Interrupts on the spine (C6).** An interrupt snapshot carries: `prev_decision_id` = the greatest decision `snapshot_id` of the same campaign with `ts_choice ≥ decision.ts` (the state the chooser actually scored against; R2 F.31-32, R4 H.2), and its **own captured state** (campaign scalars + world, both captured by the launcher immediately before the click → `state_at = 'panel'`). Migrated rows have `state_at = 'recorder'` (post-click, post-drain; R4 C.2). The column makes the semantic difference explicit and queryable.
- **Entity continuity.** `character.first_snapshot_id`/`last_snapshot_id` are maintained at write; a character absent from a snapshot has no `char_state` row for it (97.6% of lords / 98.9% of heroes persist between consecutive decisions, M1 D). Province continuity is by `region_id`.

## 2.3 Functional dependencies and normal form per relation

Notation: `→` FD; PK underlined by naming. All relations are in BCNF unless stated; the deliberate denormalisations are listed in 2.4.

| relation | key | non-trivial FDs beyond the key | NF | note |
|---|---|---|---|---|
| campaign | campaign_id; campaign_key | campaign_key → everything; (faction_id, campaign_map_id) → faction_cqi (M3 D: 0 conflicts over 128 pairs) — stored on snapshot_campaign, not here | BCNF | `faction_cqi` lives on the snapshot because it is a captured value (2.4-e) |
| snapshot | snapshot_id | none | BCNF | kind, campaign_id, ts, turn, version_id |
| decision | decision_id; decision_uuid | -- | BCNF | 1:1 subtype of snapshot |
| interrupt | interrupt_id | -- | BCNF | 1:1 subtype |
| snapshot_campaign | snapshot_id | none (the 24 scalars are all captured, none derives from another; `campaign_uuid`, `campaign_map`, `presave_radius`, `selector`, `leader`, `difficulty`, `faction` are campaign-level facts repeated per snapshot in the source; stored once on `campaign` except where presence varies per snapshot — see 2.4-d) | BCNF | |
| snapshot_world | snapshot_id | `diplo_hostile_rows` = COUNT(world_hostile) (M1 A.2: 2070/2070) — stored anyway for fidelity, CHECK enforced at write | BCNF (+ one derived column) | |
| world_army | (snapshot_id, ord) | cqi → subtype, agent_type, is_leader within a snapshot (cqi unique per snapshot); not declared UNIQUE (ord is the key; uniqueness of cqi asserted at write) | BCNF | |
| world_hostile | (snapshot_id, ord) | none | BCNF | three row shapes by `kind` (03 §3.3) |
| character | character_id; (campaign_id, cqi) | -- | BCNF | |
| snapshot_entity | (snapshot_id, entity_seq); (snapshot_id, kind, character_id) ; (snapshot_id, kind, region_id) | -- | BCNF | entity order |
| char_state | (snapshot_id, character_id) | `units` = COUNT(unit_card members) and `pending_recruits` = len(pending_recruit_unit_ids) (M1 A.3: 3102/3102 both) — kept as captured columns, CHECK at write; `ap_pct` = ap_remaining/ap_per_turn when ap_per_turn > 0 (2633 rows agree) — kept (measure) | BCNF + 3 captured-derivable columns | wide-row concern addressed by `char_state_ext` (v31 block) |
| char_state_ext | (snapshot_id, character_id) | -- | BCNF | only for collector versions emitting the v31 block; row absence = not observed |
| province_state | (snapshot_id, region_id) | `buildings` = COUNT(built) (not verified in M1; not asserted) | BCNF | |
| campaign_state | snapshot_id | -- | BCNF | |
| state_set | set_id; (kind, hash) | hash → n, members | BCNF | |
| *_set_member | (set_id, ord) | for key-unique collections (skills, tech, rites, stances, recruitable, hidden_skill_states, equipped, buildable by (slot_index,key), slot_states by index, regions, settlements, relations, war_graph, enemy_agents, ruins, anc_pool by index): (set_id, key) → ord; declared UNIQUE where M1 measured 100% uniqueness (03) | BCNF | |
| lord_pool_set_member | (set_id, subtype_id) | subtype_id → n | BCNF | |
| lord_pool_candidate | (set_id, subtype_id, ord) | -- | BCNF | parallel arrays unpivoted |
| offer | (decision_id, offer_seq) | none | BCNF | scores are attributes of the offer (2.4-a) |
| dict.action | action_id; (action_type_id, action_key) | -- | BCNF | |
| taken | decision_id | offer_seq → entity_seq, action_id (through offer) — stored redundantly (2.4-b) | 3NF | |
| decision_timing | decision_id | -- | BCNF | |
| interrupt_option | (interrupt_id, ord); (interrupt_id, option_key) | -- | BCNF | |
| interrupt_battle_panel / interrupt_diplo_panel | interrupt_id | -- | BCNF | |
| diplomacy_event | event_id | -- | BCNF | |
| postmortem | postmortem_id | campaign_id → (outcome, defeated) copied onto campaign (2.4-c) | BCNF | |
| dict.* | id; key | key → is_reference, ref_build_id | BCNF | |
| ref.* | pack key columns | as in the RON | as shipped (game data is not normalised further) | |
| ref.loc | (tbl, col, key); loc_key | -- | BCNF | |

## 2.4 Deliberate denormalisations (each with the reader that pays for it otherwise)

a. **Scores on `offer`.** `score, exploit, rank, pct_global, gnn_impact, gnn_rank, ggnn_score, ggnn_rank` are columns of `offer`, not a second and third table (today: offer_scores 2.6 GB + offer_model_scores 2.3 GB, two extra PK indexes of 0.67 + 0.86 GB; R6 B-C). The pick rpc arrives after the options rpc today; 07 merges them into one `decide` rpc so the offer row is inserted once with its scores — no UPDATE. `offer_model_scores.model` had exactly one value (`greedy_gnn`, M2 A.2) so the model dimension is two columns.
b. **`taken.entity_seq`, `taken.action_id`** duplicated from the offer row: every reader of `taken` (T3, T6, A9, A10) needs them without a join, and 9 `forced_end_turn` rows have no offer (M2 A.4).
c. **`campaign` carries outcome/defeated (from postmortem), first/last snapshot ids, turns, and the growth/reward aggregates** `first_settlements, peak_settlements, first_lord_level, peak_lord_level, first_ts, last_ts, n_decisions, n_taken, n_counted, allies_max, vassals_max`: today `campaign_gains`, `campaign_growth`, `TRAJECTORY_SQL`, `_decs_all`, `_acts_all`, `start_counts` recompute them over all decisions for every campaigns/starts/positions/training page (R3 A.2-A.3, A.7, F.1-F.7). They are maintained by W1's single UPDATE (`GREATEST`/`COALESCE`) and W4 (counts); they are recomputable (12 verification recomputes them).
d. **`snapshot_campaign.difficulty, leader, selector, presave_radius, campaign_map_id`** repeated per snapshot although campaign-level: their *presence* varies per snapshot in the history (absent on decision_id < 20,000; M1 B/C) and the round-trip must reproduce absence. `campaign_uuid` and `faction` are not repeated (present on 100% of blobs, M1 A.1) — hydration copies them from `campaign`.
e. **`snapshot_campaign.faction_cqi`** stored per snapshot although (faction, map) → faction_cqi: it is a captured value used as a Lua parameter and the FD is measured on 128 pairs only (M3 D).
f. **`interrupt.prev_decision_id`** is derivable from (`campaign_id`, `ts_choice`) but is the join every interrupt reader performs (T2, T5, L7, U6).
g. **`char_state.reach_rays`, `reach_max`** are per character (identical on all 13.6 tiles, M1 A.3), stored once per character, not per tile; hydration fans them out.
h. **`world_hostile`** as one table with nullable columns by `kind` rather than three tables: readers iterate the list as one ordered sequence with F-semantics on cqi/region (R2 B2).

## 2.5 Collector version binding (C7)

`collector_version(version_id, collector_sha UNIQUE, git_sha, started_ts, note, emits_v31_block, emits_missions, emits_pending_queue, emits_campaign_meta)`; `snapshot.version_id NOT NULL`. Sentinel rows for the 131,716 decisions with NULL `version_id` (M1 C) are assigned at migration from the **observed key signature**, not from ranges, so the `emits_*` flags are exact:

| sentinel sha | emits_campaign_meta (difficulty/leader/selector) | emits_pending_queue | emits_v31_block | emits_missions | expected decisions |
|---|---|---|---|---|---|
| `legacy:meta0:pq0` | no | no | no | no | ≈ 62/2070 × 131,716 ≈ 3,900 (decision_id < 20,000) |
| `legacy:meta1:pq0` | yes | no | no | no | ≈ 120,000 |
| `legacy:meta1:pq1` | yes | yes | no | no | ≈ 8,000 (120k-131,753 mixed range, M1 C) |

Registered versions v2..v36 get their flags from the measured drift table (M1 C): `emits_v31_block` = version ≥ v31 (`0.1.48`, decision_id ≥ 166,585), `emits_missions` = version ≥ v36 (`0.3.3`), `emits_pending_queue` = true for all registered versions (present 100% from 140k), `emits_campaign_meta` = true. Migration asserts per snapshot that the observed key signature equals the version's flags; a contradiction fails the range (never silently coerced). Going forward the writer asserts the same at W1 (a collector that emits a new optional block requires a new `collector_version` row with a new flag column — a schema change, by design).

NULL semantics for optional blocks: `char_state_ext` row absent ⇔ `emits_v31_block = false`; `campaign_state.mission_set_id` NULL ⇔ `emits_missions = false`; `char_state.pending_queue_set_id` NULL ⇔ `emits_pending_queue = false`; `snapshot_campaign.difficulty/leader/selector` NULL ⇔ `emits_campaign_meta = false`. `snapshot_campaign.effect_bundle_set_id` NULL ⇔ `emits_v31_block = false`; `province_state.effect_bundle_set_id/plague_bundle_set_id` likewise.

## 2.6 Offers: identity stored, parameters derived

Evidence: `advisor/options.py:49-51, 54-68, 71-75, 141-150, 157-185, 311-328, 528-586, 596-612, 646-682` (opened to check these lines): every `params` value is either (i) a field copied verbatim from the state member the offer was generated from (skills → `level,total_levels,tier`; recruitable → `cost,disabled`; pending_queue → `turns_left`; hostiles/settlements/ruins → `x,y,faction,cqi`; buildable → `cost,upkeep,level,can_upgrade,can_afford_resources`; relations → `standing,at_war,allied,trade,their_vassal`; lord_pools → `traits,bg_skill,cqi,unit_key,cand_rank,cand_subtype`; anc_pool/equipped → `index,name,key`; tech → `cost`; rites → `index,invalid_reason`; move_tiles → `x,y,sample_index,reach_rays,reach_max`; horde_slots → `slot_id,slot_index,key,empty`), (ii) a value of the campaign/province state (`points_available`, `current_research`, `province`, `region`, `can_set_edict`, `hero_type_counts[agent_type]`), (iii) a one-line derivation (`at_max = level >= total_levels`, `is_upgrade = not empty`, `in_progress = key == current_research`, `is_selected = key == selected_edict`, `n_traits = len(traits)`, `trait = traits[0]`), or (iv) a reference lookup (`agent_type` via `_hero_subtype_types(faction)`). M2 B confirms no other keys exist (0 unparseable, no nested dicts).

Therefore an offer is identified by (`entity_seq`, `action_id`, `slot_index`) — `slot_index` is the only member selector not encoded in `action_key` (building offers: key = building level key, several slots possible; M2 B). Its parameters are regenerated at hydration by running the generator over the hydrated record and matching on that identity (08 §8.3). What is stored per offer: identity, order, scores. What disappears: `actions.params` (1.6 GB table; 95.7% is the move share of `actions` rows, M2 B; R1 H.6).

Fidelity check (12): for every decision in the 1000-campaign training window, hydrated params == `actions.params` for every offer (100% required); outside the window the mismatch rate is reported per collector version (generator drift is expected on old versions and affects no reader: R2 B4 readers take params of hydrated offers, R3 A.5 reads `level,total_levels,building_key,is_upgrade,cost,region,refund` which are state copies).

## 2.7 Set identity, canonical typed encoding, hash (C5)

- A **set** is the value of one collection of one entity at one snapshot: an ordered sequence of member tuples (order is information: `skills` sorted by tier, `stances`/`tech` stable order, `unit_cards` positional; M1 G "order kept 100%"). Two snapshots share a `set_id` iff their sequences are identical.
- **Canonical typed encoding** `enc(seq)`: for each member in list order, for each field in the member's declared field order (03), one tagged value: `N` (null), `F`/`T` (bool), `i` + int64 big-endian (integer class), `d` + IEEE-754 binary64 big-endian (measure class), `s` + uint32 length + UTF-8 (text and dictionary keys — the **key text**, not the dictionary id, so the hash is identical in the migrator and the live writer and across databases), `a` + uint32 count + elements (arrays), `o` + uint32 count + key/value tag pairs in sorted key order (nested objects: `lord_pools{}.traits[]` members are dicts, 0.5), `M` (field absent, distinct from `N` null per 2.8). Members are separated by `\x1e`, the sequence is prefixed by `kind` (uint16) and member count (uint32). Ordinals are never encoded (C5).
- **Hash** = SHA-256 of `enc(seq)`, 32 bytes, stored as `BYTEA`, UNIQUE per `kind`. State width 256 bits; with ≤ 2^26 sets per kind over the projected corpus the collision probability is < 2^-200; **collision policy: none needed** — on a hash hit the writer reuses the set without comparing members. The round-trip verification (12) would detect a collision as a hydration mismatch. SHA-256 is chosen over a 64/128-bit non-cryptographic hash because the hash *is* the identity for deduplication across the whole corpus lifetime; the cost (≈ 0.6 µs/KB) is negligible against 40 KB per decision.
- Empty sequence: one row per kind with `n = 0` (created by DDL).

## 2.8 Null vs missing vs empty (C8), per key path with ≥ 2 observed states (M1 B)

Encoding classes: **A** = key absent from the collector's dict; **N** = key present with JSON null; **E** = present and empty (`{}`/`[]`/`''`).

| role.path | states seen | encoding | preserved distinction |
|---|---|---|---|
| CB difficulty/leader/selector | A (pre-20k), value | column NULL = A (never N in data: M1 B) | reader: none reads them from the blob (R2 B1); round-trip needs A |
| CB presave_radius | N (2), value | SMALLINT NULL = N; always present | none |
| CB effect_bundles | A (pre-v31), E, value | set_id NULL = A; empty-set id = E | version flag |
| CB hero_type_counts / read_failures | E, value | empty-set id / no `read_failure` rows = E; always present | `_positions_data` sums counts (R3 B) |
| WB armies[].hp,stance,units | N (no army), value | NULL = N | readers test `has_army` (R2 B2) |
| WB armies[].region/province/region_owner | N (2.5%), value | NULL = N | `lord_in_own_territory` etc. tolerate None |
| WB armies[].ap_pct | N (ap_per_turn=0), value | REAL NULL = N | |
| WB stationed{region} | N value (79%), cqi | member row with `cqi NULL` = N; empty set = E | `settlement_occupied` gate reads `holder is not None` (options.py:179-180) — N vs absent key matters and is preserved |
| WB relations | N (READ_FAILED), E, list | `relation_set_id` NULL = N; empty set = E | `_diplo_options` distinguishes None (broken read) from empty (options.py:633-640) |
| WB diplo_unseen | N, E, list | `INT[]` NULL = N; `'{}'` = E | none |
| WB citizenry/settlements/ruins/enemy_agents/war_graph/hostiles/regions[].adjacent | E, list | empty array / empty set | |
| lord region | N, value | NULL | |
| lord horde_slots | N (95.5%), E?, list | set_id NULL = N (key always present); empty-set = E | `_horde_building_offers(None)` == `([])` (options.py:92-95) |
| lord/hero pending_queue | A (pre-140k), E, list | NULL = A (version flag); empty-set = E | memory.py stall flags |
| lord v31 block | A, values with N/E inside | `char_state_ext` row absent = A; inside: `background_skill_id` NULL = N; arrays/sets empty = E | |
| hero hp/units | N always | NULL | `_parse_lord` for heroes |
| province max_slots… (33 rows) | N, value | NULL | `settlement_present` is True on all (M1 A.5) |
| province province (str) | E (33), value | `province_id` NULL ⇔ '' (dictionary has no '' row; hydration emits '') | |
| province active_edict | E (83%), key | `active_edict_id` NULL ⇔ '' | `selected_edict` 'none' ⇔ NULL likewise (distinct columns, distinct literals documented in 03) |
| province slot_states[].key/health/max_health/refund/queued_key | N, value | NULL | |
| campaign current_research | N, key | NULL | |
| campaign missions | A (pre-v36), list | set_id NULL = A; empty-set = E | |
| campaign lord_pools{}.units[]/bg_skills[]/traits[][] | N, value | NULL elements / NULL array = N | |
| campaign rites[].invalid_reason | N, '' , text | `reason_id` NULL = N; dictionary row '' exists for E | `_offer(... reason or "cannot_perform")` (options.py:607-611) |
| campaign resources | value only | set | |
| interrupt campaign `_eval_ms` | present only on interrupt kind | `snapshot_campaign.eval_ms` NULL for decisions | R1 B.2: popped for decisions |
| interrupt world | 6 keys only | kind-dependent hydration: `snapshot_world` columns for the other 7 keys are NULL and not emitted | R4 B.3 |

Rule: no per-row key-set side table (C8). Every A/N/E distinction above is carried by (version flags, NULL, empty-set id, dictionary row for '') and the snapshot kind.

## 2.9 Boundary normalisation rule (C3) and round-trip fidelity

The collector emits Lua numbers as `%.14g` (R1 B), so integral quantities arrive as JSON ints from the `chars/setts/hostiles` channels but as Python floats (`12.0`) from `_num` parsing of eval strings (M1 F: WB `x,y,rank,units,cqi` int vs EB `x,y,rank,units` float, `cqi` string; slot indices string keys vs numeric fields; `enemy_agents[].cqi` string). The normalisation is applied **once, in `collect.py` before the record leaves the collector** (the same function is applied by the migrator to legacy blobs), by path class:

| class | rule | paths (M1 F) |
|---|---|---|
| id | `int(x)` (strings of digits and integral floats → int) | every `cqi` (`context_id`, `cqi`, `enemy_agents[].cqi`, `citizenry[]`, `stationed{}` values, `reach_chars{}` keys, `faction_cqi`, `lord_pools{}.cqis[]`), slot indices (`built{}` keys, `building_now{}` keys, `locked_slots[]`, `slot_states[].index`, `buildable[].slot_index`, `horde_slots[].slot_index`) |
| count/level/rank/index | `int(x)` when `float(x).is_integer()`; a fractional value at an integer-class path is a **collector error → CollectError** (never rounded) | rank, skill_points, units, pending_recruits, ap_remaining, ap_per_turn, loyalty, x, y, xp, xp_next_level, subterfuge, zeal, authority, resurrection_turns, turn, settlements, armies, lord_level, allies, vassals, power_rank, difficulty, presave_radius, level/tier/total_levels, points/threshold_points, trait_progress values, turns_remaining, turns_left, avail, max_slots, free_slots, buildings, settlement_level, development_points, public_order, corruption values, health, max_health, research_points, n, ranks, index, dist, standing |
| money | `int(x)` (integral on 100% of samples; the game's treasury is integer) | income, treasury, gross_income, growth_per_turn, province income, resources values, upkeep, cost, refund, repair_cost |
| measure | `float(x)` | ap_pct, hp, strength_pct |
| bool | must already be bool (collector emits bool) | all boolean paths |
| text | str | keys, names, reasons |

`x = y = 65535` is an in-band off-map sentinel (M1 F: 35 WB rows, hero x/y max 65535) — kept as the value 65535 in an `INTEGER`/`SMALLINT`-wide column? No: 65535 exceeds `SMALLINT`; coordinates are `INTEGER` with `CHECK (x BETWEEN 0 AND 65535)`; readers keep seeing the number they see today (features compute with it).

**Canonical record** `canon(rec)` = the record after the normalisation above, `json.dumps(sort_keys=True, separators=(",",":"))`. Round-trip fidelity (C11) is defined as `canon(normalise(hydrate(snapshot_id))) == canon(normalise(json.loads(blob)))` for every migrated snapshot and every role (CB, WB, EB per entity, ICB, IWB) -- normalisation is applied to **both** sides. Amendment (0.4, measured): a per-path inverse type cannot reproduce the blob text, because `armies[].hp` and `hostiles[].hp` carry an int for some members and a float for others inside one blob (1,437 of 2,070 WB blobs in the M1 sample); reproducing that would need a per-value type flag, which C2/C3 forbid. Normalising both sides makes the comparison well-posed and is 100% on the sample with 0 semantic differences. The blob's int-vs-float rendering is an artefact of the collector's `%.14g` output, not information. It is exact in canonical form because: dictionary ids map back to the exact key text; measures are stored as `REAL`/`DOUBLE PRECISION` chosen so that `float(str)` round-trips (hp/strength_pct/ap_pct come from `%.14g` text with ≤ 6 significant digits, M1 F; `DOUBLE PRECISION` is used for `ap_pct`, `hp`, `strength_pct`, `ts` to make the argument trivial: binary64 stores every `%.14g` value exactly enough that Python `repr` reproduces the same text); ints are exact; NULL/absent/empty per 2.8; list order per member `ord`; dict key order is irrelevant (`sort_keys`). Panel blobs are **not** round-tripped whole: 03 keeps only the panel keys a reader consumes (R2 B5, R4 H.2) and validation for interrupt panels is over that projection (C2: unread payload columns are dropped).
