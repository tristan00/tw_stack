# M1 -- corpus blob profile (sampled, read-only)

Report file not written (plan mode forbids writes outside the plan file); this message is the report.

## 0. Sample, corpus, method
- Corpus (measured): `decisions` 206,907 (ids 1..206,945), `campaigns` 4,827, `entities` 1,130,208 (campaign 206,907; hero 240,388 in 192,516 decisions; lord 314,467 in 199,160; province 368,446 in 191,904), `interrupts` 43,392 (campaign_blob 43,392 / world_blob 43,386 / panel_blob 35,533), `blobs` 951,161 rows, `sum(n)` = 8,472,211,819 chars, `pg_total_relation_size('blobs')` = 2,504,409,088 B.
- Sample: `decision_id % 100 = 7` -> 2,070 decisions in 2,013 campaigns; blobs CB 2,070, WB 2,070, EB 11,250 (lord 3,102, hero 2,411, province 3,667, campaign 2,070). `interrupt_id % 20 = 3` -> 2,170 interrupts (ICB 2,170, IWB 2,169, IPB 1,786). Change-rate rerun: `campaign_id % 24 = 5` with >= 8 decisions -> 196 campaigns / 9,468 decisions (original: `% 12 = 0`, 376 / 17,113).
- Fetch SQL (per role): `select d.decision_id,d.campaign_id,d.version_id,b.n,b.z from decisions d join blobs b on b.blob_id=d.campaign_blob where d.decision_id % 100 = 7` (same with `world_blob`); entities: `... from decisions d join entities e on e.decision_id=d.decision_id join blobs b on b.blob_id=e.features_blob where d.decision_id % 100 = 7`; interrupts: `select i.interrupt_id,i.kind,b.n,b.z from interrupts i join blobs b on b.blob_id=i.<col> where i.interrupt_id % 20 = 3`.
- Census core (Python, run via `decisions.pg.connect(autocommit=True, readonly=True)`, `SET statement_timeout='600s'`):
```
WILD={'resources','hero_type_counts','read_failures','stationed','reach_chars','reach_setts','built','building_now','corruption','merc_pools','lord_pools','trait_progress'}  # identifier-keyed dicts -> path{*}
def typ(v): None->'null'; bool->'bool'; int->'int'; float->'fint' if v.is_integer() else 'ffrac'; str; list; dict
def visit(path,v,ver): n=nodes[path]; n.present+=1; n.vers[ver]+=1; n.types[typ(v)]+=1
  str: distinct set (cap 5001), maxlen, 2 samples | num: min,max, first fractional example
  list: count,sum len,max len, elem types; if all-dict: for c in [key,cqi,region,index,slot_id,slot_index,faction,mission,subtype,kind,name,x] present in every element -> lists with dup ids; whole-element dup via json.dumps(sort_keys); for every common field with comparable types -> asc/desc sorted counts; recurse path+'[]'
  dict: if lastname(path) in WILD: key count stats, distinct keys, recurse path+'{*}' else recurse path+'.'+k
presence% = present / parent occurrences (dict count | list elements | dict keys); absent = parent - present; drift = per-version presence (versions with >=30 parents) spread > 5 pts
```
- Run cost: fetch + parse + census of all 15,459 decision-role blobs ~8 s wall; interrupts ~1 s.

## A. Key census (type: `fint` = JSON float with integral value, `ffrac` = fractional float, `int` = JSON integer). Presence = % of parent unless stated. All paths are 100 % present unless `abs=` shown.

### A.1 Campaign blob (CB) -- 25 keys, 30 paths, 2,070 blobs
| key | type | detail |
|---|---|---|
| allies / vassals / armies / settlements / lord_level / turn | fint | 0..1 / 0..2 / 1..9 / 0..7 / 1..12 / 1..17 |
| income / treasury / power_rank | fint | 0..4566 / -571..40281 / -80..-1 |
| difficulty | fint, always -3 | abs=62 (3.0 %, all decision_id < 20,000) |
| leader | str dist 103 maxlen 30 | abs=64 (same rows) |
| selector | str dist 7: ucb:1 74 %, ucb:2, ucb:3, ucb:0.9, ucb:0.8, ucb:2.5, ucb:4 | abs=58 |
| presave_radius | fint always 150 | null=2 |
| faction / faction_cqi | str dist 103 (maxlen 41) / numeric str dist 103 maxlen 3 | |
| campaign_uuid | str dist 2013 maxlen 65 | |
| campaign_map | str dist 2 (`wh3_main_combi`, `wh3_main_chaos`) | |
| game_version | str, 1 distinct `8.1.1.0` | |
| defeated / is_researching / ll_wounded | bool | |
| resources | dict keys 5.4 mean / 34 max, 212 distinct keys -> fint -1..5947 | never empty |
| hero_type_counts | dict keys 1.1/3, 7 distinct (agent types) -> int 1..3 | empty 114 (5.5 %) |
| read_failures | dict | `{}` in 2070/2070 |
| effect_bundles | list 3.6/9 of {key str dist 227 maxlen 74, turns_remaining fint 0..24}; id key unique; unsorted | abs=1642 (present only v31+) |

### A.2 World blob (WB) -- 13 keys, 95 paths, 2,070 blobs
| key | shape | element facts / order |
|---|---|---|
| armies | list 4.7/13 of 18-key dict | cqi int 1..1550 unique, lists sorted by cqi asc 98 %; agent_type str 8 (colonel 39 %, general 35 %, champion, engineer, runesmith, wizard, dignitary, spy); has_army bool (74 % true); ap_pct 0..1 fint/ffrac null=520 (exactly the rows with ap_per_turn=0); ap_per_turn / ap_remaining fint 0..4723; hp int|ffrac 0.01..20 null=2469 (= rows without army); stance str 16 null=2469; units int 1..20 null=2469; rank int 0..12; region / province / region_owner str null=245 (2.5 %, incl. 35 rows with x=y=65535 sentinel); subtype str 287; x int 11..65535, y int 14..65535; in_own_territory/is_general/is_leader bool |
| settlements | list 1.9/7 {region str 318 unique, capital bool, units int 0..20, x,y int} | empty 112 (5.4 %); order stable |
| hostiles | list 7.8/48 (IWB max 60 = cap) | 3 row shapes by `kind` (5 values): settlement 8,037 rows `{dist,faction,kind,region,units,x,y}`; army / neutral_army 7,217 `{cqi int,dist,faction,hp,is_armed_citizenry,kind,province(null 95),stance,units,visible,x,y}`; hero / neutral_hero 903 `{agent_type,cqi,dist,faction,kind,province,subtype,visible,x,y}`; no single identity (cqi 50 %, region 50 %); dist int 2..92438; not sorted by dist; empty 18 |
| ruins | list 3.0/38 {region unique, x,y fint} | empty 611 (29.5 %) |
| regions | list 14.6/75 {region str 769 unique, province str 294, owner str 296 (incl. `rebels`), capital, abandoned bool, x,y fint 13..1373/12..953, adjacent list 3.3/14 str (empty 4.3 %)} | not sorted; relative order stable 100 % between consecutive snapshots |
| enemy_agents | list 0.4/8 {cqi STR dist 185, faction, at_war bool, x,y fint} | empty 1552 (75 %) |
| war_graph | list 4.2/23 {faction unique, at_war_with list 1.3/5 str} | empty 32 |
| relations | list 9.4/34 {faction str 330 unique, standing fint -430..217, allied/at_war/def_ally/excluded/mil_access/mil_ally/nap/our_master/their_vassal/trade bool} | never null in sample (R1: None on READ_FAILED) |
| diplo_schema / diplo_hostile_rows | int 2 always / int 0..48 = len(hostiles) 2070/2070 | |
| diplo_unseen | list str | empty 2063 (99.7 %); 7 elements total |
| citizenry | list 1.8/6 numeric str, sorted asc 100 % | empty 121 |
| stationed | dict keys 1.9/7 (= settlement regions, 318 distinct) -> null 3158 (79 %) / numeric str 820 | empty 112; the 820 cqis are never members of `citizenry` (820/820) -- contradicts R1 B.3 "garrison-commander cqis" |

### A.3 Lord entity (EB lord) -- 47 keys, 114 paths, 3,102 blobs
Scalars (all 100 %): acted, besieging, garrisoned, is_leader, wounded bool; cqi numeric str dist 274 maxlen 4; rank fint 1..12; skill_points fint 0..8; units fint 1..20 (== len(unit_cards) 3102/3102); pending_recruits fint 0..8 (== len(pending_recruit_keys) 3102/3102); ap_pct fint/ffrac 0..1 (25 % fractional); ap_per_turn, ap_remaining fint 0..3988 (ap_pct == ratio in 2633; ap_per_turn = 0 in 469 = 15 %); hp ffrac/fint 0.01..20 (53 % fractional); loyalty fint 0..9; stance str 16 (DEFAULT 54 %); subtype str 175; region str dist 382 null=85 (2.7 %); x fint 14..1422, y 14..958.
v31+ block (present 20.6 %, abs=2463): xp fint 0..22105, xp_next_level 0..24900, subterfuge / zeal / authority fint 1..3, resurrection_turns always 0, upkeep 0..3073, background_skill str dist 176 null 13; hidden_skill_states list 2.7/7 {key, level 0..11, total_levels -1..49} sorted total_levels desc 93 %; traits list 0.1/3 {key, level -1..3, level_key, points, threshold_points 1..12, chaos_realm bool} (empty 580/640); trait_progress dict 4.2/13 keys (96 distinct) -> fint 1..12 (empty 78/639); effect_bundles list (empty 606/639); force_effect_bundles list 1.5/5 {key str 73, turns_remaining 0..19} (empty 266 of ... nonempty 21 %); armory list str (empty 636/639).
| collection | shape | identity / order / empties |
|---|---|---|
| skills | list 50.2/68 {key str 2383 maxlen 80, status str 5 (inactive 71 %, locked_due_to_rank 15 %, active 14 %, locked_by_item, locked_by_skill), level fint 0..3, total_levels 1..3, tier fint 0..30} | key unique 100 %; sorted tier asc 100 %; order stable 100 %; empty 74 |
| stances | list 8.7/11 {key str 18, active, can_activate, can_afford bool} | key unique; not sorted; order stable 100 % |
| recruitable | list 3.3/12 {key str 232, state always 'active', cost fint 100..1500, disabled bool} | key unique; empty 553 (18 %) |
| unit_cards | list 6.7/20 {key str 738, category str 6 (inf_melee 68 %), strength_pct fint/ffrac 0..100, xp fint 0..8} | key NOT unique (dup in 64 % of lists), whole-element dup 63.6 % -> position is the identity; order stable 100 % |
| pending_queue | present 40.0 % (abs=1860, absent in decision_id < ~130k) list 0.3/6 {key, turns_left fint 0..5} | key dup 22.7 %, whole dup 45.6 %; sorted turns_left asc 99 %; empty 1013/1242 |
| pending_recruit_keys | list 0.2/8 str | dup 68 % of lists >= 2; empty 2665 (86 %) |
| equipped | list 0.8/8 {index int 1..8, key str 340, name str maxlen 85} | index & key unique; sorted index asc; empty 1807 (58 %) |
| hidden_skills | list 2.8/7 str dist 315 | unique; empty 74 |
| horde_slots | null 2962 (95.5 %) / list 140: len 46..112 (mean 83) {slot_id 'force_slot_N' str, slot_index fint 0..11, key str 140, empty, available bool} | identity = composite (slot_index,key) unique 140/140 (~10 slot_index values x candidate buildings); sorted slot_index asc 100 % |
| merc_pools | dict 1.4/2 keys, 4 distinct (recruit_ror 87 % of rows, raise_dead, recruit_blessed, recruit_imperial) -> list 11.3/43 {key str 358, avail fint 0..21, cost fint 100..3700, can bool} | `can` absent on all 3,397 raise_dead rows; key unique per list; unsorted |
| move_tiles | list 13.6/16 {x int 11..1432, y int 9..960, sample_index int 0..15 asc, reach_rays list of exactly 8 int 0..55, reach_max int 3..55} | (x,y) unique; reach_rays and reach_max identical on every tile of a list (2911/2911) and reach_max == max(reach_rays) -> per-character, not per-tile; empty 181 |
| reach_chars / reach_setts | dict 10.0/43 numeric-str keys (1039 distinct) -> bool; dict 9.5/42 region keys (768 distinct) -> bool | reach_setts empty 5 |

### A.4 Hero entity (EB hero) -- 46 keys, 81 paths, 2,411 blobs
Same `_parse_lord` block; differences: hp null 2411/2411; units null 2411/2411; unit_cards `[]` 2411/2411; pending_recruit_keys `[]` 100 %; pending_recruits always 0; loyalty always 0; stance always 'none'; pending_queue (present 40.9 %) always `[]`; armory / force_effect_bundles always `[]` when present (478); is_agent, can_embed bool; agent_type str 7; ap_per_turn fint 2836..4723; rank 1..9; skill_points 0..7; region null 151 (6.3 %); x,y up to 65535; skills list 31.4/58 (key unique, tier asc 100 %, stable 100 %, empty 11); hidden_skills 3.5/5; equipped 0.2/5 (empty 79 %); effect_bundles 1.4/2 with turns_remaining always 1; trait_progress 0.6/9 (empty 59 %); traits 0.2/3; move_tiles 13.8/16 (same redundancy); reach_chars 9.8/43, reach_setts 9.0/44; authority/subterfuge/zeal always 3; resurrection_turns 0..4; xp 0..9980; xp_next_level 1000..11200; upkeep 0..300.

### A.5 Province entity (EB province) -- 28 keys, 64 paths, 3,667 blobs
All 28 keys present in 100 %; `settlement_present` is True in 3667/3667 (R1's 2-key fallback shape not observed). 33 blobs (0.9 %) have max_slots / gross_income / growth_per_turn / development_points / income = null, province = '', built = {}, slot_states = [] (all with settlement_present True).
Scalars: active_edict str 60 distinct, '' in 3035 (83 %); selected_edict str 65, 'none' 83 %; buildings fint 1..6; free_slots 0..10; max_slots 1..12; public_order fint -99..22; settlement_level 0..3; growth_per_turn -25..140; gross_income 0..1375; income 0..1263; development_points 0..2; province str 163; region str 306; can_set_edict / complete_owner / has_port / has_walls / is_capital bool.
| collection | shape | identity / order / empties |
|---|---|---|
| buildable | list 6.2/61 {slot_index int 0..5, key str 1040, level fint 0..3, cost fint 0..12800, upkeep fint 0..500 (constant within a list), active/empty/can_upgrade/can_afford_resources bool} | identity composite (slot_index,key): key dup 3.6 %, slot_index dup 40 %; sorted slot_index asc 100 %; order stable 98.5 %; empty 158 |
| slot_states | list 5.9/12 {index fint 0..11 unique sorted asc, key str|null (null 13967 = 65 % empty slots), health/max_health/refund null with key, repair_cost fint 0..1500, queued_key str null 95 %, damaged/can_repair/repairing/can_dismantle/queued/empty/ruined/upgrading/dismantling bool} | empty 33 |
| built | dict keys 2.1/6 (slot index strings '0'..'5') -> str (537 distinct) | empty 33 |
| building_now | dict 0.3/4 (6 distinct keys) -> {key str 352, turns_left fint 1..8, paused bool} | empty 2664 (73 %) |
| corruption | dict always exactly 7 fixed keys -> fint 0..100 | never empty |
| edicts | list 4.1/5 str (109) | unique; order stable 100 %; empty 147 |
| locked_slots | list 3.4/9 slot-index str | sorted asc 100 %; empty 49 |
| effect_bundles / plague_bundles | v31+ only (18.8 %): list {key, turns_remaining fint -13..9} empty 670/691; plague_bundles `[]` 691/691 | |

### A.6 Campaign entity (EB campaign) -- 33 keys, 70 paths, 2,070 blobs
The 24 CB keys are byte-identical to the same decision's CB (2070/2070); `read_failures` absent 2070/2070.
| key | shape | notes |
|---|---|---|
| tech | list 57.9/84 {key str 1778 unique, researched, can_research bool, cost fint 0..12500} | not sorted; order stable 100 %; empty 24 |
| rites | list 17.2/111 {index int 1..111 unique sorted asc, key str 783 unique, can_perform bool, invalid_reason str|null (null 19 %; 44 distinct; `{{tr:ritual_cannot_afford_resource_cost}}` 76 %, `Turns until available: N` incl. 9996..9999, `[[col:red]]...` markup)} | order stable 100 %; empty 409 (20 %) |
| lord_pools | dict 25.1/90 keys (subtype, 581 distinct) -> fixed 9-key dict {n int 0..6; 8 parallel lists of length == n: agents bool, bg_skills str|null (0.3 %), can bool, cqis str ALWAYS '0', ranks fint ALWAYS 0, subtypes str 273, traits list (non-empty 9/52383), units ALWAYS null} | n = 0 for 61 % of subtypes; `{}` in 137 (6.6 %) |
| anc_pool | list 2.4/18 {index int 1..18 unique sorted, key str 441 (dup 7 %), name str} | empty 376 |
| equipped_all | list 1.5/10 {index int 1..8 (dup 28 %: per-character index), key, name} | whole dup 2.3 %; empty 662 |
| missions | v36 only (present 11.6 %): list 9.2/63 {mission str 281 (dup 0.4 %), status 3 (active 77 %, succeeded, cancelled), issuer 5 (CLAN_ELDERS 98 %), turns_remaining fint -11..28, is_quest/is_victory/completed/cancelled/pending bool, category ALWAYS null} | |
| current_research / research_points | str 225 null 1207 (58 %) / fint 88..200 | |

### A.7 Interrupt campaign blob (ICB) -- 2,170 blobs, mean 410 B
19 keys: the `_parse_campaign` scalars + `_eval_ms` (fint 0..27, NOT popped on this path) -- no resources / effect_bundles / campaign_map / presave_radius / selector / hero_type_counts / read_failures. difficulty + leader absent in 47 (2.2 %). Ranges: turn 1..20, income 0..4625, treasury 0..35897, power_rank -39..-1. Interrupt kinds in sample: pre_battle 1000, battle_results 695, occupation 347, dilemma 65, diplomacy_proposal 33, war_declared 26, declare_war_cancel 2, event_ack 2. 0 ICB blob ids shared with decision CBs.

### A.8 Interrupt world blob (IWB) -- 2,169 blobs, mean 8,826 B
6 keys always: armies (4.6/13; rows have 17 keys: no ap_remaining / ap_per_turn, ap_pct raw), settlements 1.9/7, hostiles 8.3/60 (cap reached), enemy_agents 0.5/9, ruins 2.9/38, regions 14.9/75 (same 8-key rows). Panel blob (not requested; 1,786 sampled, mean 426 B): shape per kind -- pre_battle `{casualties,result}` or `{ally_cqi,enemy_cqi,n_ally_armies,n_enemy_armies,region?,casualties,result}`; battle_results `{armies,faction_names,outcome,resources,result_flag,rewards,rows,settlement_captured?,dismiss_visible?}`; occupation `{name,region}`.

## B. NULL vs MISSING vs EMPTY (paths with >= 2 states; everything else is always present and non-null)
| role | path | absent | null | empty |
|---|---|---|---|---|
| CB | difficulty / leader / selector | 62 / 64 / 58 (decision_id < 20k) | 0 | -- |
| CB | presave_radius | 0 | 2 | -- |
| CB | effect_bundles | 1642 (pre-v31) | 0 | 0 |
| CB | hero_type_counts / read_failures | 0 | 0 | 114 / 2070 |
| WB | armies[].hp,stance,units | 0 | 2469 (no army) | -- |
| WB | armies[].region,province,region_owner | 0 | 245 | -- |
| WB | armies[].ap_pct | 0 | 520 (ap_per_turn=0) | -- |
| WB | stationed{*} | 0 | 3158 | dict empty 112 |
| WB | citizenry / settlements / ruins / enemy_agents / war_graph / hostiles / diplo_unseen / regions[].adjacent | 0 | 0 | 121 / 112 / 611 / 1552 / 32 / 18 / 2063 / 1294 |
| lord | region | 0 | 85 | -- |
| lord | horde_slots | 0 | 2962 | 0 |
| lord | pending_queue | 1860 | 0 | 1013 |
| lord | v31 block (14 keys) | 2463 | background_skill 13 | armory 636, effect_bundles 606, trait_progress 78, traits 580, hidden_skill_states 13 |
| lord | equipped / recruitable / pending_recruit_keys / move_tiles / skills / hidden_skills / reach_setts | 0 | 0 | 1807 / 553 / 2665 / 181 / 74 / 74 / 5 |
| hero | hp / units | 0 | 2411 / 2411 | -- |
| hero | region | 0 | 151 | -- |
| hero | unit_cards / pending_recruit_keys / pending_queue(985 present) / armory(478) / force_effect_bundles(478) | 0 / 0 / 1426 / 1933 / 1933 | 0 | 2411 / 2411 / 985 / 478 / 478 |
| province | max_slots, gross_income, growth_per_turn, development_points, income | 0 | 33 | -- |
| province | province (str) / built / slot_states / building_now / edicts / buildable / locked_slots / active_edict | 0 | 0 | 33 / 33 / 33 / 2664 / 147 / 158 / 49 / 3035 |
| province | slot_states[].key,health,max_health,refund / queued_key | 0 | 13967 / 20436 | -- |
| campaign | current_research | 0 | 1207 | -- |
| campaign | missions | 1829 (pre-v36) | 0 | 0 |
| campaign | lord_pools / rites / anc_pool / equipped_all / tech | 0 | 0 | 137 / 409 / 376 / 662 / 24 |
| campaign | lord_pools{*}.units[] / bg_skills[] / missions[].category | 0 | 52383 / 135 / 2219 | -- |
| campaign | rites[].invalid_reason | 0 | 6869 | 0 |
No path shows all three states except via version drift (absent) combined with null/empty above (pending_queue: absent + empty; background_skill: absent + null). No empty strings other than active_edict, province (33) and rites invalid_reason ''.

## C. Version drift
- `collector_versions`: 32 rows (ids 2..36, `0.1.0-dev.loco4x+...` 2026-08-29 15:46 -> `0.3.3+gadfaf3e973d9` 2026-09-02 22:26); 26 are referenced by decisions. NULL `version_id` = `TW_CODE_VERSION` unset (R1 A.2, `store.py:229`): 131,716 / 206,907 decisions (63.7 %) = every decision_id 1..131,753 (ts 2026-08-18..08-29, before v2's started_ts) plus exactly one (182,874) between v34 and v35. Versions partition decision_id into contiguous ranges; 0 campaigns mix NULL and non-NULL, 0 campaigns have > 1 non-NULL version.
- Corpus share by version_id (decisions): None 131,716; v36 21,868; v34 13,078; v11 11,583; v16 3,958; v22 3,367; v31 2,974; v23 2,458; v7 2,427; v21 2,357; v35 2,203; v3 1,335; v10 1,177; v25 1,109; v19 1,060; v8 1,025; v9 881; v2 594; v29 492; v27 281; v26 273; v32 237; v30 205; v28 116; v24 67; v5 40; v6 26. Sample shares match (None 1318 = 63.7 %, v36 219, v34 131, v11 116, v16 39, v22 33, v31 30, ...).
  SQL: `select version_id, count(*), min(decision_id), max(decision_id), to_timestamp(min(ts))::date, to_timestamp(max(ts))::date, count(distinct campaign_id) from decisions group by 1 order by 1 nulls last`.
- Paths present only in some versions (per-role per-version presence, sample):
  - v31 (`0.1.48`, decision_id >= 166,585) onward, 0 % before: CB and EB(campaign) `effect_bundles`; lord/hero `xp, xp_next_level, subterfuge, zeal, authority, resurrection_turns, upkeep, background_skill, hidden_skill_states, effect_bundles, force_effect_bundles, armory, trait_progress, traits`; province `effect_bundles, plague_bundles`. Sample presence 20.6 % (lord) / 19.8 % (hero) / 18.8 % (province) / 20.7 % (CB).
  - v36 (`0.3.3`, >= 185,078) onward: EB(campaign) `missions` (11.6 %).
  - `pending_queue` (lord/hero): absent for NULL-version decisions < ~120k, 128/321 absent in 120k..140k, present 100 % after (lord sample: 0/229 present in 0..20k ... 193/321 in 120k..140k, 343/343 from 140k).
  - CB `difficulty / leader / selector`: absent only in decision_id < 20,000 (62 / 200 sampled rows in that range; 0 later).
  - Every other path has constant presence across versions; the hostiles per-key spread (cqi 21..63 % by version) is row-shape mix by `kind`, not drift.
- Min/max version per path: all non-drifting paths span None..v36; the v31 block spans v31..v36; missions v36 only.

## D. Entity identity
- `context_id` (full corpus): lord / hero = decimal cqi string (0 non-numeric; lord 1..1623, hero 3..1622; maxlen 4); province = region key (351 distinct: `wh3_dlc20_chaos_region_citadel_of_lead`..`wh3_main_combi_region_zoishenk`); campaign = faction key, equal to `campaigns.faction` in 2070/2070 sampled (104 distinct).
- cqi collision: lord 314,467 rows -> 345 distinct cqi but 8,551 distinct (campaign_id, cqi); hero 240,388 -> 350 distinct cqi, 6,912 distinct pairs. A lord cqi is reused by ~25 campaigns on average (start-of-campaign cqis are deterministic). SQL: `select e.context_kind, count(*), count(distinct e.context_id), count(distinct (d.campaign_id, e.context_id)), min(e.context_id::bigint), max(e.context_id::bigint) from entities e join decisions d using(decision_id) where e.context_kind in ('lord','hero') group by 1` (1.1 s).
- Persistence vs the immediately previous decision of the same campaign (sampled decisions; 42-44 per kind had no predecessor): lord 2985/3059 = 97.6 % present in the previous decision (74 new cqis); hero 2345/2370 = 98.9 %; province 3578/3623 = 98.8 %; campaign 2028/2028. Predecessor is in the same turn in 1794/2028 (88 %), turn+1 in 234.
  SQL: `with s as (select decision_id, campaign_id from decisions where decision_id % 100 = 7), p as (select s.decision_id sid, (select max(d2.decision_id) from decisions d2 where d2.campaign_id=s.campaign_id and d2.decision_id < s.decision_id) pid from s) select e.context_kind, count(*), count(*) filter (where p.pid is null), count(*) filter (where p.pid is not null and exists(select 1 from entities e2 where e2.decision_id=p.pid and e2.context_kind=e.context_kind and e2.context_id=e.context_id)) from p join entities e on e.decision_id=p.sid group by 1` (0.2 s).
- Entities per decision (full corpus; kind, decisions having the kind, mean / p95 / max): campaign 206,907, 1.00 / 1 / 1; lord 199,160 (96.3 %), 1.58 / 3 / 7; hero 192,516 (93.0 %), 1.25 / 2 / 6; province 191,904 (92.7 %), 1.92 / 4 / 8. 0 duplicate (decision_id, context_kind, context_id) rows. Sample: 5.43 entity blobs per decision.
- Blob dedup: within the 1 % decision sample CB 2070/2070 distinct, WB 2051/2070, lord 3100/3102, hero 2411/2411, province 3496/3667 (4.7 %). Globally 1,666,333 references (206,907x2 + 1,130,208 + 122,311) -> 951,161 distinct blobs: 42.9 % of references reuse a blob, almost all from consecutive-decision identical states (whole_blob_same: province 80 %, CB 59 %, EB campaign 52 %, WB 45 %, lord 3 %, hero 2 %).

## E. Sizes and throughput
| role | sample n | mean chars | p50 | p95 | max | corpus rows | logical bytes (mean x rows) |
|---|---|---|---|---|---|---|---|
| CB | 2,070 | 784 | 711 | 1,183 | 2,343 | 206,907 | 0.162 GB |
| WB | 2,070 | 11,560 | 10,736 | 20,510 | 40,405 | 206,907 | 2.392 GB |
| EB lord | 3,102 | 12,324 | 11,919 | 16,775 | 25,701 | 314,467 | 3.876 GB |
| EB hero | 2,411 | 6,396 | 6,402 | 7,780 | 9,866 | 240,388 | 1.538 GB |
| EB province | 3,667 | 3,735 | 3,563 | 6,745 | 13,810 | 368,446 | 1.376 GB |
| EB campaign | 2,070 | 14,807 | 12,903 | 29,586 | 40,736 | 206,907 | 3.064 GB |
| ICB | 2,170 | 410 | 405 | 441 | 445 | 43,392 | 0.018 GB |
| IWB | 2,169 | 8,826 | 8,140 | 16,300 | 41,527 | 43,386 | 0.383 GB |
| IPB | 1,786 | 426 | 165 | 1,200 | 1,644 | 35,533 | 0.015 GB |
- Total logical JSON ~12.82 GB (extrapolation factor = corpus row count / sample rows: 99.95x for decisions, 101.4x lord, 99.7x hero, 100.5x province, 20.0x interrupts). Physical distinct text `sum(blobs.n)` = 8.472 GB (66 % of logical: dedup saves 34 %); on disk 2.504 GB -> TOAST compresses the text ~3.4x (R1's "no compression" holds only at the application layer). `n == len(z)` on 15,459/15,459 sampled rows; UTF-8 bytes / chars = 1.0000; no exponent-notation floats in any sampled WB.
- Byte share by top-level key (mean bytes of compact JSON): lord -- skills 48 %, merc_pools 11 %, move_tiles 10 %, stances 8 %, unit_cards 6 %, horde_slots 4 %, reach_setts 3 %, recruitable 3 %; hero -- skills 59 %, move_tiles 19 %, reach_setts 6 %, hidden_skills 3 %; province -- slot_states 44 %, buildable 28 %, corruption 7 %, edicts 5 %; EB campaign -- lord_pools 40 %, tech 34 %, rites 17 %; WB -- regions 41 %, relations 18 %, armies 18 %, hostiles 14 %; CB -- resources 26 %; IWB -- regions 55 %, armies 21 %, hostiles 19 %.
- Throughput (127.0.0.1:55432, warm cache, psycopg3 text protocol, `execute().fetchall()`): WB 27.7k rows/s = 320 MB/s; EB 22.2k rows/s = 194 MB/s; IWB 11.8k rows/s = 104 MB/s; CB 42k rows/s (33 MB/s, tiny rows). `json.loads`: WB 21.7k blobs/s = 251 MB/s; EB 13.7k blobs/s = 119 MB/s; CB 212k blobs/s = 167 MB/s. Bound for a full pass: 951k distinct blobs / 8.5 GB -> fetch ~45-80 s, parse ~40-70 s; 1.67M references / 12.8 GB -> ~75 s fetch + ~100 s parse, single-threaded, cache-warm (cold reads of the 2.5 GB table add disk time). The SQL-side variant (`b.z::jsonb` + md5 per blob, `change_rate.py:121-124`) ran at ~2.5 MB/s (410 s for ~1.05 GB); the same logic client-side ran at 11 s for 0.57 GB.

## F. Numeric precision
- Fractional values occur only in: WB `armies[].ap_pct` (0..1; 1458/9139 non-null fractional; null when ap_per_turn = 0), `armies[].hp` and `hostiles[].hp` (0.01..20, sum of unit strength %, 2 decimals, e.g. 11.39, 18.03), EB lord/hero `ap_pct` (0..1, 25 %/26 % fractional, e.g. 0.395508), lord `hp` (53 % fractional), `unit_cards[].strength_pct` (0..100, 38 % fractional, e.g. 67.5). Every other numeric path is integral in all 17,629 sampled blobs.
- Integral floats that are counts / levels / ranks / indices (candidate INTEGER; `float(x)` typing from `_num`, printed as `12.0`): rank, skill_points, units, pending_recruits, ap_remaining, ap_per_turn, loyalty, x, y, xp, xp_next_level, subterfuge, zeal, authority, resurrection_turns, turn, settlements, armies, lord_level, allies, vassals, power_rank, difficulty, presave_radius; skills level/tier/total_levels; hidden_skill_states level/total_levels; traits level/points/threshold_points; trait_progress values; horde_slots slot_index; unit_cards xp; effect_bundles / force_effect_bundles / plague_bundles turns_remaining; pending_queue turns_left; merc_pools avail; province max_slots, free_slots, buildings, settlement_level, development_points, public_order, corruption values, buildable level/slot_index, slot_states index/health/max_health, building_now turns_left; research_points; missions turns_remaining; lord_pools n (already int) / ranks; regions / ruins / enemy_agents x,y; relations standing; rites index; anc_pool / equipped index.
- Integral measures (money / rates; integer in the game): income, treasury, gross_income, growth_per_turn, province income, resources values (-1..5947), upkeep, recruitable cost, merc cost, buildable cost/upkeep, slot_states refund/repair_cost, tech cost.
- Cross-role type inconsistencies for the same quantity: WB `armies[].x,y,rank,units,cqi` are JSON ints (Lua `%.14g`) while EB lord/hero `x,y,rank,units` are floats and `cqi` is a string; `enemy_agents[].cqi` is a string but `armies[].cqi` / `hostiles[].cqi` are ints; slot indices are strings as dict keys (`built`, `building_now`, `locked_slots[]`) but numbers in `slot_states[].index` / `buildable[].slot_index`; `x=y=65535` is an off-map sentinel (35 WB army rows, hero x/y max 65535).

## G. change_rate.py audit and reproduction
Audit (`change_rate.py`):
1. `scd_rows` adds the whole collection length whenever its hash changes (`:83`) -- it is a whole-collection re-insert count, an upper bound on per-element SCD (a skills change is 1-2 of ~50 rows, counted as 50).
2. `dedup_rows` = elements of globally distinct collection values (`:67-68`); distinct counts grow sub-linearly with sample size, so the x12.09 projection (`:144-150`) overstates corpus dedup rows and ratios are only comparable between equal-size samples (visible below: the half-size rerun has 5-35 % higher dedup ratios on high-cardinality collections while same% agrees within 1.5 points).
3. Pairs are successive appearances of (campaign, context_id) (`:70`), not consecutive decisions -- an entity absent for k decisions still forms a pair (1-2 % of lord/hero rows per D).
4. Dict collections count keys as rows (`:45-46`): lord_pools counts subtypes (24.4), not the n-length parallel lists (25.3 entries x 8 attributes per snapshot); built / corruption / resources likewise.
5. Sample excludes campaigns with < 8 decisions (`:20`) but projects by all decisions (`:144`); short campaigns carry more first-appearance rows.
6. `move_tiles` same% (4.8 %) measures the random tile sampler (16 random reachable tiles), not state.
7. 410 s (192 s lord) is spent on `b.z::jsonb` + md5 in SQL (`:121-124`); the identical logic client-side (hash `json.dumps(sort_keys, compact)`, same pair/turn/dedup definitions) ran in 11 s on 55 % of the volume.
Otherwise it measures what it claims: same% / inturn% / xturn% / nonempty% / whole_blob_same (blob_id equality = sha dedup) are correctly defined; jsonb text canonicalises keys and numbers equivalently to `sort_keys` JSON.

Rerun core:
```
camps = campaign_id % 24 = 5 having count(*) >= 8 (196 camps, 9,468 decisions)
rows = (campaign_id, context_id|'0', decision_id, turn, blob_id, json.loads(z)) per kind, sorted; H(v)=md5(json.dumps(v,sort_keys=True,separators=(',',':')))
per collection: plain += len; distinct hashes -> dedup rows; pair if prev (campaign,ctx) equal: same/inturn/xturn, changed += len when hash differs
order_kept: for list-of-dict with id field (skills/stances/recruitable/tech/rites key, anc_pool/slot_states index, regions/settlements/ruins region, relations/war_graph faction, armies cqi, buildable key) when ids unique in both and >= 2 common: [ids_prev filtered to common] == [ids_now filtered to common]
```
15 largest collections (by original plain rows), original (`%12=0`, 17,113 dec, 410 s) vs rerun (`%24=5`, 9,468 dec, 11 s):
| collection | mean_len orig/new | same% orig/new | inturn% | xturn% | dedup ratio orig/new | order kept (pairs) |
|---|---|---|---|---|---|---|
| lord.skills | 50.7 / 51.1 | 97.0 / 97.0 | 97.0 / 97.0 | 96.7 / 97.2 | 3.0 % / 3.5 % | 100 % (14,259) |
| campaign.tech | 56.6 / 58.0 | 93.3 / 93.2 | 95.6 / 95.5 | 75.2 / 74.9 | 3.8 % / 5.1 % | 100 % (9,094) |
| hero.skills | 31.8 / 31.2 | 98.8 / 99.0 | 98.8 / 99.0 | 98.3 / 99.0 | 2.1 % / 2.2 % | 100 % (10,363) |
| campaign.lord_pools | 24.4 / 24.5 | 95.5 / 96.1 | 97.6 / 97.8 | 78.8 / 82.2 | 4.6 % / 4.9 % | dict |
| lord.move_tiles | 13.6 / 13.7 | 4.8 / 3.5 | 5.3 / 3.9 | 0.5 / 0.5 | 100 % / 100 % | random |
| campaign.rites | 16.7 / 19.2 | 95.4 / 95.0 | 97.2 / 97.1 | 80.8 / 78.4 | 8.0 % / 7.9 % | 100 % (6,324) |
| hero.move_tiles | 13.5 / 13.7 | 2.8 / 2.9 | 3.0 / 3.3 | 0.6 / 0.4 | 100 % / 100 % | random |
| lord.reach_chars | 10.1 / 10.1 | 72.1 / 73.1 | 78.8 / 79.9 | 13.3 / 14.2 | 23.2 % / 22.7 % | dict |
| world.regions | 14.7 / 15.4 | 92.6 / 92.4 | 96.7 / 96.7 | 60.6 / 58.7 | 6.3 % / 7.4 % | 100 % (9,124) |
| lord.reach_setts | 9.7 / 10.2 | 80.8 / 81.2 | 86.6 / 87.1 | 29.5 / 30.5 | 10.2 % / 10.8 % | dict |
| lord.stances | 8.8 / 8.7 | 78.6 / 80.1 | 81.7 / 82.3 | 52.0 / 60.9 | 3.3 % / 4.1 % | 100 % (14,362) |
| province.corruption | 7.0 / 7.0 | 94.8 / 94.6 | 99.0 / 99.1 | 56.8 / 55.0 | 2.1 % / 2.5 % | dict (fixed 7 keys) |
| hero.reach_chars | 9.8 / 9.7 | 74.0 / 74.3 | 80.6 / 81.6 | 18.5 / 14.0 | 21.7 % / 21.4 % | dict |
| province.buildable | 5.9 / 5.7 | 92.3 / 92.0 | 95.1 / 94.9 | 68.0 / 66.8 | 7.5 % / 9.0 % | 98.5 % (13,553) |
| province.slot_states | 5.9 / 6.0 | 94.8 / 94.6 | 96.7 / 96.5 | 78.4 / 78.3 | 4.8 % / 5.8 % | 100 % (17,023) |
Also reproduced: world.relations 84.4 / 84.7 (order 100 %), armies 47.1 / 47.9 (100 %), hostiles 73.6 / 73.9, unit_cards 86.5 / 87.8 (100 %), recruitable 98.1 / 97.9 (100 %), settlements 89.7 / 90.0, war_graph 93.9 / 93.8, ruins 96.6 / 96.5, edicts 100 / 100, locked_slots 99.8 / 99.9, resources 91.7 / 91.5, anc_pool 96.0 / 96.2; whole_blob_same lord 3.5 / 2.5, hero 2.2 / 2.4, province 80.6 / 79.9, campaign 52.5 / 52.3, world 44.9 / 45.8; scalar remainder same lord 63.8 / 61.2, hero 79.5 / 79.3, province 83.4 / 81.6, campaign 60.1 / 53.9, world 85.2 / 84.2. All same% within 1.5 points (stances xturn 52 vs 61 is the largest gap); dedup ratios within 15 % except tech (+33 %), buildable (+20 %), slot_states (+21 %), regions (+18 %), corruption (+21 %) -- the sample-size effect of flaw 2. Order semantics: every list-of-dict collection with a unique identity keeps relative order between consecutive snapshots (100 %; buildable 98.5 %); dict key order carries no information (`sort_keys=True`); `skills` is additionally sorted by `tier`, `slot_states`/`rites`/`anc_pool`/`equipped`/`horde_slots` by `index`/`slot_index`, `citizenry`/`locked_slots` lexically, `armies` by `cqi` (98 %).