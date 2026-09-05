# R6 — Server configuration, statistics, growth, other schemas

Session: 2026-09-04 ~23:00 PDT. All SQL run as role `tw`, `readonly=True`, `statement_timeout=600s`, on db `tw_stack` only. Times from `pg_stat_*` are PDT (server `TimeZone=America/Los_Angeles`); growth tables use UTC dates as stated.

## 0. Re-verification of the earlier infra map (Topic B) — corrections

| item | earlier map | verified now | source |
|---|---|---|---|
| service | postgresql-x64-17, Running/Auto, NetworkService, `pg_ctl.exe runservice -D "C:\Program Files\PostgreSQL\17\data"` | same | `Get-CimInstance Win32_Service -Filter "Name like 'postgres%'"` |
| version | 17.10 msvc-19.44.35227 | same | `SELECT version()` |
| drives | C 44.6 GB free / D 1771.2 GB free | C: 3744.8 used / **44.5 free**; D: 2044.2 used / 1771.2 free | `Get-PSDrive` |
| disks | not stated | two `NVMe PC SN820 NVMe WD 4096GB` SSDs (BusType NVMe); C = disk 0 (3789 GB partition), D = disk 1 (3815 GB) | `Get-PhysicalDisk`, `Get-Partition` |
| RAM / CPU | not stated | **63.5 GB RAM** (34.1 GB free), Intel Core Ultra 9 285K, 24 cores / 24 logical | `Win32_ComputerSystem`, `Win32_Processor`, `Win32_OperatingSystem` |
| data dir | 5,872 files / 28.22 GB | 5,873 files / 28.22 GB; `base` 27.26 GB, `pg_wal` 0.95 GB, `log` 0.01 GB | `Get-ChildItem -Recurse` on data dir (readable) |
| `game` schema | 99 tables, all empty | **56 tables**, 1040 kB total | pg_class count by nspname |
| `public` tables | 17 | 18 (+10 views) | same |
| `app` tables | 8 | 9 (`game_event, game_event_state, starts, experiments, segments, campaign_tags, boards, views, settings`) | same |
| backends at rest | 6 | 1 client backend (this session) + 5 background (`pg_stat_activity`) | see A |
| stats epoch | not stated | **all cluster counters date from 2026-08-31 04:58:46 PDT** (crash recovery, see A) and **every `tw_stack` table was bulk-loaded on 2026-09-04 16:40–17:10 PDT** (see B) — usage counters cover ~6 h only | pg_stat_bgwriter.stats_reset; pg_stat_user_tables |

Databases (`SELECT datname, pg_database_size(datname) FROM pg_database`): tw_stack 16,327,202,483 B (15 GB), tw_stack_post_refactor 12,444,399,283 (12 GB), bench 463,525,555, optuna 10,737,331, postgres/template0/template1 ~8 MB each. Collation `English_United States.1252` everywhere. No tablespaces beyond `pg_default/pg_global` (`SELECT spcname, pg_tablespace_location(oid) FROM pg_tablespace` → both ''). Extensions: `plpgsql 1.0` only. `pg_replication_slots`=0, `pg_prepared_xacts`=0.

## A. Server configuration and cluster statistics

`SELECT name, setting, unit, source FROM pg_settings WHERE name = ANY(...)` — every value below is stock default unless `source` says otherwise. Only 19 settings are non-default in the whole cluster (`WHERE source NOT IN ('default','override','session','client')`): DateStyle, default_text_search_config, dynamic_shared_memory_type=windows, lc_*, listen_addresses=localhost, log_destination=stderr, log_file_mode, log_line_prefix='%t ', log_timezone, logging_collector=on, max_connections=100, max_wal_size=1024MB, min_wal_size=80MB, port=55432, shared_buffers=16384×8kB, TimeZone — all `configuration file`, none via ALTER SYSTEM (no `postgresql.auto.conf` source rows). `sourcefile`/`sourceline` are NULL for `tw` (needs pg_read_all_settings); `SHOW data_directory` is denied to `tw`.

| setting | value | source | note vs 63.5 GB / 24-core / NVMe box |
|---|---|---|---|
| shared_buffers | 16384 ×8kB = **128 MB** | configuration file | 0.2 % of RAM |
| effective_cache_size | 524288 ×8kB = 4 GB | default | box has 34 GB free |
| work_mem | 4096 kB | default | |
| maintenance_work_mem | 65536 kB | default | |
| wal_level | replica | default | |
| max_wal_size / min_wal_size | 1024 MB / 80 MB | configuration file | 72 "checkpoints are occurring too frequently" warnings in logs 09-01..09-04 (see H) |
| checkpoint_timeout / completion_target | 300 s / 0.9 | default | |
| wal_compression | off | default | |
| synchronous_commit | on | default | recorder and bus_stats set it `off` per session (`decisions/store.py:75`, `bus/bus_stats.py:171`) |
| fsync / full_page_writes | on / on | default | |
| autovacuum | on, naptime 60 s, vacuum_scale 0.2, threshold 50, analyze_scale 0.1, max_workers 3, cost_limit -1 (=200), cost_delay 2 ms, autovacuum_work_mem -1 | default | |
| max_connections | 100 | configuration file | |
| random_page_cost / seq_page_cost | 4 / 1 | default | NVMe |
| effective_io_concurrency | 0 | default | |
| jit | on | default | |
| max_parallel_workers_per_gather / max_parallel_workers / max_worker_processes | 2 / 8 / 8 | default | |
| log_min_duration_statement | -1 | default | log_autovacuum_min_duration 600000 ms |
| log_checkpoints | on | default | |
| track_io_timing | off | default | blk_read_time/blk_write_time are 0 |
| data_checksums | off | default | |
| TimeZone / log_timezone | America/Los_Angeles | configuration file | |
| default_toast_compression | pglz | default | |
| huge_pages / huge_pages_status | try / off | default | |
| wal_buffers | 512 ×8kB = 4 MB | default | |
| default_statistics_target | 100 | default | |
| listen_addresses / port | localhost / 55432 | configuration file | |

**pg_stat_database** (`SELECT datname, blks_hit, blks_read, xact_commit, xact_rollback, deadlocks, temp_files, temp_bytes, tup_inserted, tup_updated, tup_deleted, stats_reset FROM pg_stat_database`), counters since 2026-08-31 04:58 PDT:

| db | blks_hit | blks_read | hit % | xact_commit | xact_rollback | deadlocks | temp_files | temp_bytes | tup_ins | tup_upd | tup_del | stats_reset |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tw_stack | 74,607,255 | 37,291,512 | **66.67** | 35,216 | 33 | 0 | 513 | 18,304,187,575 (17 GB) | 95,120,399 | 1,127 | 2 | NULL |
| tw_stack_post_refactor | 15,936,341,008 | 6,369,087,849 | 71.45 | 16,532,192 | 9,196 | 3 | 287,572 | 994,507,172,017 (926 GB) | 132,107,749 | 81,833,683 | 10,848,467 | NULL |
| optuna | 13,775,864 | 506,990 | 96.45 | 65,715 | 16,839 | 0 | 0 | 0 | 6,324 | 816 | 0 | NULL |
| bench | 12,571 | 999 | 92.64 | 232 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | NULL |

Reading: `tw_stack`'s 95.1 M `tup_inserted` equals the sum of `n_live_tup` over its tables (B) with only 35 K commits — a bulk load, not recording traffic. The 12 GB `tw_stack_post_refactor` carried **16.5 M commits, 81.8 M updates, 10.8 M deletes and 926 GB of temp files** in the same window — it was the *active* database for most of 08-31..09-04, not a dormant leftover (names/sizes only per rules; who wrote it: UNKNOWN). `tw_stack` hit ratio 66.7 % with shared_buffers=128 MB.

**pg_stat_bgwriter / pg_stat_checkpointer / pg_stat_wal** (`SELECT * FROM ...`, stats_reset 2026-08-31 04:58:46): bgwriter buffers_clean 12,417,310, maxwritten_clean 67,252, buffers_alloc 2,573,586,216. checkpointer num_timed **1,291**, num_requested **797** (38 % forced by WAL volume under max_wal_size=1 GB), buffers_written 461,924, write_time 35,328 s, sync_time 400 s. WAL: wal_records 881,906,621, wal_fpi 7,431,630, **wal_bytes 141,590,831,672 (132 GB in 4.8 days)**, wal_buffers_full 4,870,006, wal_write 5,834,374. Current LSN `33/1BEB8B48`.

Why the reset: `C:\Program Files\PostgreSQL\17\data\log\postgresql-2026-08-31_000000.log`: `04:58:45 database system was interrupted; last known up at 04:57:09 PDT` / `database system was not properly shut down; automatic recovery in progress`. Same pattern on 08-30 16:34:24 and 08-30 22:27:42 — **three unclean shutdowns in 13 h**; no "received fast shutdown" lines in any of the 20 log files.

**pg_stat_activity** (`SELECT application_name, state, backend_type, datname, usename, count(*), min(backend_start) ... GROUP BY 1..5`): 1 `client backend` (this session, app name '', tw_stack, tw) + 5 background rows (rows of other users are NULL-masked for `tw`; `SELECT backend_type, count(*)` → NULL:5, client backend:1). Oldest backend = this session (backend_start 22:59:42). No recorder, API, analytics runner or session process is connected. Postmaster start 2026-08-31 04:58:45 PDT, conf load same.

**pg_stat_io** (`SELECT backend_type, object, context, reads, writes, extends, hits, evictions FROM pg_stat_io WHERE reads>0 OR writes>0 OR hits>0 ORDER BY reads DESC`): client backend/normal reads 2,067,951,065, hits 10,961,648,380, **evictions 2,072,236,973**; background worker/bulkread reads 2,144,589,692; client backend/bulkread reads 1,645,992,664; autovacuum worker/vacuum reads 36,161,942 writes 5,077,285. Every read was an eviction — the 128 MB buffer pool is thrashing.

**Role** (`SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication FROM pg_roles`): `tw` = super **f**, createdb **t**, createrole f, replication f, bypassrls f; `postgres` = all t. `tw` cannot create tablespaces (needs superuser). `age(datfrozenxid)` = 3,853,751 for every db; max `age(relfrozenxid)` in public = 720 (fresh load).

## B. Table statistics (schemas public, app, analytics, reference, bus, capture, metrics)

Query: `SELECT s.schemaname||'.'||s.relname, n_live_tup, n_dead_tup, last_vacuum, last_autovacuum, last_analyze, last_autoanalyze, vacuum_count, autovacuum_count, seq_scan, seq_tup_read, idx_scan, n_tup_ins, n_tup_upd, n_tup_hot_upd, n_tup_del, pg_relation_size(c.oid), pg_relation_size(c.reltoastrelid)+pg_indexes_size(c.reltoastrelid), pg_indexes_size(c.oid), pg_total_relation_size(c.oid) FROM pg_stat_user_tables s JOIN pg_class c ON c.oid=s.relid WHERE schemaname IN (...) ORDER BY total DESC` (105 tables).

Uniform facts across **all 105 tables**: `n_dead_tup=0`, `n_tup_upd=0`, `n_tup_hot_upd=0`, `n_tup_del=0` (2 on `tw_stack`-wide), `n_tup_ins = n_live_tup`, `vacuum_count=0`, `last_vacuum/last_analyze` NULL, `autovacuum_count=1`, `last_autovacuum/last_autoanalyze` between **09-04 16:40 and 17:10 PDT** — the whole database was (re)loaded in that window (server log 16:39–16:41: checkpoints every 3–5 s, `skipping vacuum of "offers" --- lock not available`; 16:42 `terminating autovacuum process due to administrator command`). `reloptions` NULL everywhere.

Top 40 by total size (MB = 1,048,576 B; heap = main fork, toast = toast heap+toast index, idx = table indexes):

| table | live rows | seq_scan | seq_tup_read | idx_scan | heap MB | toast MB | idx MB | total MB | B/row |
|---|---|---|---|---|---|---|---|---|---|
| public.offer_scores | 22,364,955 | 35 | 268,347,252 | 227,695 | 1975.1 | 0 | 672.7 | 2648.4 | 124 |
| public.blobs | 949,090 | 33 | 15,289,730 | 2,669,452 | 674.0 | 1606.6 | 107.1 | 2388.4 | 2633 |
| public.offer_model_scores | 22,147,127 | 23 | 177,161,984 | 1,002 | 1442.1 | 0 | 856.5 | 2299.0 | 109 |
| public.offers | 24,832,005 | 90 | 794,570,944 | 217,353 | 1235.9 | 0 | 993.3 | 2229.5 | 94 |
| public.actions | 5,108,337 | 166 | 385,606,308 | 4,283,142 | 769.2 | 0 | 850.4 | 1619.8 | 332 |
| app.game_event | 5,003,759 | 87 | 157,697,041 | 671 | 972.1 | 0 | 561.8 | 1534.2 | 321 |
| analytics.game_turn_skill | 2,849,704 | 4 | 5,700,006 | 0 | 363.5 | 0 | 41.0 | 404.6 | 149 |
| public.taken | 205,533 | 151 | 12,337,787 | 148,224 | 273.6 | 48.1 | 27.5 | 349.4 | 1783 |
| analytics.model_agreement | 620,712 | 28 | 6,827,832 | 473 | 132.6 | 0 | 111.3 | 243.9 | 412 |
| bus.call_stats | 206,732 | 48 | 3,927,908 | 0 | 85.1 | 0 | 82.7 | 167.8 | 851 |
| analytics.decision_resources | 1,126,461 | 4 | 2,252,922 | 0 | 83.6 | 0 | 72.6 | 156.2 | 145 |
| analytics.game_turn_research | 1,575,819 | 4 | 3,151,638 | 0 | 133.8 | 0 | 22.0 | 155.8 | 104 |
| app.starts | 951,908 | 26 | 13,326,712 | 8 | 70.6 | 0 | 83.6 | 154.3 | 170 |
| public.decisions | 206,907 | 216 | 22,976,551 | 976,922 | 94.7 | 0 | 41.5 | 136.3 | 691 |
| public.entities | 1,130,208 | 164 | 75,102,294 | 462,260 | 83.6 | 0 | 34.0 | 117.7 | 109 |
| analytics.game_turn_region | 401,781 | 4 | 803,562 | 0 | 70.1 | 0 | 31.0 | 101.2 | 264 |
| capture.snapshots | 144,391 | 46 | 2,245,408 | — (no index) | 91.6 | 0 | 0 | 91.6 | 665 |
| public.ucb_pick_rows | 497,037 | 72 | 12,425,925 | 3,863 | 72.1 | 0 | 15.0 | 87.2 | 184 |
| analytics.game_turn_recruit | 667,765 | 2 | 667,765 | 0 | 81.5 | 0 | 5.5 | 87.0 | 137 |
| analytics.acquisitions | 255,267 | 9 | 1,021,068 | 37,680 | 34.8 | 0 | 26.9 | 61.7 | 253 |
| reference.loc | 241,972 | 250 | 27,783,291 | 4,702 | 38.2 | 0.1 | 23.1 | 61.4 | 266 |
| analytics.game_turn_diplomacy | 256,066 | 4 | 512,132 | 0 | 25.3 | 0 | 17.5 | 42.8 | 175 |
| public.diplomacy_events | 74,145 | 54 | 2,826,506 | 21 | 30.6 | 0 | 7.4 | 38.0 | 537 |
| analytics.decision_features | 206,905 | 27 | 2,069,050 | 10 | 28.1 | 0 | 8.1 | 36.2 | 183 |
| analytics.game_turn_building | 273,910 | 4 | 547,820 | 0 | 26.8 | 0 | 6.8 | 33.7 | 129 |
| analytics.game_turn_army | 165,917 | 2 | 165,917 | 0 | 30.8 | 0 | 2.0 | 32.8 | 207 |
| analytics.game_turn_unit | 250,183 | 2 | 250,183 | 0 | 25.9 | 0 | 2.9 | 28.8 | 121 |
| analytics.decision_heroes | 231,840 | 4 | 463,680 | 0 | 13.4 | 0 | 10.1 | 23.5 | 106 |
| public.interrupts | 43,392 | 207 | 7,940,736 | 13,599 | 14.5 | 1.8 | 2.3 | 18.6 | 450 |
| analytics.game_turn_character | 68,520 | 4 | 137,040 | 0 | 14.3 | 0 | 3.6 | 17.9 | 274 |
| public.postmortems | 5,020 | **5,019** | 13,851,011 | 23 | 1.6 | 13.5 | 0.1 | 15.3 | 3196 |
| analytics.game_turn_war | 137,701 | 2 | 137,701 | 0 | 12.8 | 0 | 1.7 | 14.5 | 110 |
| analytics.game_turn_resource | 155,448 | 2 | 155,448 | 0 | 12.1 | 0 | 1.9 | 14.1 | 95 |
| analytics.game_turn | 27,772 | 6 | 83,316 | 3 | 8.8 | 0 | 3.2 | 12.0 | 453 |
| analytics.game_turn_item | 99,178 | 4 | 198,356 | 0 | 9.3 | 0 | 2.1 | 11.5 | 122 |
| reference.ref_skill_effect | 33,027 | 2 | 66,054 | 0 | 5.8 | 0 | 2.1 | 7.9 | 251 |
| reference.ref_skill_level | 29,367 | 2 | 58,734 | 0 | 3.3 | 0 | 2.9 | 6.3 | 225 |
| public.rpc_requests | 1,239 | 37 | 45,843 | 36 | 1.0 | 4.5 | 0.1 | 5.7 | 4823 |
| reference.skill_links | 23,361 | 7 | 163,527 | 5 | 4.2 | 0 | 0.3 | 4.6 | 206 |
| analytics.item_events | 19,250 | 8 | 154,000 | 1,435 | 2.8 | 0 | 1.4 | 4.2 | 229 |

Remaining 65 tables are each < 4 MB (reference.* catalogue tables 0.0–4.0 MB; app.experiments 1 row, app.segments 2, app.boards 1, app.views 2, app.settings 4, app.campaign_tags 4,821, app.game_event_state 409, metrics.trials 152, metrics.trials_archive 149, capture.latest 1,385, analytics.game_turn_slot 0 rows, public.meta 1, public.collector_versions 32). Schema totals (`sum(pg_total_relation_size)` by nspname): public 12 GB, app 1,690 MB, analytics 1,497 MB (1,569,193,984 B), bus 168 MB, reference 113 MB, capture 93 MB, metrics 888 kB; leftovers test_fixture 37 MB (18 tables), test_fixture_analytics 4.4 MB (16), game 1,040 kB (56).

Notable: `public.postmortems` seq_scan 5,019 ≈ its row count (a per-row full scan pattern, `seq_tup_read` 13.85 M); `capture.snapshots` has no index at all; `app.starts` is 951,908 rows for **326 distinct starts** (see F); `bus.call_stats` idx_scan 0.

## C. Index statistics

Query: `SELECT schemaname, relname, indexrelname, pg_get_indexdef(indexrelid), pg_relation_size(indexrelid), idx_scan, idx_tup_read, indisprimary, indisunique, indkey FROM pg_stat_user_indexes JOIN pg_index USING(indexrelid) WHERE schemaname IN (...)`. 159 indexes, **4,748.7 MB** total (29 % of the DB). Caveat: `idx_scan` counts only ~6 h since the 09-04 reload, with no recorder/API/analytics process connected — "unused" is low-confidence.

Largest 20:

| index | columns | MB | idx_scan | idx_tup_read | flags |
|---|---|---|---|---|---|
| offer_model_scores_pkey | (decision_id, offer_seq, model) | 856.5 | 1,002 | 23,523,390 | PK |
| offers_pkey | (decision_id, offer_seq) | 746.9 | 217,241 | 51,713,589 | PK |
| actions_context_kind_context_id_action_type_action_key_para_key | (context_kind, context_id, action_type, action_key, params) | **741.0** | 8 | 140,981 | UNIQUE; 96 % of the 769 MB heap |
| offer_scores_pkey | (decision_id, offer_seq) | 672.7 | 227,695 | 23,489,581 | PK |
| app.game_event.ix_game_event_campaign | (campaign_key, turn, event_id) | 421.0 | 139 | 17,103,666 | |
| offers.ix_offers_action | (action_id) | 246.4 | 112 | 24,836,360 | |
| actions_pkey | (action_id) | 109.5 | 4,283,134 | 35,509,932 | PK |
| game_event_pkey | (event_id) | 107.0 | 30 | 6,810,483 | PK |
| blobs_sha_key | (sha) | 86.7 | 3 | 3 | UNIQUE |
| bus.call_stats_pkey | (channel, key) | 82.7 | 0 | 0 | PK |
| analytics.decision_resources_pkey | (decision_id, key) | 64.7 | 0 | 0 | PK |
| app.starts_campaign_map_faction_leader_difficulty_key | (campaign_map, faction, leader, difficulty) | 63.2 | 0 | 0 | UNIQUE, ineffective (NULL difficulty, F) |
| analytics.ix_ma_pair_rho | (pair, status, rho) | 36.3 | 0 | 0 | **UNUSED**, same lead as 3 siblings |
| entities_pkey | (decision_id, entity_seq) | 34.0 | 462,260 | 5,223,221 | PK |
| app.ix_game_event_kind | (event) | 33.8 | 502 | 37,873,573 | |
| analytics.ix_ma_pair_ts | (pair, ts) | 33.1 | 459 | 625,737 | |
| model_agreement_pkey | (decision_id, pair) | 33.0 | 5 | 15 | PK |
| analytics.game_turn_region_pkey | (campaign_id, turn, region) | 27.5 | 0 | 0 | PK; `ix_gtreg_ct (campaign_id, turn)` 3.5 MB is a strict prefix |
| reference.loc_pkey | (key) | 23.1 | 4,702 | 4,618 | PK |
| analytics.acquisitions_pkey | (campaign_id, family, key, ctx) | 22.4 | 33,038 | 185,287 | PK; `ix_acq_camp (campaign_id, family)` 2.5 MB is a prefix |

**Unused (idx_scan=0, not PK/unique): 40 indexes, 151.2 MB.** By size: ix_ma_pair_rho 36.3, ix_gts_skill 20.6, ix_gts_ct 20.4, ix_gtr_tech 11.2, ix_gtr_ct 10.8, ix_dr_key 7.8, ix_gtrec_ct 5.5, ix_gtb_ct 4.9, ix_ma_pair_atype 4.4, ix_gtreg_ct 3.5, ix_gtu_ct 2.9, ix_gtd_ct 2.6, ix_gt_key 2.1, ix_ref_skill_effect 2.1, ix_gta_ct 2.0, ix_gtres_ct 1.9, ix_gtb_b 1.9, ix_gtwar_ct 1.7, ix_dh_key 1.6, ix_gti_ct 1.4, ix_gtc_ct 1.4, ix_gti_anc 0.8, idx_skill_effects 0.7, ix_ref_tech_effect 0.5, plus 16 < 0.5 MB (reference.idx_*, ix_cg_key, ix_rpc_req_id, ix_segments_exp, ix_views_board, ix_gtslot_ct, ix_ref_tech_parent, ix_ref_region_province). Nearly all are `analytics.game_turn_*` `(campaign_id, turn[, cqi])` indexes that the analytics runner would use — unknowable from 6 h of stats.

**Prefix/duplicate leading columns (same first `indkey` on same table):**
- `public.decisions`: ix_dec_gains (campaign_id, decision_id, ts, turn, settlements, lord_level, allies, vassals) 16.9 MB, ix_dec_turn_bounds (campaign_id, turn, decision_id) 8.0, ix_dec_campaign (campaign_id, decision_id) 6.2 — ix_dec_campaign is a strict prefix of ix_dec_gains.
- `analytics.model_agreement`: ix_ma_pair_rho / ix_ma_pair_ts / ix_ma_pair_atype / ix_ma_pair_arm all lead on `pair` (78 MB together; 2 unused).
- `analytics.game_turn_region`, `game_turn_diplomacy`, `game_turn_character`: `ix_*_ct (campaign_id, turn)` duplicates the PK prefix `(campaign_id, turn, ...)`.
- `analytics.acquisitions`: ix_acq_camp ⊂ acquisitions_pkey.
- `app.segments`: ix_segments_exp (experiment_id, seq) is an exact duplicate of `segments_experiment_id_seq_key` (`decisions/workspace.py:54` vs UNIQUE at `:33`).
- `public.rpc_requests`: ix_rpc_req_id (rpc_id) duplicates rpc_requests_pkey (`decisions/pg_schema.py:160`).

**Index larger than table:** none among the real tables (actions: 850 MB idx vs 769 MB heap is the closest — flagged as `pg_indexes_size > pg_relation_size`? no: 891,715,584 > 806,551,552 → **yes, `public.actions` indexes exceed its heap**, driven by the 741 MB 5-column unique index). 22 tiny tables (< 1 page) trivially have PK > heap (meta, settings, boards, views, experiments, segments, agreement_*, growth_summary, analytics_state, collector_versions, captive_binding, agent_types/abilities, game_event_state, action_result_outcomes, reference.meta) — noise.

## D. TOAST and blobs

Heap/toast split (`SELECT pg_relation_size(c.oid), pg_relation_size(c.reltoastrelid), pg_indexes_size(c.reltoastrelid), tc.relpages, tc.reltuples FROM pg_class c LEFT JOIN pg_class tc ON tc.oid=c.reltoastrelid`):

| table | heap B | toast heap B | toast idx B | toast pages / chunks | 
|---|---|---|---|---|
| public.blobs | 706,740,224 | **1,661,878,272** | 22,773,760 | 202,866 / 1,011,633 |
| public.taken | 286,916,608 | 49,766,400 | 679,936 | 6,075 / 29,505 |
| public.interrupts | 15,204,352 | 1,826,816 | 49,152 | 223 / 1,159 |
| public.postmortems | 1,654,784 | 13,975,552 | 229,376 | 1,706 / 9,391 |
| public.rpc_requests | 1,048,576 | 4,677,632 | 73,728 | 571 / 2,409 |
| public.actions | 806,551,552 | 0 | 8,192 | 0 |
| app.game_event | 1,019,346,944 | 0 | 8,192 | 0 |
| public.decisions | 99,287,040 | 0 | 8,192 | 0 |
| capture.snapshots | 96,010,240 | 0 | 8,192 | 0 |
| public.diplomacy_events | 32,047,104 | 0 | 8,192 | 0 |

`blobs.z` sample (`SELECT count(*), avg(length(z)), max, min, p50, p90, count(*) FILTER (WHERE octet_length(z)>2000), count(*) FILTER (WHERE octet_length(z)>8000), sum(octet_length(z)) FROM blobs WHERE blob_id % 200 = 0` → 4,755 rows): **avg 8,967 chars, max 40,947, min 167, p50 8,756, p90 14,290**; `n` column == length(z) (avg 8,967, max 40,947 — `n` is just the text length). **4,143 / 4,755 = 87.1 % exceed 2,000 bytes** (identical count at >1,996 B and at length>2000 — all ASCII); 2,464 (51.8 %) exceed 8,000 B. Extrapolated ×200: **~829 K of 951 K blobs are TOASTed**, raw text ≈ 42,638,647 B × 200 = **8.5 GB** stored in 2.37 GB heap+toast → pglz ≈ 3.6×. `pg_column_compression(z)`: pglz 4,145, NULL 610 (the < 2 KB rows stored inline, uncompressed). Content is uncompressed JSON despite the column name (`SELECT left(z,12), count(*) ... GROUP BY 1`: `{"acted":fal` 1,768, `{"acted":tru` 950, `{"allies":0.` 865, `{"armies":[{` 635, `{"active_edi` 322, `{"_eval_ms":` 162). All text columns have `attstorage='x'` (extended) and `attcompression=''` (→ default pglz).

Blob roles (sample, left join to distinct referencing sets): entity/features_blob 3,515 rows avg 9,982 B; decisions.world_blob 528 avg 11,670 B; decisions.campaign_blob 391 avg 763 B; interrupts.panel_blob 52 avg 764 B. Reference counts: `SELECT count(*), count(distinct features_blob) FROM entities` → 1,130,208 / 699,180 (dedup by sha works: 1.6 entities per blob); decisions 85,201 distinct campaign_blob, 105,177 distinct world_blob over 206,907 rows; interrupts 30,015 / 19,041 / 12,404 distinct campaign/world/panel. Unreferenced blobs (UNION of all six reference columns vs blobs): **143 of 951,161**.

Other serialized columns (same 1/200 sampling): `actions.params` avg 88 B, max 426, none > 2 KB (so the 741 MB unique index is 5 short text columns × 5.1 M rows, not params); `app.game_event.payload` avg 77 B, max 388, uncompressed (`pg_column_compression` NULL); `taken` diagnostics+timing+confirm_before+confirm_after avg 2,737 B, max diagnostics 21,290, 1,497 of 4,111 sampled rows (36 %) > 2 KB → the 48 MB toast; `interrupts` whole-row avg 893 B, max 30,244; `postmortems.payload` avg 6,959 B, max 12,607, **5,019 of 5,020 rows > 2 KB** (13.5 MB toast vs 1.6 MB heap); `capture.snapshots.payload` avg 882 B, max 5,102, 849/11,289 sampled > 2 KB but toast size 0 (pglz brings them under the threshold inline).

## E. Growth

ts columns: `decisions.ts`, `taken.ts`, `interrupts.ts`, `postmortems.ts`, `diplomacy_events.ts`, `app.game_event.ts`, `analytics.model_agreement.ts`, `campaigns.picked_ts` are all `DOUBLE PRECISION` epoch **seconds** (`decisions/pg_schema.py:56,79,99,118,124,48`; `logs/events_stream.py:24`). `app.game_event.ts` is `time.time()` **at ingestion**, not the game event time (`logs/events_stream.py:141` passes `time.time()` as `now` → `:85`). `bus.call_stats.last_ts` is an upsert watermark, not a creation time (`bus/bus_stats.py:106`). `capture.snapshots.ts` is **not epoch**: `ctx.now()` = seconds since recorder start (`manager/manager.py:40-41`), values 37.1 … 80,222.1 (`SELECT min(ts), max(ts) FROM capture.snapshots`) — no calendar growth is derivable. `app.starts` has no timestamp column at all. Tables without a ts (entities, offers, offer_scores, offer_model_scores) are joined to `decisions` by decision_id; `actions` and `blobs` are dated by their **first** reference (`min(decision_id)` per action_id via offers / per features_blob via entities).

Data range: decisions 2026-08-19 04:53 → **2026-09-04 03:43:58 UTC** (206,907 rows, 4,749 campaigns); game_event 2026-09-02 03:58 → 09-04 03:44 UTC (events thread only ran the last 2 days); model_agreement same span as decisions.

Rows per UTC day (query shape: `SELECT (to_timestamp(ts) AT TIME ZONE 'UTC')::date, count(*) FROM <t> WHERE ts > extract(epoch from now())-16*86400 GROUP BY 1`; child tables `... FROM offers o JOIN decisions d USING(decision_id) ... GROUP BY (to_timestamp(d.ts) ...)`; actions `FROM (SELECT action_id, min(decision_id) did FROM offers GROUP BY 1) f JOIN decisions d ON d.decision_id=f.did`; blobs likewise over entities.features_blob):

| day (UTC) | decisions | entities | offers | offer_scores | offer_model_scores | actions(new) | taken | interrupts | blobs(new) | game_event | model_agr. | postmortems | diplo_ev | campaigns picked | campaigns active |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 08-21 | 10,568 | 51,070 | 962,669 | 216,650 | 0 | 150,370 | 10,469 | 1,785 | 34,928 | 0 | 31,704 | 543 | 3,341 | 347 | 397 |
| 08-22 | 11,444 | 61,342 | 1,481,561 | 1,463,821 | 1,463,821 | 294,695 | 11,354 | 2,809 | 39,380 | 0 | 34,332 | 320 | 3,555 | 308 | 322 |
| 08-23 | 10,823 | 57,005 | 1,487,758 | 1,471,264 | 1,471,264 | 287,057 | 10,755 | 2,540 | 35,161 | 0 | 32,469 | 277 | 3,771 | 271 | 280 |
| 08-24 | 11,163 | 60,160 | 1,293,364 | 1,276,094 | 1,276,094 | 295,112 | 11,092 | 2,333 | 35,845 | 0 | 33,489 | 248 | 3,788 | 236 | 243 |
| 08-25 | 10,543 | 53,659 | 1,276,021 | 1,257,043 | 1,257,043 | 261,991 | 10,452 | 2,437 | 33,361 | 0 | 31,629 | 319 | 3,419 | 299 | 312 |
| 08-26 | 12,212 | 70,615 | 1,696,771 | 1,680,470 | 1,680,470 | 277,691 | 12,144 | 3,079 | 39,883 | 0 | 36,636 | 255 | 4,822 | 242 | 255 |
| 08-27 | 12,531 | 63,182 | 1,598,613 | 1,571,737 | 1,571,737 | 315,442 | 12,402 | 3,548 | 39,605 | 0 | 37,593 | 416 | 4,574 | 345 | 386 |
| 08-28 | 16,459 | 94,786 | 2,010,377 | 1,979,400 | 1,979,400 | 473,528 | 16,313 | 3,987 | 58,228 | 0 | 49,377 | 391 | 7,185 | 343 | 371 |
| 08-29 | 19,248 | 110,298 | 2,272,549 | 2,249,317 | 2,249,317 | 490,250 | 19,144 | 3,643 | 65,257 | 0 | 57,744 | 352 | 7,138 | 298 | 331 |
| 08-30 | 17,972 | 109,657 | 2,463,897 | 2,426,744 | 2,426,744 | 531,704 | 17,837 | 4,295 | 66,155 | 0 | 53,916 | 325 | 7,500 | 284 | 311 |
| 08-31 | 11,124 | 80,042 | 1,930,045 | 1,905,246 | 1,905,246 | 369,869 | 11,052 | 2,903 | 46,967 | 0 | 33,372 | 156 | 4,045 | 125 | 145 |
| 09-01 | 18,031 | 101,852 | 2,000,319 | 1,983,143 | 1,983,143 | 443,519 | 17,962 | 2,667 | 62,521 | 0 | 54,093 | 278 | 6,155 | 257 | 278 |
| 09-02 | 5,081 | 27,326 | 535,033 | 529,735 | 529,735 | 133,902 | 5,053 | 920 | 17,342 | 413,952 | 15,243 | 92 | 1,754 | 73 | 86 |
| 09-03 | 18,151 | 95,820 | 2,045,356 | 2,013,160 | 2,013,160 | 412,733 | 18,033 | 3,235 | 60,986 | 3,798,955 | 54,453 | 325 | 5,883 | 293 | 310 |
| 09-04 (to 03:44) | 3,717 | 18,101 | 344,030 | 338,074 | 338,074 | 91,320 | 3,690 | 614 | 11,796 | 782,192 | 11,142 | 70 | 1,468 | 63 | 68 |

Weekly (`date_trunc('week', ...)`): w/o 08-17 50,675 decisions / 1,663 campaigns; 08-24 100,128 / 2,203; 08-31 (4 days) 56,104 / 884. Campaigns are short: 4,827 campaigns for 206,907 decisions (43 per campaign), ~290 distinct campaigns active per day.

Bytes/row = `pg_total_relation_size / n_live_tup` from B; MB/day = mean rows/day over the 14 full days 08-21..09-03 × B/row (last-7 in parentheses):

| table | rows/day 14d (last7) | B/row | MB/day 14d (last7) |
|---|---|---|---|
| offer_scores | 1,573,130 (1,869,535) | 118 | **186.3** (221.4) |
| offer_model_scores | 1,557,655 (1,869,535) | 104 | 161.7 (194.1) |
| offers | 1,646,738 (1,893,939) | 90 | 147.8 (170.0) |
| blobs (new) | 45,401 (53,922) | 2,511 | 114.0 (135.4) |
| actions (new) | 338,419 (407,929) | 317 | 107.3 (129.4) |
| taken | 13,147 (15,056) | 1,700 | 22.3 (25.6) |
| model_agreement | 39,718 (45,457) | 393 | 15.6 (17.9) |
| decisions | 13,239 (15,152) | 659 | 8.7 (10.0) |
| entities | 74,058 (88,540) | 104 | 7.7 (9.2) |
| diplomacy_events | 4,781 (5,666) | 513 | 2.5 (2.9) |
| interrupts | 2,870 (3,093) | 429 | 1.2 (1.3) |
| postmortems | 307 (274) | 3,048 | 0.9 (0.8) |
| campaigns | 266 (239) | 311 | 0.1 |
| **subtotal (public + model_agreement)** | | | **776 (918)** |
| app.game_event (only full day 09-03) | 3,798,955 | 307 | **1,165** when the events thread runs |
| rest of analytics (1,569 MB accrued over 16 data days, assumed ∝ decisions) | | | ≈ 94 |
| bus.call_stats | 12,209 rows touched/day by last_ts — upsert, size growth ≈ new keys only | 812 | UNKNOWN (< 10) |
| app.starts | +1 row per `start_id()`/`sync_starts()` call, no ts | 170 | see F |
| capture.snapshots | 144,391 rows, relative ts | 665 | UNKNOWN per day |

Cross-check: whole-DB (16,327 MB − 113 reference − 1,534 game_event) / 16 data days = **918 MB/day**, matching the last-7 subtotal.

`actions` is not deduplicating: 338 K *new* action rows/day for 1.65 M offers/day because the UNIQUE key includes `context_id` (per-campaign cqi), so ~1 in 5 offers mints a new action row (5.1 M rows, 0.2 references each per campaign).

**Linear projection** (assumptions: recorder runs as in the last 14 days ≈ 290 campaigns/day, schema unchanged, no deletes/retention, events thread either off or on; excludes WAL, indexes already in B/row, autovacuum bloat ~0 as observed; excludes `tw_stack_post_refactor`/bench on the same disk):

| horizon | base 776 MB/d (14d) + analytics 94 | base 918 (last7) + 94 | + game_event at 1,165 MB/d |
|---|---|---|---|
| today | 16.3 GB | | |
| +6 months (182 d) | **+158 GB → ~175 GB** | +184 GB → ~200 GB | +212 GB more → ~390–410 GB |
| +12 months (365 d) | **+318 GB → ~334 GB** | +369 GB → ~386 GB | +425 GB more → ~760–810 GB |

The cluster lives on **C: with 44.5 GB free**: at 870 MB/day (base+analytics) that is **~51 days**; with game_event on, ~22 days. The 12 GB `tw_stack_post_refactor` and 0.46 GB `bench` sit in the same `base/` directory.

## F. Other schemas: DDL, writers, readers

| object | DDL (file:line) | writer (file:line) | reader (file:line) |
|---|---|---|---|
| app.game_event(event_id, ts, campaign_key, turn, faction, event, in_player_turn, payload TEXT) + ix_game_event_campaign, ix_game_event_kind | `logs/events_stream.py:19-31` | `logs/events_stream.py:144-147` executemany INSERT, rows built at `:73-88` | **no reader** (`grep -rn game_event --include=*.py .` hits only events_stream.py) — 1.53 GB write-only |
| app.game_event_state(src PK, offset_bytes, seen) | `:33-34` | `_save_offset` `:54-58` | `_offsets` `:48-51` (same file) |
| app.starts(start_id, campaign_map, faction, leader, difficulty, UNIQUE(4 cols)) | `decisions/workspace.py:16-20` | `start_id()` `:149-165` INSERT…ON CONFLICT DO NOTHING; `sync_starts()` `:168-190` INSERT DISTINCT FROM campaigns | `sync_starts` join `:178-185`; **no other reader** (`grep app\.starts` → workspace.py only). **Bug:** 951,770 of 951,908 rows have `difficulty IS NULL` (`SELECT count(*) FILTER (WHERE difficulty IS NULL) ... FROM app.starts`), so the UNIQUE never conflicts; 326 distinct `(campaign_map,faction,leader)`; top duplicate `('', 'wh_dlc05_bst_morghur_herd', '', NULL)` × 4,983. `sync_starts` is called from `analytics/gamestate.py:399-400` on **every** `step()` with pending work, inserting one full copy of the ~326 starts each time (identity reached 1,639,424). 154 MB, 83.6 MB of it the useless unique index. |
| app.experiments(config TEXT default '{}') | `:22-26` | `experiment()` `:198-201`, `json.dumps(config or {})`; called from `runctl.py:253` | **no reader** of `config` (grep `experiments` outside workspace.py: only a log string `advisor/session.py:1016`) |
| app.segments(params TEXT) + ix_segments_exp | `:28-33`, `:54` | `open_segment()` `:216-219`, `json.dumps(params or {})`; from `runctl.py:254` | **no reader** |
| app.campaign_tags | `:35-39` | `tag_campaign()` `:229-` from `decisions/decisions_stream.py:31` (only when `TW_EXPERIMENT_ID`/`TW_SEGMENT_ID` set, `:25-28`, set by `runctl.py:256-257`); `sync_starts` `:186-189` | no reader outside workspace.py (grep `campaign_tags` in advisor_api/analytics/advisor: none) |
| app.boards / app.views(spec TEXT) | `:41-50` | `ensure()` seeds 1 board + 2 views `:104-112`, `json.dumps(spec)` | **no reader** (`SEED_VIEWS` `:70-96` exist only to be inserted) |
| app.settings(k, v TEXT) | `:52` | `save_settings()` `:135-146` `json.dumps(v)` — **no caller** anywhere | `settings()` `:118-133` `json.loads` with str fallback — **no caller** outside the module. Live rows: score, home_map_frame, home_characters, include_bot_campaigns |
| bus.call_stats(channel, key, calls, hits, empties, timeouts, errors, total_ms, last_ts; PK(channel,key)) | `bus/bus_stats.py:79-93` | `StatsTracker._write` `:169-181` upsert `_UPSERT` `:95-107`, flush every 200 calls / 10 s (`:15-16`), `KEY_MAXLEN=400` (`:14`) | `load_rows()` `:351-364` → CLI report `:368` only; **no other reader** (grep `call_stats`/`load_rows` outside bus_stats.py: none). 206,732 keys, 168 MB, idx_scan 0 |
| capture.snapshots(ts, turn, entity_kind, entity_id, action_type, payload TEXT) — no PK/index; capture.latest(PK(entity_kind, entity_id, action_type)) | `ui-capture/actions_stream.py:25-35` | `_write` `:37-44`, `json.dumps(payload, separators=(",",":"))`, from `_sweep_entity` `:79-88` and `:96-98` | **no reader** of either table (grep `search_path="capture"`/`FROM snapshots`/`FROM latest` outside actions_stream.py: none; `advisor_api/queries.py:3637` reads the public view `entity_snapshots`, unrelated) |
| metrics.trials / trials_archive(payload TEXT) | `metrics_db.py:11-21` | `write_trial` `:36-46` upsert `json.dumps(row, default=str)`; `prune_unmatched` `:49-76` archive+delete; callers `advisor/session.py:1004,1013` | `trials()` `:78-97` `json.loads` → `advisor_api/queries.py:4821`; `trial_ids()` `:100-109` → `advisor/session.py:971`; `prune_unmatched` reads `payload.campaign_uuids` `:55-58` |
| reference.meta(k,v) | `advisor/reference/build_reference.py:584` | `:585` `built`, `:884` `extra_built` (epoch as text) | `advisor/mapgraph/corpus.py:57` reads `built` |
| reference.ref_* (7 tables) | `decisions/gameref.py:13-54` | `gameref.write` from `decisions/decisions_stream.py:68` | no reader outside gameref/collect/build_map_geometry (per earlier map, unchanged) |

`ui-capture/ui_capture_stream.py` writes **no DB table** — only `ctx.emit` (`:16`) to `ui_components.jsonl`.

**DDL re-executed on every connect/open** (each is a `CREATE ... IF NOT EXISTS` batch that takes catalog locks under concurrent recording):
- `decisions/store.py:74-78` — full `pg_schema.DDL` + `VIEWS` (`CREATE OR REPLACE VIEW` ×10) + meta upsert on every read-write `Store` open.
- `decisions/workspace.py:98-101` `ensure()` — called at the top of `settings/save_settings/start_id/sync_starts/experiment/open_segment/tag_campaign` (`:120,137,152,171,196,211,232`), i.e. every `analytics/gamestate.step()` with pending rows re-runs the 8-table DDL plus a `SELECT COUNT(*) FROM app.boards`.
- `logs/events_stream.py:41-45` `_db()` — once per events thread start.
- `decisions/gameref.py:57-63` `ensure()` — on every `have_faction_tech/have_subtypes/region_count/write` call (`:83,162,175,187`), i.e. every snapshot request via `decisions_stream.py:53-55`.
- `ui-capture/actions_stream.py:25-35` `_db()` — per stream start.
- `bus/bus_stats.py:169-172` `_write` — **opens a new connection and re-runs `_SCHEMA` on every flush** (every 200 bus calls or 10 s).
- `metrics_db.py:24-28` `connect()` — DDL on every non-readonly connect: each `write_trial` (`:40`) and `prune_unmatched` (`:51`).
- `analytics/store.py:37-47` `connect()` — `CREATE SCHEMA` + `SPINE_DDL` on every read-write connect; tenant DDL re-applied at `:133,142`.

## G. SQLite and file stores

`grep -rn "import sqlite3|sqlite3\." --include=*.py` (excluding .venv): **zero hits**. The only sqlite references are the dead-file list in `retention.py:55-63` (`decisions.sqlite*`, `analytics.sqlite*`, `actions.sqlite*` under `runs/human/run` and `logs/dev`) — cleanup of a previous store, nothing reads or writes them.

JSON/JSONL stores the code writes (TWDATA = `D:\twdata`, `common.py:175`; RUN_DIR = `D:/twdata/runs/human/run` `:180`; LOGS_DEV = `D:\twdata\logs\dev` `:217`):

| file | writer | content | duplicates DB? | measured |
|---|---|---|---|---|
| `run/trace.jsonl` (+ `.rolled`) | `launcher/trace.py:21,47-48` append `json.dumps(rec)`; `execute_done` from `launcher/executor.py:53` | launcher stages incl. `executed/confirmed/counted/refusal/confirm.{signal,before,after}` | **yes** — same fields as `taken(executed, confirmed, counted, refusal, confirm_signal, confirm_before, confirm_after)` written by `decisions/store.py:347-353` | live 127.3 MB + 9 rolled × ~517 MB = **4.65 GB**; `retention.py:47` ROLL_FILES, keep 3 (`:28`) not enforced (9 present) |
| `run/decisions_stream.jsonl` | `manager/manager.py:325` writer for `decisions_stream.py` `ctx.emit` (`:136,161,173,180,191,199`) | one line per decision point/options/pick/verify/interrupt/postmortem (ids + summary, no features) | partial mirror of `decisions/taken/interrupts/postmortems`. **Contains 4,734 `decisions_point` records after the DB's max ts** (file spans 2026-08-21 20:08 → 2026-09-04 18:09 UTC, 211,677 points vs 206,907 in DB; script: parse file, count `kind` with `ts > 1788493438.3`) — the 09-04 reload dropped ~14 h of recording | 159.4 MB |
| `run/loop_report.jsonl`, `turn_trail.jsonl`, `locomotion.jsonl`, `post_attack_trace.jsonl` | `advisor/loop.py:187,357,74,102` | advisor per-turn loop reports | not in DB; `advisor/session.py:297-300,741` reads tails | 48.6 / 10.4 MB |
| `run/events.jsonl` | `manager/manager.py:213` | recorder lifecycle | not in DB; `campaigns/splitter.py:210,358,430` reads | small |
| `logs/dev/events.jsonl`, `ui_components.jsonl`, `actions_stream.jsonl` | `manager.py:327,335,339` (events thread emit, `ui_capture_stream.py:16`, `actions_stream.py`) | events: per-batch counts only (`events_stream.py:149-151`) — the rows themselves go to `app.game_event`; ui_components: UI tree captures; actions_stream: sweep summaries | ui_components has no DB twin; actions_stream summaries duplicate nothing (payloads are in capture.*) | 127.2 / 190.5 / 5.7 MB |
| `logs/dev/logs/script_log_*.txt.tail` | `logs/logs_stream.py:24-30` append raw game script-log chunks | raw game log; `events_stream.py:63` re-parses `script_log_*.txt` (the originals) into app.game_event | source of app.game_event | **715 files, 48.7 GB** (largest 263 MB) — biggest store under twdata after `archive` |
| `run/shots/*.jpg` | `shots/shots_stream.py:56-60` | screenshots | no | 1,858 files, 11.0 GB; `runs/human/screens` 42,949 files 5.6 GB |
| `D:\twdata\presaves\*.save` | game saves managed by `presaves.py:8-83` (list/restore only) | 129 save files | no | 614 MB |
| `campaign_index.json`, `bake_*.json` (twdata root) | bake/campaign tooling | indices | no | < 0.2 MB |
| `metrics/` dir | 18 files 2.2 MB | — | superseded by metrics.trials (UNKNOWN whether still written) | |

D:\twdata top-level (excluding forbidden dirs): archive 51.7 GB, logs 50.3 GB (dev 49.0), runs 21.7 GB, models 14.3 GB + three `models_backup_*` 25.0 GB, cache 2.7 GB, reference 0.9 GB, stream 0.9 GB, scratch 0.8 GB.

## H. Backup and ops

- **No pg_dump/backup script in the repo**: `grep -rn -i "pg_dump|pg_restore|pg_basebackup|backup"` over *.py/*.ps1/*.bat/*.md/*.toml → only `bake.py:209-214,261` (`backup(save_file)` copies a game save). No `*.ps1`/`*.bat` at repo root; no `scripts/` dir.
- **Task Scheduler** (`schtasks /query /fo csv /v`, 386 tasks; filter on postgres|pg_|tw_stack|twdata|python|.venv): exactly one project task — **`\tw_stack health report`**, Ready, next run 2026-09-05 07:00, runs `powershell -File D:\tw_stack\analytics\health_report.ps1` → `analytics/health_extract.py --days 7` then an `agent -p` call writing `D:\twdata\reports\health\health_<stamp>.md` (`health_report.ps1:10,17-19`). Nothing backs up or vacuums Postgres. `findstr -i postgres` on the task list: none.
- **pgpass**: `%APPDATA%\postgresql\pgpass.conf` present (`Test-Path` → True).
- **Role**: `tw` rolsuper=f, rolcreatedb=**t** (can `CREATE DATABASE` — used by `advisor/optimize_catboost.py:50-53`), rolcreaterole=f, rolreplication=f; cannot create tablespaces, cannot read `data_directory`, cannot call `pg_ls_waldir` (server log 09-04 12:31:34 `permission denied for function pg_ls_waldir`).
- **Binaries** in `C:\Program Files\PostgreSQL\17\bin`: `pg_ctl.exe, initdb.exe, pg_basebackup.exe, pg_dump.exe, pg_dumpall.exe, pg_restore.exe, pg_receivewal.exe, pg_rewind.exe, psql.exe, vacuumdb.exe` all present.
- **Server logs** (`data\log\postgresql-<date>_000000.log`, readable, ~0.3–2.6 MB/day, 20 files): 09-04 message census — 2,003 `duplicate key value violates unique constraint`, 1,097 `ON CONFLICT DO UPDATE command cannot affect row a second time`, 200 `insert or update on table ... violates foreign key`, 137 `could not receive data from client`, 85 `canceling autovacuum task` (lock conflicts on app.game_event, actions_new, game.decision_timing), **40 `checkpoints are occurring too frequently`** (72 across 09-01..09-04; `HINT: Consider increasing max_wal_size`), 22 `permission denied to vacuum/analyze`. 12:08–12:40 PDT shows an out-of-tree rebuild in flight (`column "campaign_blob" does not exist`, `relation "actions_pkey" already exists`, `schema "metrics" does not exist`, `CREATE UNIQUE INDEX ... actions_ident_rites ... NULLS NOT DISTINCT` — DDL that exists nowhere in the working tree). Three "not properly shut down" recoveries 08-30 16:34, 08-30 22:27, 08-31 04:58.

## Summary of load-bearing facts

1. Stock 128 MB shared_buffers / 4 GB effective_cache_size / 1 GB max_wal_size on a 63.5 GB, 24-core, dual-NVMe box; 66.7 % hit ratio, 2.07 G buffer evictions, 38 % of checkpoints WAL-forced, 132 GB WAL in 4.8 days.
2. `tw_stack` was bulk-reloaded 2026-09-04 16:40–17:10 PDT; every stat counter (dead tuples, idx_scan, updates) reflects ~6 h with no application process attached. ~4.7 K decisions recorded 09-04 03:44–18:09 UTC exist only in `decisions_stream.jsonl`.
3. `tw_stack_post_refactor` (12 GB) took 16.5 M commits and 926 GB of temp-file spill since 08-31 — it is the busy database, on the same C: drive.
4. Growth ≈ 0.8–0.9 GB/day at ~290 campaigns/day (offer_scores 186, offer_model_scores 162, offers 148, blobs 114, actions 107 MB/day), +1.2 GB/day when the events thread runs; C: has 44.5 GB free → ~50 days.
5. Write-only tables: app.game_event 1.53 GB, capture.snapshots/latest 93 MB, bus.call_stats 168 MB (CLI-only reader), app.experiments/segments/views/settings serialized columns have no reader. app.starts is 951 K duplicate rows of 326 starts because `UNIQUE(..., difficulty)` never fires on NULL and `sync_starts()` runs per analytics step.
6. 4.75 GB of indexes; 741 MB 5-column unique index on `actions` exceeds its heap; 40 indexes unused in 6 h (151 MB, mostly analytics `(campaign_id, turn)` twins of PKs); duplicate pairs `ix_segments_exp`/`segments_experiment_id_seq_key`, `ix_rpc_req_id`/`rpc_requests_pkey`, `ix_dec_campaign` ⊂ `ix_dec_gains`.
7. blobs.z is uncompressed JSON (avg 9 KB, 87 % TOASTed, pglz 3.6×, 8.5 GB raw → 2.37 GB); 143 orphans only.
8. No backup, no pg_dump, no retention for Postgres anywhere; only a daily health-report task. pgpass present; `tw` has CREATEDB.

(Report file not written: plan mode restricts writes to the plan file; this message is the complete report.)