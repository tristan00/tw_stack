# 11. Cleanup inventory (for the owner's confirmation; nothing here is executed by the migration)

Sizes and counts from R6 B/F, R3 F, M2 C, R1 H. "When" = the earliest point at which dropping is safe under 10.

## 11.1 Databases and clusters

| object | size | why droppable | when |
|---|---|---|---|
| C: cluster `C:\Program Files\PostgreSQL\17\data` (service `postgresql-x64-17`) | 28.2 GB data dir; 44.5 GB free on C: | superseded by the D: cluster; contains `tw_stack` (16.3 GB), `tw_stack_post_refactor` (12 GB, forbidden to open, listed by name/size only), `bench` (0.46 GB), `optuna` (10 MB) | 7 days after cutover with 12 green; `tw_stack_post_refactor` and `bench` are the owner's call — they are not migrated |
| D: `tw_stack` legacy schemas after migration: `public` (18 tables + 10 views, 12 GB), `app` (9 tables, 1.69 GB), `analytics_legacy` (32 tables, 1.5 GB), `reference` (44 tables, 113 MB), `bus` (168 MB), `capture` (93 MB), `metrics` (0.9 MB), `game` (56 empty tables), `test_fixture` (18 tables, 37 MB), `test_fixture_analytics` (16 tables, 4.4 MB) | ≈ 15.6 GB | all consumers repointed (08); V1-V9 passed | same 7-day gate; `DROP SCHEMA ... CASCADE` each |
| `D:\pg17\dump\*` | 16 GB | restore succeeded (S2 checkpoint) | after cutover |
| schema `migrate` | ≈ 10 MB | validation passed | after the 7-day gate |

## 11.2 Tables dropped by design (not migrated; C12/C2)

| table | size | evidence |
|---|---|---|
| `app.game_event`, `app.game_event_state` | 1,534 MB, 5.0M rows (47% exact duplicates) | no reader in the tree (R3 E, R6 F); `ts` is ingest time; source script logs remain on disk (48.7 GB `logs/dev/logs`). The owner's commit 0665d26 added this stream three days ago — listed explicitly because dropping it reverses that intent; the design keeps the parsing thread (jsonl summary) and removes only the DB sink |
| `app.starts` | 154 MB, 951,908 rows for 326 starts | broken UNIQUE on NULL difficulty; no reader (R6 F) |
| `app.boards`, `app.views`, `app.settings`, `app.campaign_tags` | < 1 MB | no reader/caller (R3 E) |
| `app.experiments`, `app.segments` | 3 rows | replaced by `ops.launch` (migrated) |
| `capture.snapshots`, `capture.latest` | 93 MB | no reader; non-epoch `ts` (R6 E-F) |
| `bus.call_stats` | 168 MB | migrated to `ops.bus_call_stat` (key hashed); the old table dropped |
| `analytics.game_turn*` (14 + `game_turn_recruit`, `game_turn_war`, `game_turn_slot`) | ≈ 970 MB | no consumer (R3 C.1, F) |
| `analytics.decision_features`, `decision_resources`, `decision_heroes` | 216 MB | replaced by columns (08 §8.6) |
| `analytics.campaign_growth`, `model_generations`, `campaign_endings`, `growth_summary`, `item_event_state` | small | replaced by views / not needed |
| `reference.*` (44 curated tables incl. `ref_*` live capture, `dilemma_choice`, `skill_actions`, `meta`) | 113 MB | replaced by `ref` (04) |
| `public.blobs` | 2.4 GB | dissolved into typed rows (V1 proves fidelity) |
| `public.actions`, `offers`, `offer_scores`, `offer_model_scores` | 8.8 GB | `dict.action` + `offer` |
| `public.rpc_requests`, `rpc_responses` | transient | replaced by `corpus.rpc_request/response` (not migrated: a queue) |
| `public.meta`, `collector_versions`, `decisions`, `entities`, `taken`, `campaigns`, `interrupts`, `diplomacy_events`, `postmortems`, `ucb_picks`, `ucb_pick_rows` | | migrated |
| `metrics.trials`, `trials_archive` | | migrated to `ops.trial` |
| the 40 unused indexes (151 MB), `ix_rpc_req_id`, `ix_dec_gains`, `ix_dec_campaign`, `actions` 5-column UNIQUE (741 MB) | | not recreated (03c §3.16) |

## 11.3 Files and directories (repo)

| path | reason |
|---|---|
| `decisions/pg_schema.py`, `decisions/gameref.py`, `decisions/workspace.py`, `decisions/coverage.py`, `decisions/dilemma_audit.py`, `bus/_bus_worker.py`, `campaign_growth.py`, `metrics_db.py`, `ui_docshots.py`, `advisor/mapgraph/eval.py`, `advisor_api/analytics_db.py`, `analytics/gamestate.py`, `analytics/generations.py`, `analytics/campaign_growth.py`, `analytics/growth_rollup.py`, `advisor/reference/schema_db.json` | deleted per 08 |
| `rules/v1.json`, `rules/probe_gaps.json` | no consumer (R2 A7) |
| `bus/mod/twcontrol.lua` handlers `snapshot` (175-192), `forces` (1049-1118) | no Python caller (R1 F.5) |
| `collect.py` unused wrappers: `faction_resources`, `campaign_uuid`, `settlement_forces`, `missions`, `ancillary_pool`, `current_research`, `_LUA_UUID` alias, `with_uuid` parameter; `_mask_ruin_owners` no-op; `_LUA_REF_*` + `game_reference` | R1 H.8, H.16 |
| `advisor_api/queries.py` dead functions (R3 F), `app.py` routes without UI callers (08) | |
| `advisor/session.py`: `METRICS_DIR`, `IN_FLIGHT_S`, `LIVE_LOG_S`, `CURRENT_SESSION_LOG`, `_resolve_uuid`, `_verify_streams_bg`; `advisor/loop.py`: `verify_streams`, `stream_watermark`; `base_model.py`: `SHORT_HORIZON/SHORT_WEIGHT`, `turns_left`, `future_best`, `_pct/_sd`; `catalogue.ready()` | R2 F.1, F.5 |
| `analytics/health_report.ps1` task `\tw_stack health report` | kept; script rewritten (08) |

## 11.4 Data directories under `D:\twdata` (names/sizes only; not touched by this design)

| path | size | note |
|---|---|---|
| `models/mapgraph_corpus/<fp>/run/shard_00000054..73.pt` | 5.4 GB | stale shards not in the manifest (R2 D); the new fingerprint (`ref.manifest.build_id`) invalidates the whole cache once → the corpus dir is rebuilt on the first walk after cutover, and `corpus.py` prunes sibling fingerprint dirs |
| `models/mapgraph`, `models/mapgraph_interrupt`, `models/local`, `models_backup_*` | 14.5 MB + 50.8 MB + small + 25 GB | orphan artefacts (R2 D); owner's call |
| `runs/human/run/trace.jsonl.rolled` × 9 | 4.65 GB | `retention.py` keep-3 not enforced (R6 G); owner's call |
| `logs/dev/logs/script_log_*.txt.tail` | 48.7 GB | source of the retired `game_event` sink; owner's call |
| `D:\twdata\metrics` | 2.2 MB | superseded by `ops.trial` |
| sqlite leftovers listed in `retention.py:55-63` | | none exist as readers/writers (R6 G); the list is removed from `retention.py` |
