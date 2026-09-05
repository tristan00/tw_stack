# Lane M3 -- how well the corpus's game identifiers resolve against reference data

Read-only measurement, 2026-09-04, against live `tw_stack` (host 127.0.0.1:55432, user tw, `default_transaction_read_only = on`). All DB work: ~15 s wall clock total; heaviest single query `SELECT event, count(*) FROM app.game_event GROUP BY 1` (0.1 s, index-only).

## 0. Sources and sample sizes

| Source | Rows in DB | Sample used | How |
|---|---|---|---|
| `public.decisions` (+ `campaign_blob`, `world_blob`) | 206,907 (ids 1..206,945), 4,749 campaigns | 2,070 decisions, 2,013 campaigns | `decision_id % 100 = 7` |
| `public.entities` (+ `features_blob`) | 1,130,208 | 11,250 entities (lord 3,102 / hero 2,411 / province 3,667 / campaign 2,070) | same modulo |
| `public.actions` | 5,108,337 (4,888,404 are `move`) | 268,941 rows: every non-`move` row (220,035) + `move` where `action_id % 100 = 0` (48,906) | |
| `public.interrupts` | 43,392; kinds: pre_battle 20,236, battle_results 13,552, occupation 6,755, dilemma 1,456, diplomacy_proposal 714, war_declared 579, event_ack 74, declare_war_cancel 21, ally_attacked 4, diplomacy_notice 1 | all rows (`options_json`, root, chosen); `panel_blob` for the first 400 rows of each kind | |
| `public.diplomacy_events` | 74,145 (all `kind='diplomacy'`) | 18,535 | `event_id % 4 = 0` |
| `app.game_event` | 5,003,759, 173 event kinds, 478 campaign_keys, ts 1788321492..1788493441 | 49,862 rows by `TABLESAMPLE SYSTEM (1.0)` + 138,473 rows stratified (newest 1,500 per event kind) | |
| `reference.*` tables | see B | full | |
| `db.pack` | 1,521 `db/<table>/` files, one per table; all 53 tables in `advisor/reference/schema_db.json` decode `CLEAN-EOF` (0.5 s) | full | `build_reference.parse_pack/decode_db_table` |
| `local_en.pack` | 241,972 loc entries; `reference.loc` has 241,972 rows and 200/200 sampled keys have identical text (it is built from this pack: `advisor/reference/build_reference.py:554-567`) | full | `build_reference.decode_loc` |

Sample byte volume (uncompressed JSON, `blobs.n`): campaign blobs 1,623,032 B, world blobs 23,930,099 B, entity blobs 97,999,729 B, total 123,552,860 B for the 2,070 decisions.

`campaigns.campaign_map` over all 4,827 campaigns: `wh3_main_combi` 3,982, `wh3_main_chaos` 767, NULL 78. `campaign_key` format: 3,974 keys match `faction_<11hex>_<11hex>`, 9 match `faction_<16hex>` (the format written by `decisions/collect.py:116-119`), 0 use the `faction@run_id` fallback of `decisions/store.py:145-146`.

## 1. Where identifiers are produced (code map)

Decision blobs are `json.dumps(sort_keys=True)` of the collector's dicts (`decisions/store.py:58-59`, `:222-239`). Producers, all in `decisions/collect.py` unless noted:

- campaign blob (`campaign`): `faction` = `f:name()` (:172,258), `faction_cqi` = `f:command_queue_index()` as text (:184,259), `campaign_uuid` (:116-119,260), `campaign_map` (:128-136, values `wh3_main_combi`/`wh3_main_chaos`), `leader` = `common.get_localised_string(forename..surname)` display text (:205-212,272), `resources{key}` = pooled resource keys (:76-107), `effect_bundles[].key` (:971-976), `hero_type_counts{key}` = `character_type_key()` values (:1648-1656), `game_version`, `selector`.
- campaign entity state (`context_kind='campaign'`, `context_id` = faction key, :1855): the campaign dict plus `anc_pool[]`/`equipped_all[]` `{index,name,key}` where `name` = CcoAncillary `Name` (display text) and `key` = `AncillaryRecordContext.Key` (:1156-1205), `lord_pools{subtype}` with `bg_skills[]`, `cqis[]`, `subtypes[]`, `units[]`, `traits[]` (:1329-1403; `traits` entries are `trait~level` composites, :1343), `missions[].mission/issuer/category` (:979-1011), `tech[].key` = CCO `NodeKey` (:1406-1414,1622-1645), `current_research` (NodeKey), `rites[].key/invalid_reason` (:1415-1418,1635-1642).
- world blob (`world`): `regions[]` `region/province/owner/adjacent[]` from `region:name()/province_name()/owning_faction():name()` (:276-321), `armies[]` from bus channel `chars` (`bus/mod/twcontrol.lua:862-915`: `cqi` numeric, `subtype`, `agent_type`, `region`, `province`, `region_owner`, `stance`), `settlements[]` (`twcontrol.lua:918-941`), `hostiles[]` (`twcontrol.lua:944-1046`: `kind`, `faction`, `cqi` numeric, `subtype`, `agent_type`, `region`, `province`, `stance`), `enemy_agents[]` (`cqi` as text, `faction`; :359-393), `war_graph[]` (:333-356), `relations[]` (`faction` = `f:name()`; :1433-1489), `stationed{region}` = cqi text (:861-887), `citizenry[]` = cqi text, `ruins[]`. `region_owner` is overwritten with the literal `ruins` for abandoned regions (:406-413).
- lord/hero entity state (`context_id` = character cqi as text, :1843-1847): `cqi`, `region`, `subtype` = `character_subtype_key()` (:604,636), `stance` and `stances[].key` = CCO `StanceList[].Key` (:599-600,838-842), `skills[].key` = CCO `SkillList[].Key` (:843-845), `hidden_skills[]`, `hidden_skill_states[].key`, `traits[].key` = `TraitKey`, `traits[].level_key` = `Key` (:852-856,1534-1545), `trait_progress{trait}` from bus `trait_progress` (`twcontrol.lua:838-859`), `recruitable[].key` / `unit_cards[].key` / `pending_queue[].key` / `pending_recruit_keys[]` = unit keys (:460-485,585-646), `merc_pools{action_type}[].key` = unit keys (:488-582), `horde_slots[].key` = building level keys, `slot_id` = `force_slot_N` (:893-924), `equipped[]` `{index,name,key}` (:1162-1166), `effect_bundles[].key`, `force_effect_bundles[].key`, `armory[]` = `ArmoryItemVariantRecordContext.Key` (:949-968,1571-1591), `background_skill`, `reach_chars{cqi}`, `reach_setts{region}` (:816-835), `agent_type` = `character_type_key()` (hero only, :932-934,1599).
- province entity state (`context_id` = region key, :1849): `province` = CCO `ProvinceContext.Key`, `selected_edict`/`active_edict` = initiative keys (:677-680), `built{slot_index}`/`building_now{slot}.key`/`slot_states[].key/queued_key`/`buildable[].key` = building level keys (:682-688,1213-1276,1604-1619), `edicts[]` = `InitiativeList[].Key` (:1230-1232), `corruption{pooled_resource}` (:689-701), `effect_bundles[].key`, `plague_bundles[].key`.
- `actions` rows (`context_kind, context_id, action_type, action_key, params`; `decisions/store.py:129-143`) are the advisor's offers from `advisor/options.py`: `action_key` composites `cqi:N` (:159), `settlement:<region>` (:184), `<region>@<slot>` (:494), `<subtype>@<idx>` (:578), `<unit>@<idx>` (:148), `<action>@cqi:N` / `<action>@<region>` (:451), `force_slot_N@<building>` (:101), `<faction>:<term>` (:652-682), `xy:x,y` (:72); `params` keys per action type are listed in section A.
- `interrupts` rows come from `launcher/interrupts.py:_record_choice` (:1574-1589); dilemma option keys are the UI record ids `CcoCdirEventsDilemmaChoiceDetailRecord<dilemma_key><OPTION>` and `dilemma_id`/`option_id` are split by `os.path.commonprefix` over the option ids (:1738-1745); occupation option keys are lower-cased button texts (:401).
- `diplomacy_events.payload` is written by `decisions/store.py:444-455`; `faction_keys[]` are regex-extracted from UI context strings by `launcher/diplo_stream.py:14,68-75` with `[a-z0-9_]+` (no hyphen).
- `app.game_event.payload` (`logs/events_stream.py:18-27,84-96,145-147`) persists only `TWSTATE` lines with `kind == "event"` (`:84-86`); the payload keys are those of `ctx()` in `bus/mod/twstate.lua:876-997` (`faction`, `char_cqi`, `char_subtype`, `mf_cqi`, `target_char`, `region`, `garrison`, `building`, `unit`, `unit_faction`, `unit_force_faction`, `tech`, `skill`, `ancillary`, `item_variant`, `ritual`, `ritual_rkey`, `ritual_chain_key`, `mission_key`, `initiative_key`, `dilemma`, `choice_key`, `captive_record_key`, `pr_resource_key`, `agent_action_key`, `occupation_decision`, `streak_effect`, `teleport_node`, ...). The per-turn scrape records of `twstate.lua` (`kind` char/force/unit/region/slot/faction/diplo/active_ritual/faction_ancillary/... at `:65-555`) are NOT persisted anywhere in the DB: `events_stream.py:86` drops every non-`event` kind, `ui-capture/actions_stream.py:53-76` reads only `kind=="faction"` for the current turn, and `analytics/gamestate.py` derives its `game_turn_*` tables from decision blobs (`:153-167`).

## A. Extraction: identifier-like string groups (sample)

Notation: `campaign.` = decisions.campaign_blob, `world.` = decisions.world_blob, `lord./hero./province./campaign_entity.` = entities.features_blob by context_kind, `{key}` = a dict key, `[]` = list element, `actions.<type>.params.<k>`, `interrupts.<kind>.`, `diplomacy_events.payload.`, `ev.<Event>.<k>` = app.game_event payload. "occ" = occurrences in the sample, "dist" = distinct values.

### A.1 Decision blobs (2,070 decisions, 11,250 entities): 131 string paths found. Identifier-bearing ones:

| path | occ | dist | example |
|---|---|---|---|
| campaign.faction / campaign_entity.faction / campaign_entity.context_id | 2,070 each | 103 | wh3_main_cth_the_northern_provinces |
| campaign.faction_cqi | 2,070 | 103 | '23' |
| campaign.campaign_uuid | 2,070 | 2,013 | wh3_dlc27_nor_sayl_1a0028f1782_1a0185ddb50 |
| campaign.campaign_map | 2,070 | 2 | wh3_main_combi |
| campaign.leader (display text) | 2,006 | 103 | 'Miao Ying, the Storm Dragon' |
| campaign.resources{key} | 11,224 | 212 | wh3_dlc27_nor_confidence |
| campaign.effect_bundles[].key | 1,524 | 227 | wh3_main_faction_trait_cathay |
| campaign.hero_type_counts{key} | 2,324 | 7 | champion |
| campaign_entity.tech[].key | 119,762 | 1,778 | wh3_main_tech_cth_2 |
| campaign_entity.current_research | 863 | 225 | wh3_main_tech_cth_28 |
| campaign_entity.rites[].key | 35,681 | 783 | wh3_dlc20_ritual_chs_eye_of_the_gods |
| campaign_entity.rites[].invalid_reason (loc text/markup) | 28,812 | 44 | '{{tr:ritual_cannot_afford_resource_cost}}', '[[col:red]]You have insufficient funds[[/col]]' |
| campaign_entity.anc_pool[].key / .name | 4,987 / 4,987 | 441 / 425 | wh_main_anc_enchanted_item_potion_of_strength / 'Potion of Strength' |
| campaign_entity.equipped_all[].key / .name | 3,161 / 3,161 | 381 / 369 | |
| campaign_entity.lord_pools{key} (subtype) | 51,908 | 581 | wh3_cp1_cth_bhashiva |
| campaign_entity.lord_pools{}.subtypes[] | 52,383 | 273 | wh3_main_cth_alchemist |
| campaign_entity.lord_pools{}.bg_skills[] | 52,248 | 540 | wh2_main_skill_innate_all_fleet_footed |
| campaign_entity.lord_pools{}.cqis[] | 52,383 | 1 | '0' (every candidate; `CharacterContext.CQI` reads 0, collect.py:1347) |
| campaign_entity.lord_pools{}.traits[][] | 9 | 2 | wh2_main_trait_brutally_honest, wh2_main_trait_brutally_honest~1 |
| campaign_entity.missions[].mission / .issuer / .status | 2,219 | 281 / 5 / 3 | wh_main_long_victory / CLAN_ELDERS / active |
| world.regions[].region / .province / .owner / .adjacent[] | 30,303 / 30,303 / 30,303 / 100,888 | 769 / 294 / 296 / 750 | owner includes `rebels` (5,259 = 17.4%) |
| world.armies[].subtype / .agent_type / .region / .province / .region_owner / .stance | 9,659 / 9,659 / 9,414 / 9,414 / 9,414 / 7,190 | 287 / 8 / 439 / 206 / 237 / 16 | region_owner includes `ruins` 288, `rebels` 9 |
| world.armies[].cqi (JSON number) | 9,659 | 491 | 1..1550 |
| world.hostiles[].kind / .faction / .region / .province / .subtype / .agent_type / .stance | 16,157 / 16,157 / 8,037 / 8,025 / 903 / 903 / 7,217 | 5 / 305 / 685 / 242 / 88 / 7 / 13 | |
| world.hostiles[].cqi (number) | 8,120 | 918 | 1..1563 |
| world.enemy_agents[].cqi (text) / .faction | 903 / 903 | 185 / 132 | |
| world.relations[].faction | 19,522 | 330 | |
| world.war_graph[].faction / .at_war_with[] | 8,669 / 11,380 | 312 / 283 | |
| world.settlements[].region, world.stationed{key} | 3,978 each | 318 | |
| world.stationed{} (cqi text), world.citizenry[] (cqi text) | 820 / 3,751 | 172 / 275 | |
| world.ruins[].region | 6,213 | 429 | |
| lord.context_id = lord.cqi / hero.context_id = hero.cqi | 3,102 / 2,411 | 274 / 215 | '42' |
| lord.subtype / hero.subtype | 3,102 / 2,411 | 175 / 120 | |
| hero.agent_type | 2,411 | 7 | |
| lord.region / hero.region | 3,017 / 2,260 | 382 / 346 | |
| lord.stance, lord.stances[].key | 3,102 / 27,027 | 16 / 18 | MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT |
| lord.skills[].key / hero.skills[].key | 155,608 / 75,762 | 2,383 / 1,582 | |
| lord.hidden_skills[] / hero.hidden_skills[] | 8,533 / 8,462 | 315 / 244 | |
| lord.hidden_skill_states[].key / hero. | 1,728 / 1,633 | 254 / 195 | |
| lord.background_skill / hero.background_skill | 626 / 426 | 176 / 93 | |
| lord.traits[].key / .level_key; hero.traits[].key / .level_key | 70 / 70; 114 / 114 | 20 / 22; 21 / 21 | |
| lord.trait_progress{key} / hero.trait_progress{key} | 2,661 / 265 | 96 / 45 | |
| lord.unit_cards[].key / .category | 20,833 | 738 / 6 | |
| lord.recruitable[].key, lord.pending_queue[].key, lord.pending_recruit_keys[] | 10,195 / 357 / 711 | 232 / 91 / 124 | |
| lord.merc_pools{key} / lord.merc_pools{}[].key | 4,348 / 48,970 | 4 / 358 | raise_dead / unit keys |
| lord.horde_slots[].key / .slot_id | 11,617 | 140 / 139 | wh2_dlc11_vampirecoast_ship_hold_1 / force_slot_15 |
| lord.equipped[].key / .name; hero.equipped[].key / .name | 2,374; 601 | 340 / 334; 114 / 110 | |
| lord.effect_bundles[].key / lord.force_effect_bundles[].key / hero.effect_bundles[].key / province.effect_bundles[].key | 34 / 981 / 656 / 33 | 11 / 73 / 7 / 6 | |
| lord.armory[] | 24 | 8 | wh3_main_dae_cha_daemon_prince_head_base_01 |
| lord.reach_chars{key} / hero.reach_chars{key} (cqi text) | 30,990 / 23,558 | 1,039 / 1,040 | |
| lord.reach_setts{key} / hero.reach_setts{key} | 29,393 / 21,812 | 768 / 759 | |
| province.context_id = province.region | 3,667 | 306 | |
| province.province | 3,667 | 163 | 33 are '' |
| province.edicts[] / .active_edict / .selected_edict | 15,156 / 3,667 / 3,667 | 109 / 60 / 65 | active_edict is '' in 3,035 rows; selected_edict is 'none' when unset |
| province.built{key} (slot idx) / province.built{} | 7,647 | 6 / 537 | |
| province.buildable[].key | 22,758 | 1,040 | |
| province.building_now{}.key = province.slot_states[].queued_key | 1,178 | 352 | |
| province.slot_states[].key | 7,647 | 537 | |
| province.corruption{key} | 25,669 | 7 | wh3_main_corruption_chaos |
| province.locked_slots[] | 12,338 | 11 | slot indices |

Not present in the sample as strings: `campaign.read_failures{key}` (no failures recorded in sampled blobs), `campaign_entity.missions[].category` (null), `campaign_entity.lord_pools{}.units[]` (null).

### A.2 `actions` (268,941 rows). `action_key` and `params` keys carrying identifiers, per `action_type`:

| action_type (rows in sample) | action_key form (dist) | identifier params (dist) |
|---|---|---|
| move (48,906 of 4,888,404) | `xy:x,y` (38,204) | x, y, sample_index, reach_rays, reach_max -- coordinates only |
| diplomacy (99,207) | `<faction>:<term>` (3,799) | faction (330), terms[] (9), gift (3) |
| recruit_lord (14,468) / recruit_hero (13,400) | `<subtype>@<idx>` (287 / 413) | cand_subtype (122 / 152), bg_skill (367 / 359), region (337 / 346), agent_type (3 / 6), cqi (always '0'), trait/traits[] (2, only 6 rows) |
| items (12,868) / item_unequip (5,457) | ancillary key (504 / 554) | item_key (= action_key), item_name (487 / 531 display strings), pool_index / equipped_index |
| leave_garrison (12,676) | `leave_garrison` | x, y |
| skills (10,397) | skill key (826) | level, total_levels, tier |
| hero_action (9,975) | `<action>@cqi:N` (736) | action (5), action_key (18 agent_actions keys), ability (1: assist_army), agent_type (6), attribute (3), target_cqi (numeric 1..1539), target_kind (1: character) |
| building (7,456) | building level key (727) | slot_index, cost, level ... |
| recruit_unit (6,329) / cancel_recruit (2,209) | unit key (260) / `<unit>@<idx>` (490) | unit (260 / 180) |
| attack_army (6,157) | `cqi:N` (758) | target_cqi (numeric 6..1603), target_faction (231) |
| raise_dead (5,087) / recruit_blessed (387) / recruit_ror (120) / recruit_imperial (75) | unit key (55 / 21 / 15 / 22) | unit |
| research (2,796) | tech NodeKey (672) | -- |
| garrison (2,709) | `settlement:<region>` (335) | x, y |
| stance (2,690) | `MILITARY_FORCE_ACTIVE_STANCE_TYPE_*` (17) | active |
| building_repair (1,474) / building_dismantle (852) | `<region>@<slot>` (270 / 305) | building_key (141 / 304), queued_key (14 / 92), region (150 / 200) |
| attack_settlement (1,132) / colonize (442) | region key (330 / 196) | target_faction (178) / -- |
| rites (638) | ritual key (278) | ritual_key (= action_key), rite_index |
| edict (579) | initiative key (96) | province (69), region (69) |
| horde_building (350) | `force_slot_N@<building>` (338) | building_key (78), slot_id (65) |
| end_turn (105) | `end_turn` | -- |

`actions.context_id`: lord 346 distinct cqi strings (81,687 rows), hero 346 (46,279), province 351 region keys (38,229), campaign 105 (102,746) of which one row has `context_id='campaign'` (action_id 37129, `end_turn`).

### A.3 `interrupts` (43,392 rows)

| kind | options{key} (dist) | other identifier fields |
|---|---|---|
| pre_battle | button_autoresolve / button_retreat / button_surround / (1 more) (4) | root `popup_pre_battle` |
| battle_results | button_accept, button_dismiss, button_captive_option_{release,kill,enslave,enslave_replenishment_only,enslave_slaves_only} (7) | root `popup_battle_results` |
| occupation | lower-cased button labels: sack 5,304, raze 4,878, occupy 4,438, 'loot & occupy' 3,640, 'do nothing' 1,204, colonise 523, 'occupy and vassalise' 405, 'skulls for the skull throne' 372, ... (38) | root `settlement_captured` |
| dilemma | `CcoCdirEventsDilemmaChoiceDetailRecord<dilemma_key><OPTION>` (358); `root_context` = prefix + dilemma key (146); `options{}.option_id` FIRST/SECOND/THIRD/FOURTH/FIFTH or digits (19); `options{}.subtree[].context` has 16 `CcoMainUnitRecord:<unit>` values | `options{}.text` = option label (316 display strings), `payload[]` = effect texts |
| diplomacy_proposal | button_accept / button_cancel; `answer` accept/decline | root `diplomacy_dropdown` |
| war_declared | button_ok_war_declared | |
| event_ack | button_accept / text_button | |
| declare_war_cancel | button_cancel_declare | root `move_options` |
| ally_attacked | button_join_defender / button_join_aggressor / decline_button | |
| diplomacy_notice | button_accept | |

Panel blobs hold no game keys except `diplomacy_events.payload.panel.faction`; battle/pre-battle/proposal panels are display text and numbers (`armies.allies[]` 'Tamurkhan the Maggot Lord', `faction_names.enemy` 'Crimson Skull', `outcome` 'Pyrrhic Victory', `race` 'Grand Cathay', `terms[]` 'Trade Agreement', `attitude_label` 'Friendly').

### A.4 `diplomacy_events.payload` (18,535 rows): `faction` 17,181 occ / 318 dist; `tracked[]` 3,394 / 302; `faction_keys[]` 487 / 134; `panel.faction` 4,672 / 308; `terms[]` / `panel.requested[]` / `panel.staged[]` (9-12 term names); `gift` small/medium/large; display text: `proposer` (186 / 77, e.g. 'Celestial Loyalists'), `speech`, `attitude` ('[[col:dip_attitude_4]]18[[/col]]'), `facts.reliability[]`, `panel.error`, `panel.response.text`.

### A.5 `app.game_event.payload` (stratified sample, 138,473 rows). Identifier keys (occ / dist / event kinds): `char_faction` 52,381 / 313 / 51 kinds; `char_subtype` 52,381 / 353 / 51; `unit` 9,333 / 456 / 9; `unit_faction` 9,333 / 215; `unit_force_faction` 7,108 / 215; `region` 6,653 / 671 / 11; `garrison` 14,721 / 722 / 14; `building` 1,631 / 402 / 2; `skill` 1,500 / 342 / 1; `ancillary` 1,891 / 496 / 2; `tech` 1,556 / 189 / 2; `ritual_rkey` 3,000 / 157 / 2; `ritual_chain_key` 200 / 33; `ritual_category` 3,000 / 59 (enum text e.g. CRAFTING_RITUAL); `ritual` 3,000 / 2,999 (`ACTIVE_RITUAL_SCRIPT_INTERFACE (0000...)` userdata addresses, useless); `mission_key` 1,691 / 209 / 3; `initiative_key` 137 / 48; `dilemma` 543 / 167 / 3 (includes incidents via IncidentOccuredEvent); `choice_key` 137 / 4 (FIRST..); `captive_record_key` 1,500 / 48; `captive_outcome_key` 1,500 / 4; `pr_resource_key` 3,000 / 46; `pr_factor_key` 1,500 / 58; `pr_threshold_op` 5 / 5; `agent_action_key` 1,516 / 23; `ability` 1,516 / 4; `attribute` 1,516 / 3; `item_variant` 147 / 19; `converted_unit` 732 / 21; `streak_effect` 455 / 7; `teleport_node` 18 / 6; `occupation_decision` 1,500 / 111 (numeric ids as text); `performing_faction` 3,005 / 89; `diplomacy_target` 5 / 3; `component` (= `context.string`) 20,979 / 594 -- a mix of UI component ids and faction keys. Numeric: `char_cqi` 52,381 occ, 964 dist, 1..1518; `mf_cqi` 3,573 / 310, 1..1050; `target_char` 1,502 / 191, 4..1469. `mission` (`context:mission():command_queue_index()`) never appeared (call fails inside `try`).

## B. Resolution

(a) = currently loaded `reference.*` tables; (b) = tables decoded from `db.pack` with `build_reference.decode_db_table` + `schema_db.json` (53 tables). For pack tables absent from `schema_db.json` the decoder cannot run; for those, (b) is a byte-level test: the value's length-prefixed UTF-8 encoding (`struct.pack('<H',len)+bytes`, the `StringU8` layout read by `build_reference._Reader.s_u8`, :115-116) occurs in the zstd-decompressed `db/<table>/` file. That test proves the string is stored in that table, not which column. Percentages are over distinct values / over occurrences. Loc coverage figures are in E.

| group (paths, dist, occ) | (a) table.column [size] -> resolved | (b) pack table.column [size] -> resolved | unresolved / diagnosis |
|---|---|---|---|
| skill (13 paths, 4,210, 345,569) | reference.skills.key [5,944] -> 100% / 100% | character_skills_tables.key [5,944] -> 100% / 100% | none. Keys with '-' or '&' (`..._self_foe-seeker`, `..._rune_of_oath_&_steel`) are real keys. Game-captured `reference.ref_skill.skill` covers 3,057 of 3,291 distinct lord/hero/action skill keys (it is per subtype: 240 subtypes loaded). |
| unit (21 paths, 1,242, 122,130) | reference.units.key [2,609] -> 100% / 100% | main_units_tables.unit [2,609] -> 100% / 100% | none |
| unit in dilemma subtree `CcoMainUnitRecord:<key>` (16, 224) | 100% / 100% | 100% / 100% | none (prefix stripped) |
| building (14 paths, 1,437, 64,205) | reference.buildings.key [5,259] -> 100% / 100% | building_levels_tables.level_name [5,259] -> 100% / 100% | none; `wh_pro02_VAMPIRES_isabella_unique`, `wh3_main_DWARFS_underdeep_1`, `wh_dlc03_HUMAN_settlement_minor_beastmen` are real (mixed-case) keys |
| building in `force_slot_N@<key>` (78, 350) | 100% / 100% | 100% / 100% | |
| tech NodeKey (3 paths, 1,778, 123,421) | reference.tech.key [2,056] -> 100% / 100% | technology_nodes_tables.key [1,843] (+technologies_tables.key) -> 100% / 100% | all 1,778 are node keys; 1,097 of them also equal a technologies_tables.key, 952 have node_key == technology_key. Game-captured `reference.ref_tech` holds 1,746 of 1,778 distinct tech keys, but only 88.2% of (faction, tech) pairs in the sample (5,163 of 5,851; missing for wh_dlc08_nor_wintertooth 83 pairs, wh2_dlc09_tmb_lybaras 74, ...; 92 factions loaded). |
| tech from events `context:technology()` (2 paths, 189, 1,578) | reference.tech.key -> 69.3% / 71.3%; reference.tech.technology_key -> 100% | technologies_tables.key [1,869] -> 100% / 100% (only 131 also in technology_nodes.key) | events carry the technology key, decision blobs carry the node key; `reference.tech.key` stores node keys and only adds technology keys that have no node (`build_reference.py:432-450`). Unresolved-in-key examples: `wh3_dlc20_chs_und_shared_marauders`, `tech_grn_start_2_2`, `wh3_main_tech_ogr_0_0_0`. |
| region (46 paths, 811, 330,780) | reference.ref_region.region [814] -> 100% / 100% | regions_tables.key [945] -> 100% / 100% | none. ref_region was captured once by `decisions/gameref.py` from a live campaign: 562 `wh3_main_combi_region_*`, 226 `wh3_main_chaos_region_*` (all chaos rows have NULL x/y/province/is_capital/climate), plus dlc20/dlc23 sets and `wh_main_unknown_terrain`. |
| region in `settlement:<key>` (335, 2,709) and `<key>@<slot>` (264, 2,326) | 100% | 100% | |
| province (5 paths, 294, 51,955) | reference.ref_region.province (distinct) [214] -> 72.8% / 85.8% | provinces_tables.key [316] -> 100% / 100% (also region_to_province_junctions_tables) | (a) misses every `wh3_main_chaos_province_*` (e.g. `wh3_main_chaos_province_zorn_uzkul` 500 occ) because ref_region's chaos rows have NULL province. 33 `province.province` values are '' (skipped). |
| faction (89 paths, 347, 441,892) | distinct reference.agent_permitted_subtypes.faction [638] -> 98.8% / 98.7% | factions_tables.key [717] -> 98.8% / 98.7% | 4 unresolved: `rebels` 5,343 (engine pseudo-faction: `world.regions[].owner` 5,259, `world.armies[].region_owner` 9, game_event 79), `ruins` 288 (masking literal, collect.py:406), `wh_main_grn_skull` 20 (truncation of `wh_main_grn_skull-takerz` by the hyphen-less regex `launcher/diplo_stream.py:14`; 91 diplomacy_events rows in 21 campaigns carry it, 200 rows carry the full key), `campaign` 1 (actions row 37129). No reference table holds faction keys as a primary key; `factions_tables` is in `schema_db.json` but not loaded into `reference.*`. |
| faction in `<faction>:<term>` (330, 99,207) | 100% / 100% | 100% / 100% | |
| faction prefix of `campaign_uuid` (115, 2,070) | 87.8% / 83.4% | 87.8% / 83.4% | artefact of the split (`rsplit('_',2)`): 327 of 2,013 uuids have one 11-hex suffix, 1,686 have two; not a corpus defect -- `campaigns.faction` holds the key directly |
| agent_subtype (59 paths, 586, 207,936) | distinct reference.agent_permitted_subtypes.subtype [565] -> 93.2% / 97.7%; loc `agent_subtypes_onscreen_name_override_` -> 100% | faction_agent_permitted_subtypes_tables.subtype [565] -> 93.2% / 97.7%; byte test in agent_subtypes_tables (no schema) -> 586/586 = 100% | 40 unresolved in the permitted-subtypes table are non-recruitable legendary/scripted subtypes: `wh3_dlc23_chd_drazhoath` 572, `wh3_dlc23_chd_astragoth` 443, `wh3_dlc23_chd_zhatan` 256, `wh3_demo_kho_cha_exalted_bloodthirster` 191, `wh3_survival_kho_cha_daemon_prince_of_khorne(_fe)` 191+191, `wh_pro02_vmp_isabella_von_carstein` 190, `wh3_dlc20_chs_daemon_prince_{khorne,nurgle,slaanesh,tzeentch,undivided}` 162 each, ... All 40 are present in `agent_subtypes_tables` and have a loc name. `agent_subtypes_tables` is not in `schema_db.json`. |
| agent_subtype in `<subtype>@<idx>` (273, 27,868) | 100% / 100% | 100% / 100% | |
| agent_type (7 paths, 9, 53,140) | reference.agent_types.key [9] -> 100% | agents_tables.key [9] -> 100% | none |
| ancillary (10 paths, 797, 49,754) | reference.ancillaries.key [2,671] -> 100% / 100% | ancillaries_tables.key [2,671] -> 100% / 100% | none |
| ritual (5 paths, 800, 39,993) | reference.rituals.key [1,326] -> 100% / 100% | rituals_tables.key [1,326] -> 100% / 100% | none. `wh3_dlc27_hef_court_action_tribute` (actions.rites) is a rituals_tables key. |
| ritual_chain (2 paths, 33, 206) | none loaded -> 0% | ritual_chains_tables.key [91] -> 100% | table decodes but is not loaded |
| trait (7 paths, 120, 3,131) | reference.trait_meta.trait [744] -> 100% / 100% | character_traits_tables.key [744] -> 100% | `lord_pools{}.traits[][]` and `actions.recruit_lord.params.trait(s)` carry `key~level` composites (collect.py:1343); resolved after splitting on '~' |
| trait level key (2 paths, 43, 184) | reference.trait_levels.level_key [979] -> 100% | character_trait_levels_tables.key [979] -> 100% | |
| mission (4 paths, 341, 3,925) | none loaded -> 0% | missions_tables.key [3,186] -> 100% / 100% | decodes, not loaded |
| edict / provincial initiative (5 paths, 109, 17,243) | none loaded -> 0% | byte test provincial_initiative_records_tables -> 100% / 100% | no schema in `schema_db.json`; loc family `provincial_initiative_records_localised_name_` and `effect_bundles_localised_title_` both carry these keys |
| effect_bundle (5 paths, 324, 3,228) | none -> 0% | byte test effect_bundles_tables -> 100% / 100% | no schema |
| dilemma from interrupts (2 paths, 146, 5,098) | none -> 0% | dilemmas_tables.key | incidents_tables.key [1,127 + 1,754] -> 97.3% / 98.3% | prefix `CcoCdirEventsDilemmaChoiceDetailRecord` stripped. 4 unresolved are commonprefix artefacts of `launcher/interrupts.py:1738-1745`: `wh_dlc03_full_moon_preparations_solar_eclipseSCRIPTED_` 80 (options `SCRIPTED_1..n` share `SCRIPTED_`), and single-option dilemmas where dilemma_id keeps the option suffix (`..._stitch_up_1SECOND` 4, `..._you_can_danceTHIRD` 2, `..._elector_politics_15SECOND` 2). |
| dilemma/incident from events (3 paths, 167, 546) | none -> 0% | dilemmas | incidents -> 100% / 100% (65 dilemmas, 102 incidents) | `IncidentOccuredEvent.dilemma` holds incident keys |
| pooled_resource (4 paths, 231, 40,704) | none -> 0% | byte test pooled_resources_tables -> 100% / 100% | no schema; includes `wh3_dlc26_ogr_meat_CAI` |
| stance (5 paths, 18, 47,226) | none -> 0% | byte test campaign_stances_tables -> 100% / 100% | keys are the engine enum strings `MILITARY_FORCE_ACTIVE_STANCE_TYPE_*`; no loc entry exists for them |
| agent_action (3 paths, 27, 11,504) | reference.agent_actions.key [176] -> 100% | agent_actions_tables.unique_id [176] -> 100% | |
| captive_record_key (1 path, 49, 1,548) | reference.captive_options.record_key [95] -> 100% | campaign_post_battle_captive_options_tables.record_key [95] -> 100% | numeric-looking strings ('1352401758') are the real record keys |
| armory_item_variant (3 paths, 19, 171) | none -> 0% | byte test armory_item_variants_tables -> 100% | no schema |
| mission_issuer (1 path, 5, 2,219) | none -> 0% | byte test mission_issuers_tables -> 100% | CLAN_ELDERS 2,176, BOOK_NAGASH 40, _DEFAULT_, BLACK_TOOF, BLESSED_SPAWNING |
| unit_category (1 path, 6, 20,833) | distinct reference.units.category -> 100% | land_units_tables.category -> 100% | inf_melee, inf_ranged, war_beast, ... |
| streak_effect (7, 458) | none | byte test campaign_streak_effects_tables -> 7/7 | |
| teleport_node (6, 18) | none | byte test teleportation_network_nodes_tables -> 6/6 | |
| occupation option keys in interrupts (38, 22,901) | none | none | lower-cased button labels ('loot & occupy'); no loc text equals them (only `culture_settlement_occupation_options_tooltip_<id>` keys exist, no onscreen_name family); not resolvable to a key |
| occupation_decision from events (111 in stratified sample, 1,500) | none | numeric record ids (I32) -- byte test not applicable; 111/111 match the numeric suffix of loc keys `culture_settlement_occupation_options_tooltip_<id>` | the id is the primary key of `culture_settlement_occupation_options_tables` (no schema) |

Composite / non-key forms found: `cqi:N` (attack_army, hero_action), `xy:x,y` (move), `<region>@<slot>`, `<subtype>@<idx>`, `<unit>@<idx>`, `force_slot_N@<building>`, `<faction>:<term>`, `settlement:<region>`, `trait~level`, `CcoCdirEventsDilemmaChoiceDetailRecord<dilemma><OPTION>`, `CcoMainUnitRecord:<unit>`, `<faction>_<hex>_<hex>` (campaign_uuid), building loc key `building_culture_variants_name_<building><culture>`.

Mod content: none detected -- every key group resolves 100% against vanilla `db.pack` except the four faction pseudo-values and the dilemma-id artefacts.

## C. Localised text in the corpus

Paths storing display strings rather than keys, whether the key is stored alongside, and byte volume (UTF-8 bytes of the string values only):

| path | key also stored? | bytes in sample | share |
|---|---|---|---|
| campaign.leader and campaign_entity.leader (leader display name) | no key for the leader in the campaign dict; `world.armies[].is_leader`+`cqi`+`subtype` exist separately | 35,834 + 35,834 | of 123.55 MB decision sample |
| campaign_entity.anc_pool[].name | yes (`.key`, 441 dist vs 425 names) | 88,255 (keys: 207,438) | |
| campaign_entity.equipped_all[].name | yes | 55,429 (keys 130,435) | |
| lord.equipped[].name / hero.equipped[].name | yes | 39,446 / 12,831 | |
| campaign_entity.rites[].invalid_reason | key present (`rites[].key`) | 1,188,551 | 1.0% of sample; 44 distinct strings, mixes `{{tr:...}}` unresolved loc tokens and `[[col:red]]...[[/col]]` markup |
| total loc-text in decision blobs | | 1,456,180 B = 1.18% | |
| actions.items.params.item_name / item_unequip.params.item_name | yes (`item_key` = `action_key`) | 221,161 + 91,537 = 0.82% of 38.1 MB params in the actions sample; 1,913,031 B of params over all 18,325 items/item_unequip rows | |
| interrupts.dilemma.options{}.text (316 dist), payload[] (525), subtree[].text (823) | dilemma key in `root_context`, option in `option_id` | text 499,330 + payload 229,357 inside 20.98 MB dilemma options_json (subtree paths/ids alone 10.77 MB) | |
| interrupts.occupation.options{key} = button label | no key at all | 1.48 MB options_json over 6,755 rows | |
| interrupts panel_blob (battle_results 12.14 MB, pre_battle 2.26 MB, diplomacy_proposal 0.28 MB, war_declared 0.15 MB, occupation 0.07 MB) | army/faction display names, outcome, race, attitude labels; no keys except numbers | | |
| diplomacy_events.payload.proposer (77 dist names), speech, attitude markup, facts.reliability[], panel.error, panel.response.text | `faction` key present in most rows; `proposer` rows have `faction_keys[]` (regex-extracted, unaligned) | 2,572 + 10,071 + 6,041 + 5,580 + 144,646 + 35,386 = 3.6% of 5.64 MB sample payload | |
| app.game_event | no display text keys in `ctx()` (`twstate.lua:876-997`); `ritual` = userdata address string, `component` = UI id | | |

Largest string-byte paths in the decision sample are keys, not text: `lord.skills[].key` 6.80 MB (5.5%), `world.regions[].adjacent[]` 3.44 MB (2.8%), `hero.skills[].key` 3.38 MB, `campaign_entity.tech[].key` 2.87 MB, `lord_pools{}.bg_skills[]` 2.17 MB, `lord.merc_pools{}[].key` 1.80 MB, `rites[].key` 1.72 MB.

## D. Numeric identifiers

- Character cqi appears as: JSON number in `world.armies[].cqi` (1..1550 in sample, p50 490, p90 1371) and `world.hostiles[].cqi` (1..1563), `actions.*.params.target_cqi` (6..1603), `app.game_event.char_cqi` (1..1518), `target_char` (4..1469); as text in `lord/hero.context_id`, `lord/hero.cqi`, `world.enemy_agents[].cqi`, `world.citizenry[]`, `world.stationed{}`, `reach_chars{key}`, `actions.lord/hero.context_id`, and embedded in `cqi:N` action keys. `lord_pools{}.cqis[]` and `actions.recruit_*.params.cqi` are always '0'.
- Military-force cqi exists only in `app.game_event.mf_cqi` (1..1050) -- the decision collector never stores it (`collect.py` reads `MilitaryForceContext` but keeps no `mf_cqi`).
- Faction cqi: `campaign.faction_cqi` (text). Over the 2,013 sampled campaigns, (faction, campaign_map) -> faction_cqi is a function (128 pairs, 0 conflicts); 23 factions seen on both maps have a different faction_cqi per map (e.g. `wh3_dlc20_chs_festus` 20 on chaos / 86 on combi).
- cqi values are per-campaign: 1,048 distinct character cqi values, 931 occur in more than one campaign, and 452 of those denote different (subtype, faction) in different campaigns. Within a campaign a cqi is unique; the faction leader's cqi is the same across campaigns of the same (faction, map) in 113 of 128 pairs (the 15 exceptions have a second, later-spawned leader cqi >1300 after the original died, e.g. `wh_main_vmp_schwartzhafen`: 160 and 1410).
- Cross-campaign identity of characters: the corpus stores no character name (only the faction leader's display name in `campaign.leader`) and no start position per character, so an entity is identifiable across campaigns only by `subtype` when the subtype is unique per faction (legendary lords/heroes). Among own armies, 1,385 of 7,499 (campaign, subtype) pairs have more than one cqi (generic `wh_main_vmp_lord` 185 pairs, `wh_main_dwf_lord` 115, ...), i.e. generic-subtype characters are not distinguishable across campaigns. Region and province keys are stable across campaigns of the same map.
- Other numeric ids: `captive_record_key` (e.g. 1352401758) and `occupation_decision` (e.g. 1129, 1673500944) are db record keys, stable across campaigns (see B); slot indices `province.built{key}` 0..5; `force_slot_N` ids are per-force runtime slot ids (139 distinct).

## E. Loc coverage of resolved keys (`reference.loc` == `local_en.pack`, both 241,972 entries; results identical in both)

| group | loc key family | covered |
|---|---|---|
| skill (4,210) | `character_skills_localised_name_<key>` | 4,210 / 4,210 |
| unit (1,242) | `land_units_onscreen_name_<land_unit>` | 1,201 / 1,242 by unit key; 1,242 / 1,242 via `main_units_tables.land_unit` (41 units have land_unit != unit key) |
| building (1,437) | `building_culture_variants_name_<building><culture>` (composite, prefix match) | 1,436 / 1,437; missing `wh_dlc06_grn_boars_1_skarsnik` |
| tech NodeKey (1,778) | `technologies_onscreen_name_<technology_key>` | 1,097 / 1,778 by node key; 1,778 / 1,778 via `technology_nodes_tables.technology_key`; event tech 189 / 189 |
| region (811) | `regions_onscreen_<key>` | 811 / 811 |
| province (294) | `provinces_onscreen_<key>` | 294 / 294 |
| faction (343 resolved) | `factions_screen_name_<key>` | 343 / 343 (`rebels`/`ruins` have none) |
| agent_subtype (586) | `agent_subtypes_onscreen_name_override_<key>` | 586 / 586 (incl. the 40 not in permitted-subtypes) |
| agent_type (9) | UNKNOWN -- no `agents_*` loc family exists; `agents_onscreen_name_<key>` 0 / 9 | |
| ancillary (797) | `ancillaries_onscreen_name_<key>` | 797 / 797 |
| ritual (800) | `rituals_display_name_<key>` | 800 / 800 |
| ritual_chain (33) | `ritual_chains_display_name_<key>` | 33 / 33 |
| trait (120) | `character_trait_levels_onscreen_name_<trait>_<level>` (per level) | 119 / 120; missing `wh3_dlc25_trait_chieftain_kazyk_antitrait` |
| trait level key (43) | same family, exact | 43 / 43 |
| mission (341) | `missions_localised_title_<key>` | 341 / 341 |
| mission issuer (5) | `mission_issuers_on_screen_name_<key>` | 5 / 5 |
| edict (109) | `provincial_initiative_records_localised_name_<key>` | 109 / 109 |
| effect_bundle (324) | `effect_bundles_localised_title_<key>` | 324 / 324 |
| dilemma (interrupt, 142 resolved) | `dilemmas_localised_title_<key>` | 142 / 142 |
| dilemma/incident (events, 167) | `dilemmas_localised_title_` 65 + `incidents_localised_title_` 102 | 167 / 167 |
| pooled_resource (231) | `pooled_resources_display_name_<key>` | 231 / 231 |
| stance (18) | none -- no loc key contains `MILITARY_FORCE_ACTIVE_STANCE_TYPE` | 0 / 18 |
| agent_action (27) | `agent_actions_localised_action_name_<key>` | 27 / 27 |
| captive_record_key (49) | `campaign_post_battle_captive_options_onscreen_name_<key>` | 49 / 49 |
| armory_item_variant (19) | `armory_item_variant_ui_infos_localised_name_<key>` | 13 / 19 (missing e.g. `..._arm_l_base_01`, `..._tail_snapping_04`) |
| occupation_decision ids (111) | `culture_settlement_occupation_options_tooltip_<id>` | 111 / 111 |
| streak_effect, teleport_node, unit_category | no loc family found (UNKNOWN) | |

## F. Leftover objects touched (names/counts only)

`reference.dilemma_choice` (2,866 rows) and `reference.skill_actions` (775 rows) are created by no code in the working tree; `skill_actions` is read by `advisor/reference/features_db.py:322` and `advisor/mapgraph/catalogue.py:56`, `dilemma_choice` by nothing. Neither was used above. `reference.meta`: built 1786170352.79, extra_built 1788319259.69.

## G. Scripts (run from D:\tw_stack with `.venv/Scripts/python.exe`; scratchpad = `C:/Users/trist/AppData/Local/Temp/claude/D--tw-stack/503ea3b2-0e04-45e7-b92b-dfb232c06b94/scratchpad`)

### G.1 m3_extract.py -- decision sample

```python
import sys, time, json, pickle, collections, os
sys.path.insert(0, 'D:/tw_stack')
from decisions import pg
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m3_corpus.pkl")
MOD, RES = 100, 7
DYN = {"campaign.resources", "campaign.read_failures", "campaign.hero_type_counts",
       "campaign.lord_pools", "world.stationed", "lord.reach_chars", "lord.reach_setts",
       "lord.trait_progress", "lord.merc_pools", "hero.reach_chars", "hero.reach_setts",
       "hero.trait_progress", "province.built", "province.building_now",
       "province.corruption", "campaign_entity.resources", "campaign_entity.read_failures",
       "campaign_entity.hero_type_counts", "campaign_entity.lord_pools"}
vals = collections.defaultdict(collections.Counter)
nums = collections.defaultdict(collections.Counter)
bytes_ = collections.Counter()
per_camp = collections.defaultdict(lambda: collections.defaultdict(set))
def walk(o, path, camp):
    if isinstance(o, dict):
        dyn = path in DYN
        for k, v in o.items():
            if dyn:
                vals[path + "{key}"][str(k)] += 1
                bytes_[path + "{key}"] += len(str(k))
                walk(v, path + "{}", camp)
            else:
                walk(v, path + "." + k, camp)
    elif isinstance(o, list):
        for v in o:
            walk(v, path + "[]", camp)
    elif isinstance(o, str):
        vals[path][o] += 1
        bytes_[path] += len(o.encode("utf-8"))
        if "cqi" in path.split(".")[-1] or path.endswith("context_id"):
            per_camp[path][camp].add(o)
    elif isinstance(o, bool) or o is None:
        pass
    elif isinstance(o, (int, float)):
        last = path.split(".")[-1]
        if "cqi" in last or last in ("leader_cqi", "equipped_to", "fm_cqi", "slot_cqi"):
            nums[path][o] += 1
            per_camp[path][camp].add(o)
con = pg.connect(autocommit=False, readonly=True)
con.execute("SET statement_timeout = '900s'")
con.execute("SET work_mem = '256MB'")
t0 = time.time()
cur = con.cursor(name="m3dec"); cur.itersize = 100
cur.execute("SELECT d.decision_id, d.campaign_id, bc.z, bw.z FROM decisions d"
            " LEFT JOIN blobs bc ON bc.blob_id=d.campaign_blob"
            " LEFT JOIN blobs bw ON bw.blob_id=d.world_blob"
            " WHERE d.decision_id %% %s = %s" % (MOD, RES))
ndec = 0
for did, cid, cz, wz in cur:
    ndec += 1
    walk(json.loads(cz or "{}"), "campaign", cid)
    walk(json.loads(wz or "{}"), "world", cid)
cur.close()
cur = con.cursor(name="m3ent"); cur.itersize = 200
cur.execute("SELECT e.decision_id, d.campaign_id, e.context_kind, e.context_id, b.z"
            " FROM entities e JOIN decisions d ON d.decision_id=e.decision_id"
            " JOIN blobs b ON b.blob_id=e.features_blob"
            " WHERE e.decision_id %% %s = %s" % (MOD, RES))
nent = collections.Counter()
for did, cid, ck, ctx, z in cur:
    nent[ck] += 1
    role = "campaign_entity" if ck == "campaign" else ck
    vals[role + ".context_id"][ctx] += 1
    per_camp[role + ".context_id"][cid].add(ctx)
    walk(json.loads(z or "{}"), role, cid)
cur.close(); con.close()
pickle.dump({"vals": dict(vals), "nums": dict(nums), "bytes": dict(bytes_),
             "per_camp": {p: {c: sorted(s, key=str) for c, s in d.items()} for p, d in per_camp.items()},
             "ndec": ndec, "nent": dict(nent)}, open(OUT, "wb"))
print("decisions", ndec, "entities", dict(nent), "paths", len(vals), "%.1fs" % (time.time() - t0))
```

### G.2 m3_extract2.py -- actions / interrupts / diplomacy / game_event random sample

```python
import sys, time, json, pickle, collections, os
sys.path.insert(0, 'D:/tw_stack')
from decisions import pg
HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, "m3_corpus2.pkl")
vals = collections.defaultdict(collections.Counter); nums = collections.defaultdict(collections.Counter)
bytes_ = collections.Counter(); per_camp = collections.defaultdict(lambda: collections.defaultdict(set))
def walk(o, path, camp=None):
    if isinstance(o, dict):
        for k, v in o.items(): walk(v, path + "." + k, camp)
    elif isinstance(o, list):
        for v in o: walk(v, path + "[]", camp)
    elif isinstance(o, str):
        vals[path][o] += 1; bytes_[path] += len(o.encode("utf-8"))
        if camp is not None and "cqi" in path.split(".")[-1]: per_camp[path][camp].add(o)
    elif isinstance(o, bool) or o is None: pass
    elif isinstance(o, (int, float)):
        last = path.split(".")[-1]
        if "cqi" in last or last in ("target_char", "leader_cqi", "equipped_to", "fm_cqi", "slot_cqi", "mission"):
            nums[path][o] += 1
            if camp is not None: per_camp[path][camp].add(o)
con = pg.connect(autocommit=False, readonly=True)
con.execute("SET statement_timeout = '900s'"); con.execute("SET work_mem = '256MB'")
t0 = time.time()
cur = con.cursor(name="m3act"); cur.itersize = 2000
cur.execute("SELECT action_type, context_kind, context_id, action_key, params FROM actions"
            " WHERE action_type <> 'move' OR action_id % 100 = 0")
nact = collections.Counter()
for at, ck, cid, ak, params in cur:
    nact[at] += 1
    vals["actions.%s.action_key" % at][ak] += 1; bytes_["actions.%s.action_key" % at] += len(ak.encode("utf-8"))
    vals["actions.%s.context_id" % ck][cid] += 1
    walk(json.loads(params or "{}"), "actions.%s.params" % at)
cur.close()
cur = con.cursor(name="m3int"); cur.itersize = 500
cur.execute("SELECT i.kind, i.root, i.root_context, i.options_json, i.chosen, i.chosen_context,"
            " i.campaign_id, bp.z FROM interrupts i LEFT JOIN blobs bp ON bp.blob_id=i.panel_blob")
nint = collections.Counter()
for kind, root, rctx, oj, chosen, cctx, camp, pz in cur:
    nint[kind] += 1; p = "interrupts.%s" % kind
    for k, v in (("root", root), ("root_context", rctx), ("chosen", chosen), ("chosen_context", cctx)):
        if v is not None: vals[p + "." + k][str(v)] += 1; bytes_[p + "." + k] += len(str(v).encode("utf-8"))
    try: opts = json.loads(oj) if oj else {}
    except ValueError: opts = {}
    for ok_, ov in opts.items():
        vals[p + ".options{key}"][str(ok_)] += 1; walk(ov, p + ".options{}", camp)
    if pz and nint[kind] <= 400: walk(json.loads(pz), p + ".panel", camp)
cur.close()
cur = con.cursor(name="m3dip"); cur.itersize = 2000
cur.execute("SELECT campaign_key, payload FROM diplomacy_events WHERE event_id % 4 = 0")
ndip = 0
for camp, payload in cur:
    ndip += 1; walk(json.loads(payload or "{}"), "diplomacy_events.payload", camp)
cur.close()
cur = con.cursor(name="m3ev"); cur.itersize = 2000
cur.execute("SELECT event, campaign_key, payload FROM app.game_event TABLESAMPLE SYSTEM (1.0)")
nev = collections.Counter()
for ev, camp, payload in cur:
    nev[ev] += 1; walk(json.loads(payload or "{}"), "game_event.%s" % ev, camp)
cur.close(); con.rollback(); con.close()
pickle.dump({"vals": dict(vals), "nums": dict(nums), "bytes": dict(bytes_),
             "per_camp": {p: {c: sorted(s, key=str) for c, s in d.items()} for p, d in per_camp.items()},
             "nact": dict(nact), "nint": dict(nint), "ndip": ndip, "nev": dict(nev)}, open(OUT, "wb"))
print(sum(nact.values()), dict(nint), ndip, sum(nev.values()), "%.1fs" % (time.time() - t0))
```

### G.3 m3_extract3.py -- game_event stratified by kind

```python
import sys, time, json, pickle, collections, os
sys.path.insert(0, 'D:/tw_stack')
from decisions import pg
HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, "m3_events.pkl"); PER_KIND = 1500
vals = collections.defaultdict(collections.Counter); nums = collections.defaultdict(collections.Counter)
per_camp = collections.defaultdict(lambda: collections.defaultdict(set))
def walk(o, path, camp):
    if isinstance(o, dict):
        for k, v in o.items(): walk(v, path + "." + k, camp)
    elif isinstance(o, list):
        for v in o: walk(v, path + "[]", camp)
    elif isinstance(o, str): vals[path][o] += 1
    elif isinstance(o, bool) or o is None: pass
    elif isinstance(o, (int, float)):
        if path.split(".")[-1] in ("char_cqi", "mf_cqi", "target_char", "mission", "occupation_decision"):
            nums[path][o] += 1; per_camp[path][camp].add(o)
con = pg.connect(autocommit=True, readonly=True); con.execute("SET statement_timeout = '900s'")
kinds = [r[0] for r in con.execute("SELECT DISTINCT event FROM app.game_event").fetchall()]
nrows = collections.Counter()
for ev in kinds:
    for camp, payload in con.execute("SELECT campaign_key, payload FROM app.game_event WHERE event=%s"
                                     " ORDER BY event_id DESC LIMIT %s", (ev, PER_KIND)).fetchall():
        nrows[ev] += 1; walk(json.loads(payload or "{}"), "game_event." + ev, camp)
con.close()
pickle.dump({"vals": dict(vals), "nums": dict(nums), "nrows": dict(nrows),
             "per_camp": {p: {c: sorted(s) for c, s in d.items()} for p, d in per_camp.items()}}, open(OUT, "wb"))
```

### G.4 m3_resolve.py -- resolution against reference.* and db.pack (core; the GROUPS/TARGETS tables are as listed in B)

```python
import sys, os, re, time, pickle, struct, collections
sys.path.insert(0, 'D:/tw_stack'); sys.path.insert(0, 'D:/tw_stack/advisor/reference')
import common, build_reference as B
from decisions import pg
HERE = os.path.dirname(os.path.abspath(__file__))
C1 = pickle.load(open(os.path.join(HERE, "m3_corpus.pkl"), "rb")); C2 = pickle.load(open(os.path.join(HERE, "m3_corpus2.pkl"), "rb"))
C3 = pickle.load(open(os.path.join(HERE, "m3_events.pkl"), "rb"))
VALS = {}; VALS.update(C1["vals"]); VALS.update(C2["vals"])
for p, c in C3["vals"].items(): VALS.setdefault(p, collections.Counter()).update(c)
files, d = B.parse_pack(common.GAME_DATA_DIR + "/db.pack"); schema = B.load_db_schema(); _dec = {}
def pack_col(table, col):
    if table not in _dec:
        rows, meta = B.decode_db_table(files, d, table, schema); assert meta["ok"], (table, meta); _dec[table] = rows
    return {str(r[col]) for r in _dec[table]}
_raw = {}
def raw_table(table):
    if table not in _raw:
        ents = [n for (n, off, sz, c) in files if n.replace("\\", "/").startswith("db/%s/" % table)]
        _raw[table] = b"".join(B.read_file(files, d, n) for n in ents)
    return _raw[table]
def bytes_present(table, key):
    k = key.encode("utf-8"); return (struct.pack("<H", len(k)) + k) in raw_table(table)
con = pg.connect(autocommit=True, readonly=True, search_path="reference"); con.execute("SET statement_timeout = '900s'")
def ref_col(sql): return {str(r[0]) for r in con.execute(sql).fetchall() if r[0] is not None}
LOC = {}
def loc_keys(prefix):
    if prefix not in LOC:
        LOC[prefix] = {r[0][len(prefix):] for r in con.execute("SELECT key FROM loc WHERE key LIKE %s", (prefix + "%",)).fetchall()}
    return LOC[prefix]
lfiles, ld = B.parse_pack(common.GAME_DATA_DIR + "/local_en.pack"); PACK_LOC = {}
for name, off, size, comp in lfiles:
    if name.endswith(".loc"): PACK_LOC.update(B.decode_loc(B.read_file(lfiles, ld, name)))
ident = lambda v: v
strip_prefix = lambda pre: (lambda v: v[len(pre):] if v.startswith(pre) else v)
before = lambda ch: (lambda v: v.split(ch, 1)[0]); after = lambda ch: (lambda v: v.split(ch, 1)[1] if ch in v else v)
SKIP = {"", "nil", "none", "-", "0"}
GROUPS = [  # (name, [paths], normaliser) -- full list as in section B, e.g.
 ("skill", ["lord.skills[].key", "hero.skills[].key", "lord.hidden_skills[]", "hero.hidden_skills[]",
            "lord.hidden_skill_states[].key", "hero.hidden_skill_states[].key", "lord.background_skill",
            "hero.background_skill", "campaign_entity.lord_pools{}.bg_skills[]", "actions.skills.action_key",
            "actions.recruit_hero.params.bg_skill", "actions.recruit_lord.params.bg_skill",
            "game_event.CharacterSkillPointAllocated.skill"], ident),
 ("faction (diplomacy action_key faction:term)", ["actions.diplomacy.action_key"], before(":")),
 ("dilemma (interrupt root_context)", ["interrupts.dilemma.root_context", "interrupts.dilemma.options{}.dilemma_id"],
  strip_prefix("CcoCdirEventsDilemmaChoiceDetailRecord")),
 ("trait", ["lord.traits[].key", "hero.traits[].key", "lord.trait_progress{key}", "hero.trait_progress{key}",
            "campaign_entity.lord_pools{}.traits[][]", "actions.recruit_lord.params.trait", "actions.recruit_lord.params.traits[]"], before("~")),
 # ... remaining groups/paths exactly as enumerated in section B
]
TARGETS = {  # base -> (name_a, set_a, name_b, set_b or None for byte test, [loc prefixes])
 "skill": ("reference.skills.key", ref_col("SELECT key FROM skills"), "character_skills_tables.key", pack_col("character_skills_tables", "key"), ["character_skills_localised_name_"]),
 "unit": ("reference.units.key", ref_col("SELECT key FROM units"), "main_units_tables.unit", pack_col("main_units_tables", "unit"), ["land_units_onscreen_name_"]),
 "building": ("reference.buildings.key", ref_col("SELECT key FROM buildings"), "building_levels_tables.level_name", pack_col("building_levels_tables", "level_name"), ["building_culture_variants_name_"]),
 "tech": ("reference.tech.key", ref_col("SELECT key FROM tech"), "technology_nodes_tables.key|technologies_tables.key", pack_col("technology_nodes_tables", "key") | pack_col("technologies_tables", "key"), ["technologies_onscreen_name_"]),
 "region": ("reference.ref_region.region", ref_col("SELECT region FROM ref_region"), "regions_tables.key", pack_col("regions_tables", "key"), ["regions_onscreen_"]),
 "province": ("reference.ref_region.province", ref_col("SELECT DISTINCT province FROM ref_region"), "provinces_tables.key", pack_col("provinces_tables", "key"), ["provinces_onscreen_"]),
 "faction": ("reference.agent_permitted_subtypes.faction", ref_col("SELECT DISTINCT faction FROM agent_permitted_subtypes"), "factions_tables.key", pack_col("factions_tables", "key"), ["factions_screen_name_"]),
 "agent_subtype": ("reference.agent_permitted_subtypes.subtype", ref_col("SELECT DISTINCT subtype FROM agent_permitted_subtypes"), "faction_agent_permitted_subtypes_tables.subtype", pack_col("faction_agent_permitted_subtypes_tables", "subtype"), ["agent_subtypes_onscreen_name_override_"]),
 "agent_type": ("reference.agent_types.key", ref_col("SELECT key FROM agent_types"), "agents_tables.key", pack_col("agents_tables", "key"), []),
 "ancillary": ("reference.ancillaries.key", ref_col("SELECT key FROM ancillaries"), "ancillaries_tables.key", pack_col("ancillaries_tables", "key"), ["ancillaries_onscreen_name_"]),
 "ritual": ("reference.rituals.key", ref_col("SELECT key FROM rituals"), "rituals_tables.key", pack_col("rituals_tables", "key"), ["rituals_display_name_"]),
 "ritual_chain": ("(none)", set(), "ritual_chains_tables.key", pack_col("ritual_chains_tables", "key"), ["ritual_chains_display_name_"]),
 "trait": ("reference.trait_meta.trait", ref_col("SELECT trait FROM trait_meta"), "character_traits_tables.key", pack_col("character_traits_tables", "key"), ["character_trait_levels_onscreen_name_"]),
 "trait_level_key": ("reference.trait_levels.level_key", ref_col("SELECT level_key FROM trait_levels"), "character_trait_levels_tables.key", pack_col("character_trait_levels_tables", "key"), ["character_trait_levels_onscreen_name_"]),
 "mission": ("(none)", set(), "missions_tables.key", pack_col("missions_tables", "key"), ["missions_localised_title_"]),
 "edict": ("(none)", set(), "provincial_initiative_records_tables", None, ["provincial_initiative_records_localised_name_"]),
 "effect_bundle": ("(none)", set(), "effect_bundles_tables", None, ["effect_bundles_localised_title_"]),
 "dilemma": ("(none)", set(), "dilemmas_tables.key|incidents_tables.key", pack_col("dilemmas_tables", "key") | pack_col("incidents_tables", "key"), ["dilemmas_localised_title_", "incidents_localised_title_"]),
 "pooled_resource": ("(none)", set(), "pooled_resources_tables", None, ["pooled_resources_display_name_"]),
 "stance": ("(none)", set(), "campaign_stances_tables", None, []),
 "agent_action": ("reference.agent_actions.key", ref_col("SELECT key FROM agent_actions"), "agent_actions_tables.unique_id", pack_col("agent_actions_tables", "unique_id"), ["agent_actions_localised_action_name_"]),
 "captive_record_key": ("reference.captive_options.record_key", ref_col("SELECT record_key FROM captive_options"), "campaign_post_battle_captive_options_tables.record_key", pack_col("campaign_post_battle_captive_options_tables", "record_key"), ["campaign_post_battle_captive_options_onscreen_name_"]),
 "armory_item_variant": ("(none)", set(), "armory_item_variants_tables", None, ["armory_item_variant_ui_infos_localised_name_"]),
 "mission_issuer": ("(none)", set(), "mission_issuers_tables", None, ["mission_issuers_on_screen_name_"]),
 "unit_category": ("reference.units.category", ref_col("SELECT DISTINCT category FROM units"), "land_units_tables.category", pack_col("land_units_tables", "category"), []),
 "streak_effect": ("(none)", set(), "campaign_streak_effects_tables", None, []),
 "teleport_node": ("(none)", set(), "teleportation_network_nodes_tables", None, []),
}
BYTE_TABLE = {"edict": "provincial_initiative_records_tables", "effect_bundle": "effect_bundles_tables",
              "pooled_resource": "pooled_resources_tables", "stance": "campaign_stances_tables",
              "armory_item_variant": "armory_item_variants_tables", "mission_issuer": "mission_issuers_tables",
              "streak_effect": "campaign_streak_effects_tables", "teleport_node": "teleportation_network_nodes_tables",
              "agent_subtype": "agent_subtypes_tables"}
for gname, paths, norm in GROUPS:
    base = gname.split(" (")[0]; name_a, set_a, name_b, set_b, locpre = TARGETS[base]
    cnt = collections.Counter()
    for p in paths:
        for v, n in (VALS.get(p) or {}).items():
            k = norm(v)
            if k not in SKIP: cnt[k] += n
    tot = sum(cnt.values()); nd = len(cnt)
    ra = {k for k in cnt if k in set_a}
    rb = {k for k in cnt if k in set_b} if set_b is not None else {k for k in cnt if bytes_present(BYTE_TABLE[base], k)}
    pct = lambda s: "%.1f%% dist / %.1f%% occ" % (100.0 * len(s) / nd, 100.0 * sum(cnt[k] for k in s) / tot)
    print(gname, nd, tot, name_a, pct(ra), name_b, pct(rb),
          sorted(((k, cnt[k]) for k in cnt if k not in rb), key=lambda x: -x[1])[:12])
    for pre in locpre:
        lk = loc_keys(pre)
        if base == "building": hit = {k for k in rb if any(x.startswith(k) for x in lk)}
        elif base == "trait": hit = {k for k in rb if any(x.startswith(k + "_") or x == k for x in lk)}
        else: hit = {k for k in rb if k in lk}
        print("  loc", pre, len(hit), "/", len(rb), "pack:", sum(1 for k in rb if (pre + k) in PACK_LOC))
```

### G.5 follow-ups (inline, one-off)

```python
# tech node->technology mapping, unit->land_unit loc mapping, subtype byte test, loc-prefix probes
nodes = B.decode_db_table(files, d, "technology_nodes_tables", schema)[0]
node2tech = {r["key"]: r["technology_key"] for r in nodes}
loc_t = loc_keys("technologies_onscreen_name_")
print(sum(1 for k in corpus_nodes if node2tech.get(k, k) in loc_t), len(corpus_nodes))
u2l = {r["unit"]: r["land_unit"] for r in B.decode_db_table(files, d, "main_units_tables", schema)[0]}
loc_u = loc_keys("land_units_onscreen_name_")
print(sum(1 for k in corpus_units if u2l.get(k) in loc_u), sum(1 for k in corpus_units if u2l.get(k) != k))
# occupation ids vs loc numeric suffix
ids = {k.rsplit("_", 1)[1] for k, in con.execute("SELECT key FROM loc WHERE key LIKE 'culture_settlement_occupation_options_%'")}
print(sum(1 for k in occupation_ids if k in ids), len(occupation_ids))
# cqi scope (section D): per sampled decision, map world.armies[].cqi -> (subtype, faction, is_leader); count
# (faction,map)->faction_cqi conflicts, (faction,map)->leader cqi sets, cqi values shared across campaigns
# and how many of those denote >1 (subtype,faction); count (campaign,subtype) pairs with >1 cqi.
# byte volume (section C): SELECT sum(bc.n), sum(bw.n) FROM decisions d LEFT JOIN blobs bc ... WHERE decision_id % 100 = 7;
#   SELECT sum(b.n) FROM entities e JOIN blobs b ON b.blob_id=e.features_blob WHERE e.decision_id % 100 = 7;
#   SELECT sum(length(params)) FROM actions WHERE action_type <> 'move' OR action_id % 100 = 0;
#   SELECT kind, sum(length(options_json)), sum(b.n) FROM interrupts i LEFT JOIN blobs b ON b.blob_id=i.panel_blob GROUP BY 1;
#   SELECT sum(length(payload)) FROM diplomacy_events WHERE event_id % 4 = 0;
# truncated faction key: SELECT count(*), count(distinct campaign_key) FROM diplomacy_events WHERE payload LIKE '%wh_main_grn_skull"%';
```
