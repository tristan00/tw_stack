# 1. Workload catalogue

Every access path the schema must serve, with trigger, frequency, rows touched, measured latency today, the latency budget the design commits to, and the question it answers. Sources: R1 (writers), R2 (live loop + training), R3 (API + analytics + utilities), R4 (interrupts + side tables), R6 (growth). Budgets are the design's own commitments; they are justified in 05 (walk-throughs). "Corpus today" = 206,907 decisions, 4,827 campaigns, 43,392 interrupts, 24.8M offers (R2 §A header, M2 §0).

Frequency basis: 13,239 decisions/day 14-day mean, 15,152 last 7 days (R6 E); 290 campaigns/day; 0.21 interrupts/decision (R4 B.4); 120 offers/decision (M2 A.1).

## 1.1 Write paths (recorder process, `decisions/store.py`; R1 A.19)

| # | path | trigger / frequency | rows written today | today (measured) | budget | question the write must make answerable |
|---|---|---|---|---|---|---|
| W1 | snapshot: decision + campaign + world + entities | every decision, 13.2k/day, 1 per ~6.5 s per running campaign | 1 decision + 5.5 entities + 7.5 blobs (M2 D: 4.66 blobs/decision), 5 no-op campaign UPDATEs, 3 gameref connects + 39 DDL statements (R1 A.19, H.3-4) | store_ms p50 103 / p90 219 ms (R2 A1.2), of which ~10+2E statements + 3 transient connections | p50 ≤ 60 ms, p90 ≤ 120 ms, 1 transaction, 0 transient connections, ≤ 45 statements | what was the full game state at snapshot T (state-at-time-T, S3) |
| W2 | options | every decision | 120 offers + ~26 new `actions` rows (M2 D: 25.7/decision; params in the UNIQUE key) | part of `pick_log`/`generate_ms` (0-2 ms advisor side); recorder side unmeasured, 2 statements per cold action (R1 A.19.2) | folded into W3 (one rpc, one transaction) | which actions were available, in which order, on which entity |
| W3 | pick: scores + timings + taken placeholder | every decision | 108 offer_scores + 107 offer_model_scores + 1 UPDATE decisions + 1 UPSERT taken, 3 commits (R1 A.19.3-5) | unmeasured; 3 transactions | 1 transaction with W2: ≤ 6 statements, ≤ 25 ms | which offer was chosen, with what scores, by which policy, how long each phase took |
| W4 | verification | every decision | 1 UPSERT taken (overwrites all columns), plus `_action_id` when the taken action was not offered (R1 A.9) | 2 statements | 1 UPDATE ≤ 3 ms | did the action execute/confirm, refusal, latency, executor phases, stderr |
| W5 | interrupt | 2,870/day (R6 E) | 1 interrupts row + 1-3 blobs + campaigns upsert (R1 A.12); rpc payload avg 346 KB, pre_battle 685 KB (R4 G) | 1 transaction; payload dominated by the discarded UI tree (R4 C.11) | 1 transaction ≤ 20 ms; rpc payload ≤ 4 KB (tree not shipped) | what screen, options, choice, policy, outcome, at which turn/ts, against which preceding decision, with which panel facts |
| W6 | diplomacy event | 4,781/day | 1 row, payload JSON (R1 A.16) | 1 statement | 1 statement, typed columns | deals attempted/answered, pair treaty checkpoints, campaign end summary |
| W7 | postmortem | 307/day | 1 row + 2 campaigns UPDATEs (R1 A.15) | 1 transaction | 1 transaction, typed columns, no `turn_tail` | why/when the campaign ended, verdict, growth facts |
| W8 | ucb pick | 266/day, 129 rows each (R4 F) | 130 rows | 1 transaction | unchanged shape + `k`, `scale` | selector state at campaign start |
| W9 | campaign upsert | first snapshot of a campaign (266/day) + every decision today (5 no-op UPDATEs, R1 H.3) | 1 INSERT, then 0 | see W1 | INSERT once; `last_decision_id/turns` maintained by W1's single statement | campaign identity, start, faction, map, selector, outcome |
| W10 | collector version register | recorder start | 1 upsert (R1 A.2) | -- | unchanged; NOT NULL on every snapshot (C7) | which collector wrote a snapshot |
| W11 | rpc queue | 4-5 inserts/decision advisor side + 1 response (R1 A.19) | 1,239 rows live, pruned at 900 s (R4 G) | INSERT + pg_notify, not atomic (R1 A.13) | 1 statement (`INSERT ... RETURNING` + `pg_notify` in one statement); req_id UUID NOT NULL on every request (idempotency, C9) | transport only |
| W12 | reference import | on game-data change (weeks); today manual, DROP+CREATE per table, per-row DELETE+INSERT (R5 G) | 1,520 tables / 865,404 rows / 242k loc rows | decode 2.7 s (decode_probe); load unmeasured, per-row statements | full rebuild ≤ 3 min, transactional swap, skipped when manifest unchanged (C10) | is the reference current; which build resolved a dictionary key |
| W13 | analytics fold | every 5 s pass (R3 C.0) | model_agreement 39.7k rows/day; acquisitions/item_events incremental; rollups DELETE+rebuild whole table per pass (2.5-6.2 s each, R3 C.1) | game_turn: whole-corpus GROUP BY per pass + 14 unread tables (R3 C.1) | model_agreement + acquisitions + item_events incremental from columns; rollups on model_agreement watermark change only; no blob parse; no game_turn | model agreement per decision; first-seen/acquired per (campaign, family, key) |

## 1.2 Live loop reads (advisor process; R2 A1-A4)

| # | path | trigger / frequency | rows | today | budget | question |
|---|---|---|---|---|---|---|
| L1 | `read_decision(decision_id)` → record dict (campaign, world, entities[].state) | every decision, inside `roundtrip_ms` (p50 719 / p90 1276 ms, R2 A1.2) | 1 decision, 2 + 5.5 blobs (~42 KB JSON, M2 D) | 1.7-8.6 ms fetch + json.loads (R2 A1.3-A1.4); offers query always returns 0 rows (R2 F.2) | ≤ 15 ms hydrated (≈ 40 narrow SELECTs or 1 multi-result function); no offers query | the state the policy scores against; must be byte-equivalent (after canonicalisation) to the collector's record |
| L2 | reference lookups (`features_db`, `catalogue`) | per unseen key, process caches | ≤ 20 rows | 0.002 ms warm, 147 ms cold connect (R2 C) | unchanged semantics; tables repointed to `ref.*` (04) | catalogue attributes for feature columns and graph nodes |
| L3 | `loc` LIKE `agent_actions_localised_action_name_%<suffix>` | per campaign (5 actions) | 5 | 271 ms (R2 A3.3) | ≤ 5 ms via `(tbl, col, key)` PK equality | action labels |
| L4 | `stream_watermark` / `verify_streams` | per campaign | -- | broken (`?` placeholders; R2 A3.2, F.1) | deleted | -- |
| L5 | `_width_counts` (start_counts view) | per campaign with `--width` | 129 | 48 ms, correlated subquery (R2 A3.6) | ≤ 5 ms from `campaign` GROUP BY (index) | campaigns per start |
| L6 | `window_rewards` (campaign_gains view, LIMIT 1000) | per campaign with `--ucb` | 1000 | 68 ms, whole-decisions GROUP BY (R2 A3.7) | ≤ 10 ms from `campaign` columns maintained at W1/W7 | reward per recent campaign |
| L7 | `_ending_evidence`: turn_open last 6, interrupts last 6 | per campaign end | 12 | ms (R2 A3.12) | ≤ 3 ms | last turns' scalars; last battle interrupts |
| L8 | `_checkpoint_trial`: `_resolve_uuid`, `TRAJECTORY_SQL_ONE`, `_campaign_timing` | per campaign in stretch, twice, O(n²) (R2 A3.10) | 3 queries × stretch | 58 + 16 + 7 ms each | once per campaign, ≤ 10 ms, from `campaign` + `decision_timing` columns | campaign growth and timing for the trial row |
| L9 | `metrics.trials` write/prune | per campaign / session | 1 | DDL on every connect (R3 D) | 1 upsert into `ops.trial` | trial ledger |
| L10 | hash rpc (`request_hash`, Watchdog every 15 s) | 4/min per campaign | 1 rpc | rpc round trip | unchanged | is the game responsive |

## 1.3 Training reads (child processes; R2 A5)

| # | path | trigger | rows / bytes today | today | budget | question |
|---|---|---|---|---|---|---|
| T1 | `target_series()` = turn_open scalars, whole corpus | each of 3 trainers per retrain (every N campaigns; ~13 min/retrain) | 27,772 rows | 0.10-0.17 s over a GROUP BY view (R2 A5.4) | ≤ 50 ms via `decision_turn_open` index-only | first decision scalars per (campaign, turn) → labels |
| T2 | `prebattle_attributions` | per retrain, unwindowed | 14,272 rows; jsonb subselects on world blobs (R2 A5.5) | 2.54 s | ≤ 0.3 s windowed, columns only | which attack decision preceded which pre_battle interrupt, target zone |
| T3 | `taken_rows(min_decision)` streaming | per CatBoost retrain | 62,267 rows; campaign + world + taken entity + every province blob per row ≈ 2.1 GB JSON (R2 A5.7) | ~9 s DB side, ~3 min total gather at 2.9 ms/decision (R2 A5.8) | hydrate ≤ 6 ms/decision (≤ 6.5 min DB side for the window, parallelisable by range); memory: no 2.1 GB text | full record + taken offer for every counted decision in the window |
| T4 | `campaign_snapshots(min_decision)` | interrupt retrain | 62,647 decisions × campaign+world (~820 MB) (R2 A5.13) | ~14 s | ≤ 20 s; same hydration path as T3 for campaign+world only | base state for interrupt labels |
| T5 | `interrupt_rows()` unwindowed, 3 blobs each | interrupt retrain | 43,392 rows, 383 MB world text never read (R2 A5.14, R4 H.1) | 5.7 s | ≤ 0.5 s windowed, columns only, no world | interrupt rows + options + panel facts |
| T6 | `taken_map()` whole corpus | GNN retrain | 205,385 rows | 0.99 s | ≤ 0.3 s (index-only on `taken`) | taken identity per decision |
| T7 | `labelled_decisions(after, before)` per shard | GNN retrain, only uncached shards (5,380 of 62k rebuilt last run) | all entities + offers + params of the range (R2 A5.19) | 82 s walk for 5,380 graphs | hydrate ≤ 6 ms/decision + offers ≤ 2 ms | full records with offers for a decision_id range |
| T8 | `replay_stamps` | per shard | taken rows of the shard's campaigns, `be.z::jsonb` fields (R2 A5.19) | -- | columns only (`char_state.pending_recruit_unit_ids`, x, y, `pending_queue`) | memory stamps replay |
| T9 | `window_floor` / `window_keys` | per retrain | 1 / 1000 | ms | unchanged | training window |
| T10 | `corpus.fingerprint` | per GNN walk | reference counts | 0.07 s | read `ref.manifest.build_id` (04) | is the graph cache valid |

## 1.4 API reads (advisor_api; R3 A). Cadence: **live** = ≤ every 10 s while the corpus changes, **once** = per page load.

| # | route | cadence | rows today | today | budget | question |
|---|---|---|---|---|---|---|
| A1 | `/api/run` (`current`, `throughput`, `totals`, `collect_timing`, `cycle_timing`) | live, every page | latest decision + blob decode; `COUNT(*) FROM offers` 0.33 s; 400 timings JSON decodes ×2 (R3 A.1) | ~0.5 s | ≤ 30 ms: `snapshot_campaign` scalars, `decision_timing`/`taken` columns, counts from `campaign` aggregates | what is running now, throughput, timing sparklines |
| A2 | `/api/campaigns` (`campaign_rows`) | live | whole-corpus GROUP BY decisions + taken + campaign_growth + campaign_gains + endings + picks (R3 A.2) | ~9 queries, cache-key collision rebuilds per request (R3 §0) | ≤ 80 ms from `campaign` denormalised columns; one cache key | campaign list with growth, outcome, reward |
| A3 | `/api/campaigns/{key}` (`reward_series`, `diplomacy_tail`, `campaign_turn_rollup`) | live | 1 campaign: turn_open rows, 600 diplomacy payload decodes | ms + decode | ≤ 20 ms, typed `diplomacy_event` columns | one campaign's trajectory, diplomacy, per-turn action rollup |
| A4 | `/api/campaigns/{key}/state` | live | latest campaign entity blob + latest lord blobs (DISTINCT ON) (R3 A.2) | 0.00 s + decode | ≤ 10 ms from `campaign_state`/`char_state` latest rows | current research, equipped/pool items, lords' hp/rank/region |
| A5 | `/buildings`, `/research`, `/skills`, `/items` per campaign | once | taken⋈actions with `params` decode; `_campaign_chars` blob decodes (R3 A.2) | ms + decode | ≤ 30 ms from `taken` ⋈ `offer` ⋈ state member tables | what was built/researched/learned/equipped per campaign |
| A6 | `/api/campaigns/starts`, `/starts/{m}/{f}` (+performance, openings, campaigns, research, skills, items, buildings) | live / once | whole-corpus `campaign_rows` rebuild, `start_counts`, `turn_open` GROUP BY, `_start_snapshots` 835 blobs / 8 MB (R3 A.3) | 0.25 s + rebuilds | ≤ 100 ms per route from `campaign` columns + `char_state` latest per character (index) + `analytics.acquisition` | per-start statistics, openings, skills taken |
| A7 | catalog: `/api/items`, `/items/swaps`, `/items/{key}`, `/{family}`, `/{family}/{key}`, `/choices/{family}`, `/overtime/{family}` | once | `acquisitions` whole family, `item_events` whole table, `decision_features` whole table ×2, taken parallel seq scan (R3 A.4) | 0.1-0.6 s each | ≤ 150 ms; `decision_features` replaced by `snapshot_campaign` columns (index `(campaign_id, turn, snapshot_id)`) | acquisition statistics per key/start/culture |
| A8 | `/api/positions`, `/lookup`, `/lookup/facets` (`_positions_data`) | once, warm loop every 120 s | **every campaign blob decoded** (206,907 rows, ≈ 5 s) (R3 A.5) | ≈ 5 s + Python | ≤ 0.5 s: `snapshot_campaign` scalars + `resource_set_member` join, no decode | filter campaigns by state conditions |
| A9 | `/api/decisions` page + facets, `/api/decisions/{id}` | live | page of 200 + `action_offers` view; detail decodes ≤ 40 entity blobs raw (R3 A.6) | 0.37 s facets | page ≤ 30 ms; detail ≤ 20 ms from `offer` columns + hydrated entity state (L1 adapter) | decision list, one decision with offers/scores/entities |
| A10 | `/api/decisions/actions`, `/menus`, `/timeline`, `/diplomacy` | live | `taken GROUP BY action_id` + 76 chunked `actions` lookups; **43,392 options_json decodes per request**; 200-row timeline with 2 JSON decodes each; diplomacy partials (R3 A.6) | 0.08-0.4 s | ≤ 60 ms each: `dict.action` join, `interrupt_option` columns, `decision_timing`/`taken` columns | action success rates, interrupt coverage, timeline |
| A11 | `/api/models/*` (forcing, agreement, correlations, training) | live | `taken GROUP BY`, analytics rollups, `TRAJECTORY_SQL` live per request (R3 A.7) | 0.1-0.5 s | ≤ 80 ms; `TRAJECTORY_SQL` replaced by `campaign` columns | model behaviour over versions |
| A12 | `/api/campaigns/picks`, `/picks/{id}`, `/matrix` | live | ucb_pick_rows 497k (rank ≤ 2: 7.7k; ARRAY_AGG per pick) (R3 A.8) | 0.13 + 0.11 s | ≤ 60 ms; `/picks/{id}` reads one pick, not the series | selector history |
| A13 | `db.stamp()` (3 MAX queries) | every 2 s per SSE client + every `_stamped` call | 3 | 3 × ≤ 10 ms | 1 query on `snapshot` (MAX id) ≤ 1 ms | has the corpus changed |
| A14 | `/api/analytics`, `/api/analytics/rebuild`, `/starts/{m}/{f}/actions`, `/campaigns/matrix`, `/campaigns/{key}/decisions`, `/models/agreement/breakdown` | no UI caller (R3 F) | -- | -- | deleted | -- |

## 1.5 Analytics and utility reads (R3 C-D, R4 A.7)

| # | path | trigger | today | budget / decision |
|---|---|---|---|---|
| U1 | `analytics.gamestate` (`game_turn*`, 14 tables, no consumer) | every 5 s | whole-corpus GROUP BY + every blob of pending turns | deleted (C12: unused tables are not migrated) |
| U2 | `decision_features` + `decision_resources` + `decision_heroes` | every pass | campaign blob parse | deleted; consumers read `snapshot_campaign`, `resource_set_member`, `hero_count_set_member` |
| U3 | `acquisitions`, `item_events` | every pass | entity + world blob parse in 5,000-id steps | rewritten to SQL over `campaign_state`/`char_state`/`province_state` member tables; incremental by snapshot_id watermark |
| U4 | `model_agreement` + 3 rollups + `growth_summary` | every pass | offer_scores/offer_model_scores; rollups whole-table DELETE+rebuild | model_agreement from `offer` score columns; rollups only when watermark moved; `growth_summary` deleted (no consumer) |
| U5 | `campaign_growth`, `model_generations`, `campaign_endings` | every pass (REBUILD_EVERY_PASS) | TRAJECTORY_SQL over all decisions; trials payload parse; postmortem payload parse | replaced by views over `campaign`, `ops.trial`, `postmortem` columns (no tenant, no pass) |
| U6 | `health_extract.py` (weekly task) | weekly | decisions ⋈ collector_versions, taken percentiles, interrupts LATERAL, postmortems payload parse | rewritten against columns; ≤ 2 s |
| U7 | `debugging/timeline.py` | manual | ts-window scans; `interrupts/rpc/postmortems/diplomacy` have no ts index (R3 D) | ts indexes on `snapshot`, `taken`, `diplomacy_event`, `postmortem` |
| U8 | `decisions/coverage.py`, `dilemma_audit.py`, `cycle_audit.py`, `cco_audit.py` | manual | blob-view scans; `pgtest` import missing (R1 H.18) | coverage/dilemma_audit deleted (their question is answered by the schema + verification tests); cycle_audit rewritten on `taken`/`decision_timing` columns; cco_audit no DB, unchanged |
| U9 | `gameref` live-capture (`ref_*` tables) | every snapshot: 3 connects + 39 statements (R1 H.4) | 814 regions, per-faction tech, per-subtype skills | retired: regions/tech/skills come from `ref.regions`, `ref.technology_nodes`, `ref.character_skill_node_sets`; `build_map_geometry` repointed (04) |
| U10 | `bus.call_stats` flush | every 200 calls / 10 s, new connection + DDL each flush (R6 F) | 206,732 rows, CLI-only reader | kept as `ops.bus_call_stat`; writer uses the process connection, no DDL |
| U11 | `capture.snapshots/latest`, `app.game_event`, `app.starts/boards/views/settings` | streams / analytics side effects | write-only (R6 F) | dropped (11) |

## 1.6 Derived design targets

1. The hot write (W1) and the hot read (L1) are the same shape: one snapshot = one transaction in, one hydration out. Every table in 03 is keyed so that both are range/equality scans on `snapshot_id`.
2. Training (T3, T4, T7) is the same hydration over a `snapshot_id` range, streamed; it must not materialise text. 6 ms/decision hydration × 62,647 = 6.3 min single-threaded, ≥ 4× parallel by id range → ≤ 2 min. Today: ~3 min gather + 2.1 GB text (R2 A5.7-8).
3. The API's whole-corpus scans (A2, A6, A8, A10, A11) are served by denormalised `campaign` columns maintained at W1/W7 and by `snapshot_campaign` scalar indexes; no blob decode remains anywhere (S6).
4. Interrupts (W5, T5, A10) are snapshots on the same spine; the 346 KB rpc payload becomes ≤ 4 KB.
5. Per-collection storage (03 §3.4) follows the measured change rate so W1 writes ≈ 60 set-lookups instead of 40 KB of JSON, and T3 reads member rows once per distinct set.
