# 6. Sizing

Method: row bytes = 24 B tuple header + null bitmap (present when any column is NULL: ⌈ncols/8⌉ B, then MAXALIGN to 8) + column widths in declaration order with alignment (int2 2, int4/real 4, int8/float8 8, bool 1, text = 1+len for len < 127, array = 16 + elements + 4 per dimension header rounded) → MAXALIGN 8. Index entry ≈ key bytes + 8 (tuple header) + 4 (line pointer), ×1.1 for B-tree fill (90% leaf fill for right-growing keys). Cardinalities are the measured corpus (M1 §0, M2 §0, R6 B) and the change-rate projection (change_rate.out corpus-scale, sample × 12.09, an upper bound per M1 G flaw 2).

## 6.1 Cardinalities after migration

| table | rows | derivation |
|---|---|---|
| snapshot | 250,299 | 206,907 decisions + 43,392 interrupts |
| decision / decision_timing | 206,907 / 205,157 | 35/4,138 decisions lack timings (M2 A.1) → 206,907 × (1 − 0.0085) |
| interrupt | 43,392 | |
| interrupt_option | 98,066 | 2.26 options/interrupt (M2 C) |
| interrupt_battle_panel / diplo_panel | 33,214 / 1,294 | pre_battle 20,200 + battle_results 13,014 with panel (R4 B.1); proposal 714 + war_declared 579 + notice 1 |
| snapshot_campaign / snapshot_world | 250,299 each | |
| snapshot_read_failure | ≈ 0 | `read_failures` `{}` in 2,070/2,070 (M1 A.1) |
| world_army | 1,172,000 | 4.7 × 206,907 + 4.6 × 43,386 |
| world_hostile | 2,036,000 | 8.1 × 206,907 + 8.3 × 43,386 |
| campaign | 4,827 | |
| character | 15,463 | 8,551 lord + 6,912 hero distinct (campaign, cqi) (M1 D) |
| snapshot_entity | 1,130,208 | |
| char_state | 554,855 | lord 314,467 + hero 240,388 |
| char_state_ext | 112,000 | 20.6% × 314,467 + 19.8% × 240,388 (M1 C v31 share) |
| province_state | 368,446 | |
| campaign_state | 206,907 | |
| state_set | ≈ 600,000 | Σ (dedup member rows / mean len) over kinds in the sample ≈ 50k sets × 12.09 |
| set members (all 22 tables) | ≈ 3,500,000 | 13.6M projected dedup rows − move_tiles (4.24M + 3.22M) − reach arrays (1.04M + 0.75M) − armies/hostiles (0.92M, stored as rows) ≈ 3.43M; per kind: skill 635k, tech 451k, unit_card 333k, rite 275k, relation 270k, lord_pool 233k + candidate 241k, region 190k, buildable 166k, resource 145k, slot_state 107k, horde 96k, stance 91k, item_slot 61k, war_graph 51k, settlement 29k, trait_progress 24k, ruin 20k, merc 18k, built 16k, stationed 16k, building_now 13k, enemy_agent 13k, mission 12k, effect_bundle 12k, hidden_skill_state 9k, pending_queue 5k, hero_count 1k, trait < 1k |
| offer | 24,832,005 | |
| taken | 205,533 | |
| dict.action | ≈ 700,000 | 626k move `xy:x,y` keys (M2 B: 284,716 + 341,521) + ≈ 80k non-move (type, key) pairs |
| diplomacy_event / postmortem / ucb_pick / ucb_pick_row | 74,145 / 5,020 / 3,853 / 497,037 | |
| analytics.model_agreement / acquisition / item_event | 620,712 / 255,267 / 19,250 | |
| ref.* / ref.loc | 865,404 / 241,972 | decode_probe, R5 F |

## 6.2 Bytes per table (heap + indexes)

| table | row B (arithmetic) | heap MB | indexes MB | total MB |
|---|---|---|---|---|
| offer | 24 + 8 (bitmap, 13 cols) + 8+2+2+4+2+4+4+2+4+4+2+4+2 = 76 → 80 | 24.83M × 80 = 1,987 | PK (8+2 → 10+12) × 1.1 × 24.83M = 601 | **2,588** |
| taken | 24 + 8 + 8+4+2+2+4+2+8+3+2+2+4 + 7×4 + 2+1+2 + doomed 6 + stderr 2,090 (M2 C: 2.09 KB/row; 36% of rows TOASTed, pglz ≈ 3× on stderr) ≈ 2,200 raw → ≈ 1,000 stored | 205 | PK 5 + campaign INCLUDE 8 + ts 5 + action 6 = 24 | **229** |
| char_state | 24 + 8 + scalars (8+4+2+1+2+2+2+2+8+4+4+8+2+2+2+2+4+4+6×1+2+9×8) = 162 + arrays: hidden_skill_ids 16+2.8×2 = 22, pending_recruit 17, reach_chars_true (½ of 10 keys true, ASSUMPTION) 16+5×4 = 36, reach_setts_true 26, move_x/move_y 2 × (16+13.6×4) = 142, reach_rays 32, reach_max 2 = 277 → 471 → 472 | 554,855 × 472 = 262 | PK 13 + (snapshot_id, entity_seq) 12 + character idx 13 = 38 | **300** |
| world_hostile | 24 + 8 + 8+2+2+2+1+4+2+2+2+2+4+4+4+1+2+8+2 = 52 → 88 | 2.036M × 88 = 179 | PK 2.036M × 22 = 49 | **228** |
| world_army | 24 + 8 + 66 → 104 | 1.172M × 104 = 122 | PK 28 | **150** |
| snapshot_entity | 24 + 8 + 8+2+2+4+2 = 18 → 56 | 1.13M × 56 = 63 | PK 27 + 2 partial (character 0.55M, region 0.37M) × 22 B = 20 | **110** |
| set members | weighted mean 50 B (skill 48, tech 48, unit_card 48, rite 48, relation 56, region 80, lord_pool 40 + candidate 56, buildable 56, resource 40, slot_state 64) | 3.5M × 50 = 175 | PK 3.5M × 22 = 77; second UNIQUE on ≈ 60% of rows 46 | **298** |
| state_set | 24 + 8+2+2+33 = 69 → 72 | 0.6M × 72 = 43 | (kind, hash) 0.6M × 47 = 28; PK 13 | **84** |
| province_state | 24 + 8 + 116 scalars/ids + locked_slots 23 + edict_ids 24 = 171 → 176 | 368,446 × 176 = 65 | PK 9 + entity 8 + region 8 = 25 | **90** |
| snapshot_world | 24 + 8 + 8×8 + citizenry 23 + diplo_unseen 16 + 4 + reach_char_cqis 56 + reach_region_ids 36 = 231 → 232 | 250,299 × 232 = 58 | PK 6 | **64** |
| snapshot_campaign | 24 + 8 + 2+2+2+4+2+4+1+2+2+2+2+2+1+2+1+2+ leader 26 +2+2+2+2+24 = 93 + 8 (snapshot_id) → 120 | 250,299 × 120 = 30 | PK 6 + INCLUDE idx 12 | **48** |
| snapshot | 24 + 4+2+8+2+2+8 = 50 → 56 | 14 | PK 6 + campaign_ts 8 + campaign_turn 8 + ts 6 = 28 | **42** |
| decision + decision_timing | 56 / 112 | 12 + 23 | 5 + 7 (uuid) + 5 | **52** |
| campaign_state | 24 + 8 + 2 + 5×8 + 8 + 2 + 2 = 86 → 88 | 18 | 5 | **23** |
| char_state_ext | 24 + 8 + 4+4+2+2+2+2+4+2+8+8+8 + armory 16 = 94 → 96 | 11 | 3 | **14** |
| interrupt + options + panels | 190 / 110 / 100 | 8 + 11 + 3 | 3 + 5 + 1 | **31** |
| dict.action + other dicts | 48 | 34 + 3 | 20 + 20 (key) + 2 | **79** |
| character, campaign | 56 / 176 | 1 + 1 | 1 + 1 | **4** |
| diplomacy_event, postmortem(+metrics), ucb_pick(+rows) | 120 / 200 / 72 | 9 + 1 + 36 | 4 + 1 + 12 | **63** |
| analytics (model_agreement, rollups, acquisition, item_event) | 100 / 80 / 60 | 62 + 20 + 1 | 20 + 15 + 1 | **119** |
| ref.* + loc + meta | 865k × (108 raw + 24) = 114; loc 242k × 200 = 48 | 162 | PK 40 + FK support 30 + loc 25 = 95 | **257** |
| rpc, ops.trial*, ops.bus_call_stat, migrate.* (transient) | | 5 + 1 + 120 + 10 | 1 + 1 + 40 + 2 | **180** |
| **total** | | **≈ 3,730** | **≈ 1,330** | **≈ 5,060 MB** |

Today: 16,327 MB whole database (R6 §0), of which public 12,000, app 1,690, analytics 1,497, bus 168, reference 113, capture 93 (R6 B). The 12.8 GB of logical JSON (M1 E) become ≈ 1.6 GB of typed rows (everything above except offer/taken/ref/analytics/ops); offers+scores+actions 8.8 GB become 2.6 GB. Indexes fall from 4,749 MB (R6 C) to ≈ 1,330 MB. Cross-check of the set estimate: if the sample-size over-estimate (M1 G flaw 2, +15-33% on high-cardinality collections) is corrected, set tables shrink to ≈ 250 MB; the figure above is the upper bound.

Rows per decision: 1 snapshot + 1 snapshot_campaign + 1 snapshot_world + 4.7 armies + 8.1 hostiles + 5.5 entities + 2.8 char_state + 0.6 ext + 1.9 province + 1 campaign_state + 1 decision + 1 timing + 1 taken + 120 offers + ≈ 17 new set members (3.5M / 206,907) ≈ **168 rows** (today ≈ 405, M2 D). Bytes per decision on disk: 2,588+229+300+228+150+110+298+84+90+64+48+42+52+23+14 MB / 206,907 ≈ **22 KB** (today ≈ 64 KB in `public` + 42 KB `game_event`, M2 D).

## 6.3 Growth per day at measured ingest

Ingest: 15,152 decisions/day (last 7 days, R6 E; 13,239 14-day mean), 3,093 interrupts/day, 1.87M offers/day, 266 campaigns/day.

| table | per day | derivation |
|---|---|---|
| offer | 202 MB | 1.87M × 108 B (80 heap + 28 index) |
| taken | 25 MB | 15,152 × (1,000 + 120) B... ≈ 15,152 × 1.1 KB = 17 MB heap + 8 idx |
| char_state (+ext, entity) | 24 MB | 15,152 × (2.68 lord/hero × 510 B + 5.5 × 78 B) |
| world_army + world_hostile | 25 MB | 18,245 snapshots × (4.7 × 128 + 8.1 × 110) |
| set members + state_set | 27 MB | 3.5M rows / 206,907 decisions × 15,152 × 85 B... = 17 new members/decision × 15,152 × 100 B (incl. index) + sets |
| province_state, campaign_state, snapshot*, decision*, interrupt* | 20 MB | 15,152 × 1.3 KB |
| dict.action (move keys) | 3 MB | ≈ 40k new coordinates/day (M2 B: 626k over 16 data days) × 68 B |
| analytics + side tables | 10 MB | model_agreement 45k × 120 B + acquisitions + diplomacy 5.7k × 160 |
| **total** | **≈ 336 MB/day** | today 918 MB/day (last 7, R6 E) + 1,165 MB/day when `game_event` runs |

WAL: `wal_compression = zstd` and `max_wal_size = 8 GB` (10 §10.2) end the 38% forced checkpoints and the 132 GB/4.8 days WAL volume (R6 A); WAL is not counted in the table sizes.

## 6.4 Projection

| horizon | corpus (5.06 GB + 336 MB/day) | with 30% headroom for autovacuum/bloat + WAL (8 GB) |
|---|---|---|
| after migration | 5.1 GB | 15 GB |
| +6 months (182 d) | 66 GB | 94 GB |
| +12 months (365 d) | 128 GB | 174 GB |

D: has 1,771 GB free (R6 §0). At 336 MB/day the disk lasts > 13 years; the training window (1000 campaigns ≈ 3.5 days of ingest) and every API query are bounded by indexes, not by corpus size, except the whole-corpus scans listed in 05 (A8 positions ≈ 0.3 s at 207k decisions → ≈ 4 s at 12 months; the API's warm loop rebuilds it every 120 s in a thread, so it never sits on a request path). Retention decision: none — the corpus is kept whole (03c §3.15); a `snapshot_id`-range archival (COPY out + DELETE by range) is trivially possible because every corpus table is keyed by `snapshot_id`, and is scheduled for consideration at 100 GB (≈ 9 months).

## 6.5 Migration disk headroom (10)

Peak during migration on D:: old database dump/restore copy (16.3 GB) + new tables (5.1 GB) + indexes built after load (1.3 GB) + WAL during COPY (bounded by `max_wal_size` 8 GB; no `wal_level = minimal` toggle, m5) ≈ **46 GB**, against 1,771 GB free. C: is not written at all after the base copy (its cluster keeps running until cutover).
