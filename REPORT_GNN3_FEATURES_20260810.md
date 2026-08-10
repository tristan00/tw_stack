# mapgraph3 — entities, edges, and candidate features

Measured 2026-08-10 against `D:\twdata\runs\human\run\decisions.sqlite` (22,136 decisions,
96,737 entity snapshots, **9,013,360 offers**, 583 campaigns, max turn 13) and
`D:\twdata\reference\reference.sqlite`. Graph statistics are from 653 real graphs built
with `build.build_graph`; offer statistics are full-table unless noted.

**Nothing here has been implemented.** This is the inventory you asked for before feature
work starts.

---

## 0. Read this first: six defects worth more than any new feature

The graph is not currently realising the design it already has. Every item below is a
measured fact about the shipped code, and each is cheaper to fix than any feature is to
add.

| # | Defect | Measured | Cost to fix |
|---|---|---|---|
| **D1** | **`building` offers attach no building node.** `build.py` reads `params.building_key` for the five building action types, but for `action_type='building'` that key is **absent in all 181,754 offers** — the real key is in `action_key`, and it joins the catalogue **912/912**. | `act_on` fires on **0 / 5,022** building offers. 4.4 building nodes/graph against 8.5 distinct building keys actually offered. | one line |
| **D2** | **`move` is unlearnable.** 13,188 move offers; the action node carries `available` (always 1.0) and two edges, and `params.x/y` are dropped. | **100.0%** of move offers are byte-identical to a sibling in the same graph. | needs a destination node or an x/y field on the action |
| **D3** | **`ritual` identity is a list position.** Rite keys are literally `rite_index_1 … rite_index_109`; `params` holds only `{rite_index: N}`. **0/109 join** `rituals` (1,326 rows). | 367,961 offers, ~21 ritual nodes/graph, all semantically empty. `rite_index_1` for Khorne and for Dwarfs share one embedding. `schema.py:18` lists `rite_index` among things *deleted* as "list positions, no semantics" — it came back as the identity. | recorder change |
| **D4** | **`item` is one shared node.** `params.item_key == "nil"` in **100%** of 95,178 item offers. Real identity is the unread `params.item_name` (31–261 distinct). | the whole `item` catalogue resolves to a single id | recorder change |
| **D5** | **`besieging` never fires — 0 edges in 653 graphs.** It looks up `s:<region>`, but settlement nodes are built only from `world.settlements`, which is **my own** settlements. A besieged settlement is an enemy settlement, which arrives as `world.hostiles[kind=settlement]` and is dropped. | 7 lords with `besieging=True` → 0 edges. **1,053 enemy settlements (21.8% of hostiles) get no node at all.** | needs enemy-settlement nodes |
| **D6** | **`garrison` targets never resolve.** The key is `settlement:<region>`; `_target_of` looks up `"s:" + key` without stripping the prefix. | **1,973 / 1,973 = 100%** unresolved | one line |

**Structurally identical siblings — the headline number.** Fingerprinting each action node
by its identity, scalars and outgoing edges within its own graph:

| action_type | offers | in a duplicate class |
|---|---:|---:|
| move | 13,188 | **100.0%** |
| recruit_unit | 7,386 | **100.0%** (local/global collapse) |
| recruit_hero | 6,270 | **100.0%** |
| stance | 8,975 | **98.4%** (15 distinct stances, no identity on the node) |
| horde_building | 11,586 | 96.8% |
| building | 5,022 | 80.4% (D1) |
| **all offers** | **343,944** | **18.8%** |
| **the action actually TAKEN** | 674 | **39.5%** |

Roughly **two in five training labels sit on a candidate the model cannot distinguish
from at least one alternative it did not pick.** No feature added elsewhere can recover
that; the listwise loss is being asked to separate identical rows.

**Three declared relations are never emitted by any code**: `tech_requires`,
`researching`, `unlocks`. Nine more emit zero edges in practice (`besieging`,
`dip_allied`, `dip_mil_ally`, `dip_def_ally`, `hinder_character`, `hinder_province`,
`assist_province`, `command_force`, `passive_ability`) — 12 of 46 forward relations, 26%.

**Corpus vintage warning.** The region layer — region nodes, `x`/`y`, `adj`, `owns_region`,
`in_prov` — is **absent below `decision_id ≈ 6,711`, i.e. for 30% of the corpus**.
`world.armies[].rank` is absent for 57%. Four of the nine diplomacy flags are absent for
86%. Anything built on those trains on a minority of the corpus with a silent zero
elsewhere.

---

## 1. Node types — 9 instance + 10 catalogue

Counts per graph over 653 graphs.

| # | Node | One instance is | p50/graph | mean | Scalars now | Identity embeddings |
|---|---|---|---:|---:|---|---|
| 1 | `faction` | a faction this decision knows | 10 | 15.9 | `is_player` | race, own |
| 2 | `region` | a campaign-map region | 12 | 13.3 | `x`, `y`, `public_order` | own |
| 3 | `settlement` | **my own** settlement | 2 | 1.7 | `garrison_units` | own (always true) |
| 4 | `province` | a province | 7 | 7.6 | `corruption` (max of 7) | — |
| 5 | `slot` | a building slot | 8 | 6.3 | — | — |
| 6 | `lord` | character with an army | 5 | 6.5 | `x,y,units,hp,rank,ap_pct` | agent, stance, subtype |
| 7 | `hero` | character without an army | 1 | 2.1 | `x,y,rank,ap_pct` | agent, stance, subtype |
| 8 | `action` | one offer | 419 | 526.7 | `available` | atype, term |
| 9 | `cgroup` | (ego, action_type) candidate group | 30 | 35.2 | — | — |
| 10 | `building` | catalogue building key | 4 | 6.1 | — | cat |
| 11 | `chain` | building chain | 4 | 3.1 | — | cat |
| 12 | `unit` | unit key | 18 | 21.5 | — | cat |
| 13 | `tech` | tech key | 59 | 59.9 | — | cat |
| 14 | `skill` | skill key | 81 | 83.5 | — | cat |
| 15 | `ritual` | **`rite_index_N`** (D3) | 8 | 22.8 | — | cat |
| 16 | `agent_action` | hero-action key | 9 | 7.9 | — | cat |
| 17 | `edict` | edict key | 4 | 3.8 | — | cat |
| 18 | `item` | **always `nil`** (D4) | 1 | 0.9 | — | cat |
| 19 | `race` | race code | 7 | 8.6 | — | cat |

Whole graph: **p50 720 nodes / 2,984 edges / 419 action nodes**. Catalogue nodes are 28%
of all nodes.

**Entities in the record with no node at all:** enemy settlements (1,053 per 653
decisions), ruins/razed sites (732), enemy agents (662).

---

## 2. Edge types — 46 forward, 92 with reverses

Forward edges per graph, 653 graphs. `0.0%` = never fires.

**world** — `adj` 16.4 · `in_prov` 13.3 · `owns_region` 13.3 · `of_race` 15.4 ·
`owns_char` 8.6 · `in_province` 8.2 · `near` 15.7 (k-NN, K=4) · `at_region` 2.6 ·
`sett_of` 1.7 · `owns_sett` 1.7 · `at_sett` 0.8 · **`besieging` 0.0%** (D5)

**diplomacy** — `dip_met` 13.3 · `dip_war` 1.25 · `dip_nap` 0.70 · `dip_vassal` 0.11 ·
`dip_trade` 0.09 · `dip_mil_access` 0.04 · **`dip_allied` 0.0%** · **`dip_mil_ally` 0.0%** ·
**`dip_def_ally` 0.0%** (0/22,136 corpus-wide — dead everywhere)

**province** — `has_slot` 8.4 · `slot_locked` 4.5 (self-loop) · `slot_filled` 3.4 ·
`of_chain` 3.4 · `slot_building` 0.20

**catalogue** — `queued` 0.57 · **`tech_requires` 0.0%** · **`researching` 0.0%** ·
**`unlocks` 0.0%** — the last three have no emitting code at all

**ability** — `assist_army` 7.4 · `hinder_settlement` 4.8 · `hinder_army` 2.3 ·
`hinder_agent` 2.1 · the other five **0.0%** (`params.ability` has exactly 4 values)

**act** — `act_actor` 526.7 · `in_group` 526.7 · `act_on` 283.8 · `act_target` 192.5 ·
`act_subject` 156.6 · `of_ego` 35.2 · `act_slot` 32.8

---

## 3. Suggested features, per entity

Legend — **R** relation (preferred by this design) · **C** category needing an embedding ·
**S** scalar. **Feas:** EASY = already in the record or in `reference.sqlite` · FIX = code
only · RON = needs the pack extractor (see §5) · BLOCKED = needs a recorder change first.
**⚠CB** = the same raw value is already a CatBoost feature.

### The budget that actually constrains this
`MAX_FIELDS` is **6** (set by `lord`), and `net.py` scatters into a dense
`[N, 19 × 6]` block. **Any node type can take up to 6 scalars at zero cost** — the matrix
does not widen. The 7th scalar on *any* type widens the input for *every* node in the
graph. That, not `N_SCALARS`, is the line to watch.

### lord / hero
| Kind | Feature | Source | Feas | ⚠CB | Note |
|---|---|---|---|---|---|
| S | `acted` | `state.acted`, 100% fill | EASY | ⚠ | has this character already acted — strong availability signal |
| S | `skill_points` | `state.skill_points`, 0–4 | EASY | ⚠ | gates all 72,811 `skills` offers; `no_points` is 20.4% of all gates |
| S | `ap_remaining` / `ap_per_turn` | `state.*`, 100% | EASY | — | `ap_pct` is carried; these are the numerator/denominator |
| C | `is_leader`, `is_general` | 100% | EASY | ⚠ / — | |
| S | `in_own_territory` | 100% | EASY | ⚠ | attrition/replenishment gate |
| R | `char → faction` (region owner) | `armies[].region_owner`, 95.6% | EASY | — | whose land am I standing in |

### province
| Kind | Feature | Source | Feas | ⚠CB | Note |
|---|---|---|---|---|---|
| S | **corruption as 7 values, not 1** | `state.corruption` dict, all 7 keys 100% filled | EASY | ⚠ | the graph collapses seven distinct corruption types into their max |
| S | `settlement_level` | 0–3, 100% | EASY | ⚠ | gates which buildings are constructible |
| S | `complete_owner` | 100% | EASY | ⚠ | gates edicts — 3,288 offers blocked on `province_not_complete` |
| S | `can_set_edict`, `is_capital` | 100% | EASY | ⚠ | |
| R | `province → edict` | `selected_edict` / `active_edict` | EASY | ⚠ | edict is already a node type |
| S | `building_now.turns_left`, `.paused` | 12.4% fill | EASY | ⚠/— | scalars on an edge that already exists |

### region / settlement
| Kind | Feature | Source | Feas | ⚠CB | Note |
|---|---|---|---|---|---|
| S | `capital`, `abandoned` | `regions[]`, 100% (32.8% / 8.4% true) | EASY | ⚠/— | `abandoned` is the colonisation surface |
| S | settlement `x`,`y` | `settlements[]`, 100% | EASY | ⚠ | the settlement node currently has **no position at all** |
| **R** | **enemy settlement nodes** | `hostiles[kind=settlement]`, 1,053 in 653 decisions | FIX | ⚠ | fixes D5 and gives `attack_settlement` a real target |
| R | ruin nodes → region | `world.ruins[]`, 732, unread by **both** models | EASY | — | |

### faction
| Kind | Feature | Source | Feas | ⚠CB | Note |
|---|---|---|---|---|---|
| S | `standing` | `relations[].standing`, −239…+123, 100% | EASY | ⚠ | the **only quantity** on a diplomacy edge; the graph carries booleans only |
| R | **`researching`** | `params.current_research`, 64% fill | FIX | — | the declared relation that is never emitted |
| R | faction → resource node | `campaign.resources`, 63 stable game keys | EASY | ⚠ | per-race mechanic pools; key is stable so it is a catalogue-node shape, not 63 columns |
| R | faction → subculture → culture | `factions_tables` + `cultures_*` | RON | — | the current 24-way race token is flat |
| R | faction → permitted agent subtypes | `agent_permitted_subtypes`, 9,187 rows, already in reference.sqlite, unused | EASY | — | would ground the 1024-bucket hashed `subtype_idx` |

### action
| Kind | Feature | Source | Feas | ⚠CB | Note |
|---|---|---|---|---|---|
| **C** | **`gate`** | 61% of offers, **33-value closed vocabulary** | EASY | ⚠ | the single largest unread categorical in the record — *why* an offer is unavailable |
| S | `chance` (hero_action) | 30–100, 100% | EASY | ⚠ | success probability |
| S | `pool_avail` (RoR/raise_dead/imperial) | 100% | EASY | — | hard availability gate |
| C | `queue` local vs global (recruit_unit) | 100% | EASY | ⚠ | **the only thing separating two otherwise identical offers** — 100% duplicate class |
| C | `is_upgrade` (building) | 100% | EASY | — | |
| C | `attribute` (hero_action) | zeal/subterfuge/authority | EASY | — | 3-way; the axis `ABILITY_RELATIONS` does not encode |
| S | `cost`, `points_available` (research) | 100% | EASY | ⚠ | |
| R | `act_slot` for horde | `params.slot_id`, 100% | FIX | — | `act_slot` is 0% for horde today |
| — | stance identity | 15 distinct stances, no node identity | FIX | ⚠ | 98.4% duplicate class |

### building / chain
| Kind | Feature | Source | Feas | ⚠CB |
|---|---|---|---|---|
| **R** | **`of_chain` on offered buildings** (not just built) | fix D1, then existing `catalogue.chain_of` | FIX | — |
| **R** | `chain → superchain` | `building_chains.superchain` — **already loaded, never used** (`catalogue.superchain_of`) | FIX | — |
| C | `chain_category` (money/military/happiness) | `building_chains` | EASY | ⚠ |
| S | `level`, `create_cost`, `create_time`, `dev_point_cost` | `buildings` | EASY | ⚠ |
| R | building → unit / building → effect | `building_units_allowed_tables`, `building_effects_junction_tables` (21,613) | RON | — |

### unit
| Kind | Feature | Source | Feas | ⚠CB |
|---|---|---|---|---|
| C | `caste` (11), `category` (6), `class` (9) | `units` | EASY | ⚠ |
| S | `tier` 1–5 | `units` — real ordering: mean recruit cost by tier 314/591/928/1384/1994 | EASY | ⚠ |
| **R** | **unit → merc/RoR pool** | `merc_units`, **361/519 observed units have a row** (renown 255, raise_dead 42, imperial_supply 31, blessed 21) — in reference.sqlite, unused | **EASY** | — |
| R | RoR → base unit | `unit_set_to_unit_junctions_tables` (21,476) | RON | — |

### tech
| Kind | Feature | Source | Feas | ⚠CB |
|---|---|---|---|---|
| **R** | **`tech_requires` — the prerequisite DAG** | `technology_node_links_tables`, **1,792 rows**, `(child, parent, visible_in_ui)` | RON | — (CatBoost has only `required_parents`, a *count* — **no edges**) |
| C | `node_set` (30 values) | `tech` — the named sub-trees; the `_mil`/`_civ` suffix carries the axis the boolean columns lost | EASY | ⚠ |
| S | `research_points_required`, `cost_per_round` | `tech` | EASY | ⚠ |
| — | `tier` | **do not use** — 998/999 sentinels, 213 NULL; the wiki gives techs no tier | — | ⚠ |

### skill
| Kind | Feature | Source | Feas | ⚠CB |
|---|---|---|---|---|
| **R** | key-grammar tokens → race / lord\|hero / category | `wh<N>_<set>_skill_<race>_<lord\|hero>_<campaign\|battle\|self\|assist\|passive\|hinder\|army>`, 89% carry a race | **EASY** (pure string split) | — |
| S | `unlocked_at_rank` 0–26 | `skills` — the only live column on offered skills | EASY | ⚠ |
| R | skill prerequisite DAG | `character_skill_node_links_tables` (20,590, has explicit `link_type=REQUIRED`) | RON | — |
| R | `unlocks` (skill → agent_action) | `agent_action_unlocks.sqlite` — **only 10 of 3,231 observed skills covered (0.3%)**. Better source: `params.skill_unlocked`, present on every hero_action offer | EASY | — |

### agent_action
| Kind | Feature | Source | Feas | ⚠CB |
|---|---|---|---|---|
| C | `attribute` (3), `agent` (6) | `agent_actions` **and** `params` | EASY | ⚠ |
| S | `chance_of_success` | verified identical to `params.chance` for all 72 keys — it is the static base | EASY | ⚠ |
| R | agent_action → action_result → outcome | `action_results` (74) + `action_result_outcomes` (75), joinable | EASY | — |

### edict / item / ritual
| Node | Status |
|---|---|
| `edict` | **no table exists** in reference.sqlite. Race token from the key (95/97) is EASY. Real archetypes need `provincial_initiative_records_tables` (117) — RON. Only ~97 edicts, so a full effect vector per edict is tiny. |
| `item` | **BLOCKED on D4.** Nothing attachable while the key is `nil`. |
| `ritual` | **BLOCKED on D3.** Once real keys exist: `category` (166 values), `cooldown_time`, `influence_cost`, `expended_resources` (587 distinct → a resource relation). |

---

## 4. Feasibility summary

| Class | Data complete? | New extractor? | Static (shared node) or per-decision? | Verdict |
|---|---|---|---|---|
| D1, D6, horde `act_slot`, `researching`, `of_chain`, `chain→superchain` | yes | no | static | **do first — code-only, data already present** |
| `gate`, `standing`, `acted`, `skill_points`, `corruption`×7, `settlement_level`, `chance`, `queue`, `pool_avail`, `attribute`, skill key-grammar, `merc_units` pool, permitted subtypes | yes | no | mixed | **EASY — on disk or already in `params`** |
| `tech_requires`, skill prereq DAG, RoR→base unit, edict archetypes, effect junctions | complete in `db.pack` | **one-time ron converter (~25 lines)** | static | **same-day work — see §5** |
| D3 (rituals), D4 (items) | **no** | recorder change | static | blocked at the source; 463k offers currently meaningless |
| region adjacency beyond runtime `adj` | **not available** | — | — | confirmed absent from all 1,521 pack tables; adjacency is geometric (map bitmap), not relational |

**Cost model.** A static scalar on a catalogue node is ~186 float writes/graph — negligible.
A catalogue↔catalogue relation like `tech_requires` adds ~60–120 edges to a ~2,944-edge
graph, **2–4%**. A per-decision scalar must live on the action node (419/graph) and breaks
the sharing that makes catalogue nodes cheap. A **shared effect node type** is the only
proposal here with real inflation risk and should be measured before it is built.

---

## 5. The finding that changes feasibility

`D:\twdata\reference\ui3_extraction\schema_wh3.ron` — a 10.77 MB RPFM schema,
**1,663 tables / 15,432 field definitions**, using exactly the 11 field types
`build_reference._read_field` already implements. **It is referenced nowhere in the repo**
(zero grep hits for `schema_wh3` or `.ron`). Verified: **1,520 of 1,521** pack tables
decode CLEAN-EOF from it at the exact installed pack version, zero version mismatches;
the one uncovered table has 0 rows. Caveat: each version block ends with
`localised_fields:` which are not in the binary and must be cut.

So **"needs pack parsing" is no longer a real category.** The hand-written 25-table
`schema_db.json` becomes a ~25-line ron→json converter and all 1,521 tables become
extractable — including the tech DAG, the skill DAG, edicts, and the effect junctions.

**Suspected extractor defect, flag before trusting `technologies_tables`:** `is_military ≡ 1`
and `is_civil ≡ 0` across all 2,056 rows while `node_set` plainly contains `_civ` sub-trees;
`building_level` is one constant string in 97% of rows. CLEAN-EOF proves total byte length,
not field alignment.

**Wiki disagreements worth knowing:** `rituals.slave_cost ≡ 0` across all 1,326 rows, but
Dark Elf rites cost 400–800 slaves each — do not use that column. `buildings.level` is only
interpretable relative to the host settlement's chain level, which is a graph constraint,
not a node scalar. Static costs are systematically wrong by a race- and state-dependent
margin (public order ±10%, global recruitment doubles cost, Tomb Kings and Beastmen have
no upkeep at all).

---

## 6. CatBoost overlap ledger

The hard rule is that the GNN does not transcribe `advisor/features.py`. Making the
overlap visible is the point, so:

**The docstring claim is false.** `catalogue.py:13` and `schema.py:22` say "CatBoost reads
two columns of this database." In fact `features.py:386-401 → features_db.py:24-57` does
`SELECT *` on **buildings, building_chains, tech, units, skills, rituals** and emits every
column as `opt_db_<col>`. **Every scalar in those six tables is already a CatBoost feature.**
`features.py` reads 107 distinct raw record keys in total.

| | Items |
|---|---|
| **Already CatBoost** | every scalar/category listed above from those six tables; all ten `g_ctx` fields; `gate`, `standing`, `acted`, `skill_points`, `is_leader`, `in_own_territory`, `settlement_level`, `complete_owner`, `can_set_edict`, `selected_edict`, `active_edict`, corruption (all 7 + total + max), `is_researching`, `resources`, `cost`, `chance`, `queue`, `active`, `capital` |
| **Genuinely new to both models** | **all the relations**: `tech_requires` (CatBoost has only a count), skill prereq edges, `chain→superchain` as an edge, building→unit, RoR→base unit, unit→merc pool, faction→permitted subtypes, agent_action→result→outcome, edict effect vectors, any shared effect node. Plus `world.ruins[]`, `regions[].abandoned`, `armies[].region_owner/ap_per_turn/ap_remaining/is_general`, `relations[].excluded`, `building_now.turns_left/paused`, `params.current_research/in_progress/pool_avail/slot_id/slot_empty/is_upgrade/item_name/attribute`, `campaign.hero_type_counts`, and the six reference tables CatBoost never opens (`merc_units`, `agent_permitted_subtypes`, `action_results`, `action_result_outcomes`, `agent_types`, `captive_*`) |

**The honest reading: the scalar half of this report is almost entirely CatBoost overlap,
and the relational half is almost entirely new.** That matches the design's own stated
principle — anything that can be a relation is a relation — and is why `tech_requires` and
D1 rank far above any cost or tier scalar.

**Explicitly excluded, not proposed:** `world.hostiles[].dist` (a cross-entity derived
value `guard.Raw` would refuse to construct, *and* a CatBoost feature) and
`params.reach_max` / `reach_rays` / `sample_index` (the v2 reach features `guard.py` was
written to make unwritable).

---

## 7. Recommended order

1. **D1** — one line, unlocks ~9 building + ~9 chain nodes/graph.
2. **D6**, horde `act_slot`, `researching`, `of_chain` on offers, `chain→superchain` — code only, data present.
3. **`gate`** as a category on the action node — the largest unread categorical, 33 values.
4. **The duplicate-sibling problem** (`move`, `stance`, `recruit_unit` queue, `recruit_hero`) — 39.5% of labels sit on an indistinguishable candidate. Fixing this is worth more than any new scalar.
5. **The ron converter**, then `tech_requires` and the skill DAG.
6. **D5** — enemy settlement nodes; fixes `besieging` and gives `attack_settlement` a target.
7. **D3 / D4** — recorder changes for rituals and items; 463k offers currently carry no identity.

Before any of it: the held-out metric cannot currently detect a regression between
retrains (`grouped_split` re-permutes when the corpus grows — only ~21% of the previous
holdout survives, and two identical retrains 12 rows apart moved 0.0945 nats). Fix the
split first, or none of these changes can be evaluated.
