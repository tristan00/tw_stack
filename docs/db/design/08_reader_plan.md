# 8. Reader plan

## 8.1 Hydration adapter (the contract that keeps `features.py`, `memory.py`, `interrupt_model.py`, `options.py`, `build.py` unchanged)

Module `decisions/hydrate.py` (replaces `store.read_decision`, `_entities_and_offers`, `taken_rows`, `campaign_snapshots`, `interrupt_rows`, `labelled_decisions`, `replay_stamps`' blob reads). Every function logs timing on entry/exit.

`record(con, snapshot_id, legacy=True) -> dict` returns exactly today's `read_decision` shape (R2 B header):
```
{"decision_id": int, "turn": int, "campaign_id": <campaign_key str>,
 "campaign": {25 CB keys}, "world": {13 WB keys}, 
 "entities": [{"snapshot_id": decision_id*65536+entity_seq, "context_kind": str, "context_id": str, "state": {...}, "offers": []}]}
```
- `legacy=True` applies the inverse of the normalisation table (2.9) **per role and path** so the dicts are type-identical to the historical blobs: `context_id`, `state.cqi`, `enemy_agents[].cqi`, `citizenry[]`, `stationed{}` values, `reach_chars{}` keys, `faction_cqi`, `lord_pools{}.cqis[]` back to decimal strings; `_num` paths of `_parse_lord/_parse_province/_parse_campaign` back to `float` (`12.0`); slot indices in `built{}`/`building_now{}`/`locked_slots[]` back to strings; WB channel numbers stay int (int when integral, float otherwise for `hp`, `ap_pct`); `'none'`/`''` literals for `selected_edict`/`active_edict`/`province`; key sets by snapshot kind, `is_hero`, and the version's `emits_*` flags (2.5). The inverse table is data, not code branches: one dict `LEGACY_TYPES[(role, path)] → caster`; 12 tests byte equality of `_dumps(legacy_record)` against the original blob text on the migration sample, so the table is proven, not assumed.
- `legacy=False` returns the normalised record (ints for ids/counts) — the form new code reads.
- Order: entities by `entity_seq`; lists by `ord`; dict-keyed collections in sorted-key order (identical to `sort_keys=True` output, so `json.dumps(sort_keys=True)` of both forms is stable).
- Sets are cached per process (`set_id → tuple of member dicts`, LRU 50k entries ≈ 60 MB): consecutive decisions share 93-99% of their sets (change_rate.out), so the live loop hydrates ≈ 5 new sets per decision.
- `entities[].offers` is `[]` (today's live path returns 0 rows too, R2 F.2); `offers(con, records)` attaches them for training/replay: it runs `advisor.options.generate(record)` on the hydrated record and matches the stored `offer` rows by (`entity_seq`, `action_type`, `key`, `slot_index`) in `offer_seq` order, attaching `params` from the generated offer and `score…` from the row; a stored offer with no generated match raises `OfferDriftError(decision_id, offer_seq)` — never silently dropped (12 §12.3 measures the drift rate per collector version and gates training on 0 in the window).

`records(con, lo, hi, kind='decision', legacy=True) -> Iterator[dict]` streams a `snapshot_id` range with the same statements batched 200 snapshots per round trip (05 Q-L1 range variant).

`DecisionStore` read facade kept (call sites in `model.py`, `interrupt_model.py`, `train.py`, `greedy_rank.py`, `eval.py`, `wl.py`, `replay_options.py`): `max_decision_id`, `window_floor`, `window_keys`, `target_series`, `taken_map`, `action_sequence`, `taken_rows(min_decision)`, `campaign_snapshots(min_decision)`, `interrupt_rows(campaign_keys)`, `labelled_decisions(after, before)`, `snapshot_read()`, `read_decision` — reimplemented over columns + `hydrate` with identical return shapes (`taken_rows` yields `(record, taken_dict)` with the taken offer attached and `params` regenerated; `interrupt_rows` yields rows with `options` dict rebuilt from `interrupt_option` and `panel` rebuilt from the panel tables, no `campaign`/`world` keys — nothing read them, R2 F.3).

## 8.2 Per-file verdicts (every DB-touching file named in R1/R2/R3/R4/R6)

Verdicts: **keep** (no DB change), **rewrite** (kept, DB access rewritten as stated), **delete**.

| file | verdict | reason / change |
|---|---|---|
| `decisions/pg.py` | rewrite | `connect(app_name, readonly, search_path)`: `application_name`, timeouts, keepalives, `Conn` wrapper with reconnect-once; drop `TW_PG_SEARCH_PATH` override |
| `decisions/pg_schema.py` | delete | DDL/views live in the 03 SQL files applied by `db-init`; packing constants gone (entity/offer ids are columns) |
| `decisions/pgbootstrap.py` | rewrite | role/db creation + apply 03 files once; no DDL anywhere else |
| `decisions/store.py` | rewrite | writer = 07 units U1-U7; reader facade = 8.1; `_blob`, `_action_id`, `_dumps`, `_SnapshotRead` gone |
| `decisions/journal.py` | rewrite | `req_id` UUID on every request; envelope fields `rpc_kind/rpc_ts/rpc_id` never overwrite the body; `respond` for every kind; cursor = MAX(rpc_id) of responses; `_ask` = one `INSERT ... RETURNING` + `pg_notify` in the same statement; `log_options`+`log_pick` → `log_decide`; connection from `pg.Conn` |
| `decisions/decisions_stream.py` | rewrite | dispatch to U1-U7; `_sync_reference`, `campaign_changed`, `decision_seq`, `workspace.tag_campaign` removed; failures always answered; `PRUNE_EVERY` kept |
| `decisions/collect.py` | rewrite (boundary only) | apply the normalisation rule (2.9) at the end of `snapshot`/`campaign_state`/`world_state`; `_merc_flavors` reads `ref.merc_units` via the process connection; dead wrappers (R1 H.16) deleted; `game_reference` + `_LUA_REF_*` deleted |
| `decisions/gameref.py` | delete | `ref_*` tables retired (04 §4.7) |
| `decisions/workspace.py` | delete | `app.*` tables dropped except launches (`ops.launch`, written by `runctl._record_launch` directly) |
| `decisions/hydrate.py` | new | 8.1 |
| `decisions/coverage.py` | delete | its question (which keys exist per role) is the schema; `pgtest` never existed (R1 H.18) |
| `decisions/cycle_audit.py` | rewrite | `OVERLAP/ACTED_PAST/STALE_AWAITING` over `taken` + `decision_timing` columns |
| `decisions/dilemma_audit.py` | delete | replaced by `SELECT ... FROM interrupt JOIN interrupt_option` (12 has the query as a check) |
| `decisions/cco_audit.py` | keep | no DB |
| `decisions/retention.py` | keep (edit) | drop the sqlite `DEAD` list and `common.run_dbs` pseudo-path (R1 H.21) |
| `common.py` (`run_dbs`, `LOOKBACK_CAMPAIGNS`, paths) | rewrite | `run_dbs` deleted (callers use the one database) |
| `manager/manager.py` | rewrite (edit) | events thread no longer opens a DB connection; `write_current_pointer`/`observe_state` dead code (R1 H.17) removed |
| `logs/events_stream.py` | rewrite | no DB (`app.game_event` dropped, 11); keeps parsing for the jsonl counts, or deleted if the owner drops the stream (11 lists it) |
| `bus/bus_stats.py` | rewrite | `ops.bus_call_stat` upsert through the owning process connection; `key_sha` = sha256(key); no DDL, no per-flush connect |
| `bus/_bus_worker.py` | delete | orphan (R1 F.4) |
| `ui-capture/actions_stream.py` | rewrite | no DB (`capture.*` dropped); jsonl output kept |
| `ui-capture/ui_capture_stream.py` | keep | no DB |
| `advisor/loop.py` | rewrite (edits) | `read_decision` → `hydrate.record`; `log_options`+`log_pick` → `log_decide` after `choose`; `verify_streams`, `stream_watermark`, `_verify_action_catalogues`' LIKE scan → `ref.loc` PK; `I.set_snapshot` unchanged; interrupt drain passes records that already contain the launcher-captured state |
| `advisor/session.py` | rewrite (edits) | `_width_counts` → `corpus.start_counts` view; `_start_gain_stats`/`window_rewards` → `campaign_gains` view; `_ending_evidence` → `turn_open` + `interrupt` columns, no bare except, no newest-campaign fallback; `_checkpoint_trial` once per campaign from `campaign` + `decision_timing` columns; `_resolve_uuid` deleted (uuid always known: 09 C.5); `metrics_db` → `ops.trial`; `_reconcile_ledger` = `UPDATE ops.trial SET archived` ; models loaded once per session (R2 F.8) |
| `advisor/watchdog.py` | keep | rpc `hash` unchanged |
| `advisor/model.py`, `advisor/interrupt_model.py`, `advisor/mapgraph/train.py`, `greedy_train.py`, `greedy_rank.py`, `wl.py`, `replay_options.py` | keep (edits) | call the `DecisionStore` facade (8.1) with the same names; `interrupt_model.gather` drops the Python-side window filter (SQL windowed); `train.py` `taken_map` windowed |
| `advisor/mapgraph/eval.py` | delete | reads the retired `marwil` artefact (R2 A6) |
| `advisor/memory.py` | rewrite (edits) | `prebattle_attributions` = 05 Q-T2; `replay_stamps` reads `char_state.pending_recruit_unit_ids, x, y, pending_queue_set_id` columns; `feed_interrupts`/`CampaignMemory` unchanged |
| `advisor/features.py`, `advisor/options.py`, `advisor/policy.py`, `advisor/base_model.py`, `advisor/strategies.py`, `advisor/mapgraph/build.py`, `schema.py` | keep | consume the legacy-shaped record; reference through `features_db`/`catalogue` |
| `advisor/reference/features_db.py` | rewrite | tables → `ref.*` views (04 §4.7); process/thread connection; miss cache removed; `agent_action_keys` by PK; `_have` gates removed |
| `advisor/mapgraph/catalogue.py`, `corpus.py` | rewrite (edits) | `ref.*` views; fingerprint = `ref.manifest.build_id` + schema hash; `catalogue.ready()` deleted |
| `advisor/mapgraph/invariants.py`, `optimize_catboost.py`, `optimize_greedy.py`, `optuna_table.py` | keep (edits) | optuna storage DB kept; `CREATE DATABASE` on demand → `db-init` creates `optuna` once |
| `advisor/ucb_stats.py` | rewrite (edit) | `window_rewards` over `corpus.campaign_gains` |
| `campaign_growth.py` (root) | delete | `TRAJECTORY_SQL` replaced by `campaign` columns |
| `metrics_db.py` | delete | `ops.trial*` written by `session.py` through its connection |
| `advisor/reference/build_reference.py`, `ron_schema.py` | rewrite | the 04 builder (decoder kept, loader/manifest/swap new); `schema_db.json` deleted |
| `advisor/reference/build_map_geometry.py` | rewrite (edit) | reads `ref.regions`/`ref.campaign_map_regions`, writes `ref.region_geometry` |
| `advisor_api/db.py` | rewrite | 1 connection per worker thread, `search_path = corpus, dict, ref, analytics, ops`; `stamp()` = `SELECT MAX(snapshot_id) FROM corpus.snapshot` (one query); `write/columns/db_path` deleted |
| `advisor_api/analytics_db.py` | delete | analytics tables are on the same connection; no `except psycopg.Error → []` |
| `advisor_api/labels.py` | rewrite | `ref.loc` by `(tbl, col, key)`/`loc_key`; `_have`, `_one/_rows` swallow-all removed; cache keyed by `build_id` |
| `advisor_api/queries.py` | rewrite | every function listed in R3 A against 05 queries; JSON decodes removed at: `current` (campaign blob), `campaign_state` (entity blobs), `_start_snapshots`, `_campaign_chars`, `_positions_data`, `decision_detail` (entity blobs → `hydrate.record`), `campaign_buildings`/`campaign_skills` (`actions.params` → regenerated offers or `slot_state`/`skill_set_member` columns), `collect_timing`/`_phases`/`timeline` (`decision_timing` columns), `cycle_timing` (`taken` columns), `menus`/`_interrupt_coverage` (`interrupt_option`), `diplomacy_tail` (`diplomacy_event` columns), `_trials` (`ops.trial`), `campaign_verdict` (`postmortem.growth_reason`, no regex); dead code deleted (`join_outcomes`, `ucb_pick_counts`, `_start_means`, `rho_for`, `ALIGNMENT_CAVEAT`, `REWARD_CAMPAIGNS`, `SeriesPoint`); one reward formula (`reward_weights()`), the `_stamped`/`_stamped_slow` key collision fixed by one cache keyed on `stamp`; `_action_types_for` chunking → join on `dict.action` |
| `advisor_api/app.py` | rewrite (edits) | routes with no UI caller deleted (`/api/campaigns/matrix`, `/api/campaigns/{key}/decisions`, `/starts/{m}/{f}/actions`, `/models/agreement/breakdown`, `/api/analytics`, `/api/analytics/rebuild`); `stamp` thread uses the single query |
| `advisor_api/proc.py`, `ident.py`, `models.py` | keep (edits) | `models.py`: `EntityState.features` stays a dict (hydrated state); `DiploEvent` fields = `diplomacy_event` columns |
| `analytics/store.py` | rewrite | tenant registry, `state` table, watermark by `snapshot_id`; **rebuild trap removed** (C12): no row-count/source-count comparison, no `DROP TABLE` on `InvalidTableDefinition`; rebuild only on `formula_version` change or explicit `--rebuild`; no DDL on connect |
| `analytics/runner.py`, `tenants.py` | rewrite (edits) | tenants = `model_agreement`, `acquisition`, `item_event`, `agreement_rollup`; `POLL_S` kept |
| `analytics/model_agreement.py` | rewrite | source = `offer` score columns; `_score_vectors` = one PK range read |
| `analytics/state_facts.py` | rewrite | `acquisition` = 05 Q-U3 per family; `decision_features`/`campaign_endings` classes deleted |
| `analytics/item_events.py` | rewrite | `item_event` from consecutive `char_state.equipped_set_id` changes per character (window `LAG` over `char_state_character`); `item_event_state` deleted |
| `analytics/agreement_rollup.py` | rewrite (edit) | run only when the `model_agreement` watermark moved; `growth_rollup.py`, `gamestate.py`, `generations.py`, `campaign_growth.py` (analytics) deleted; `metrics.py` kept |
| `analytics/health_extract.py`, `health_report.ps1` | rewrite | columns (`snapshot`, `taken`, `interrupt`, `postmortem`); version join via `snapshot.version_id` |
| `debugging/timeline.py` | rewrite | ts indexes exist on every ts column; `taken.stderr`, `decision_timing` columns, `rpc_request.payload` text (transport) |
| `ui_docshots.py` | delete | targets routes that do not exist (R3 D) |
| `doctor.py` | keep | no DB |
| `runctl.py` | rewrite (edits) | `_record_launch` → `ops.launch`; `up` runs the reference builder check; `start_analytics` unchanged |
| `launcher/interrupts.py` | rewrite (edits) | `_record_choice` captures `campaign_state`/`world_state` via the executor's bus before the click, stamps `ts_choice`, `campaign_uuid`; drops `tree`/`controls`/`subtree`; `policy=None` when no chooser ran; `answer_diplomacy` deleted (never fires, R4 C.12); `_root_gone` confirmation replaced by the `panel closed` bus row wait (09) |
| `launcher/executor.py`, `cco_actions.py`, `click_actions.py`, `nav.py`, `diplomacy.py`, `diplomacy_actions.py`, `diplo_stream.py`, `bus_launcher.py`, `trace.py` | keep (edits) | reach the DB only through `journal.log_*`; `diplo_stream.emit` passes `kind` in the body as today (now preserved); `_emit_stream` payload keys reduced to the retained columns; `trace.jsonl` keeps `confirm_before/after` (their only remaining home) |
| `bus/mod/twcontrol.lua`, `twstate.lua` | keep | no DB; unused handlers `snapshot`/`forces` (R1 F.5) listed in 11 |
| `bake.py`, `presaves.py`, `arms.py`, `campaigns/splitter.py`, `shots/shots_stream.py`, `logs/logs_stream.py`, `bus/pack_multi.py` | keep | no DB |

## 8.3 Live loop after the change (per decision)

1. `journal.request_snapshot(active, decision_uuid)` → recorder U1 → response `{decision_id, collect_ms, store_ms}`.
2. `hydrate.record(con, decision_id)` (Q-L1, ≈ 8 ms) — the same connection, no `DecisionStore(readonly)` per thread.
3. `pol.gate.apply(record)` → `options.generate` (unchanged), `pol.choose` (unchanged; `features_db` through the process connection).
4. `journal.log_decide(decision_id, offers_with_scores, pick, timings)` → U2.
5. execute → `journal.log_verification` → U3.
6. `_drain_interrupts` → `journal.log_interrupt` per record → U4 (records carry their own captured state).
Statements per decision advisor-side: 3 rpc inserts + 1 hydration pipeline + response polls (unchanged mechanism). The `entities[].offers` DB query that always returned 0 rows (R2 F.2) is gone.

## 8.4 API sites that stop decoding JSON (S6) and the id contract

All 15 decode sites in R3 A.10 are removed (8.2 `queries.py` row). Ids exposed by the API: `decision_id` (unchanged), `offer_id = decision_id * 1048576 + offer_seq` (computed in SQL; URLs stay valid), `interrupt_id` = new `snapshot_id` (the API's `/api/decisions/menus` rows carry `legacy_interrupt_id` too for old links), `campaign_key` unchanged, `pick_id` unchanged (ids preserved at migration, 10).

## 8.5 Consumed-key coverage (S6)

Every key in R2 B1-B5 and R3 B maps to a column, array element, set member field, or a derived hydration value (2.6 for offer params; `campaign.campaign_uuid`/`faction` from `campaign`; `snapshot_id`/`offer_id` synthesised). Keys with **no reader** that are still stored (for round-trip fidelity of state blobs): all of them — state blobs are stored whole. Keys dropped: interrupt panel keys without a reader, dilemma `subtree`, `chosen_context` (100% NULL), `taken.confirm_before/after`, `diagnostics.params/execute_error`, postmortem `turn_tail/errors_tail/game_logs/trajectory/recent_battles/defeat_row/campaign_source`, diplomacy `panel.*` except `success_chance/failed_at/accepted`, `facts`, `options`, `faction_keys`, `variant`, trial `hist/policies/run_dirs/campaign_index`, rpc envelope leaks (`req_id/rpc_id/kind` inside payloads).

## 8.6 Analytics tables dissolved

| today | after | consumer repointed to |
|---|---|---|
| `decision_features` | `snapshot_campaign` + `snapshot` | `_fact_turn_states` (Q-A8 shape with `DISTINCT ON (campaign_id, turn)` over `snapshot_campaign_turn`) |
| `decision_resources`, `decision_heroes` | `resource_set_member`, `hero_count_set_member` | none (no consumer, R3 C.1) |
| `game_turn` + 13 children + 3 leftovers | dropped | none |
| `campaign_growth` | `corpus.campaign` columns + `campaign_gains` view | `campaign_rows`, `_campaign_settlement_growth` |
| `model_generations` | `analytics.model_generation` view over `ops.trial` | `model_versions` |
| `campaign_endings` | `corpus.campaign_ending` view over `postmortem` | `outcome_join` |
| `growth_summary` | dropped | none |
| `item_event_state` | not needed (LAG over `char_state`) | -- |
| `acquisitions` | `analytics.acquisition` (keyed by dictionary ids) | catalog/start/positions queries (join `dict.*` for keys) |
| `model_agreement`, `agreement_*` | kept, sourced from `offer` | unchanged routes |
| `analytics_state` | `analytics.state` (no `rows/source_rows/source_floor`) | `_freshness` |
