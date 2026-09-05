# 7. Write path

## 7.1 Process topology and connections (C9)

| process | connections today (R1 F) | after |
|---|---|---|
| manager (recorder thread) | store (non-autocommit) + journal (autocommit, LISTEN) + 3 transient `gameref` per snapshot + `workspace.tag_campaign` per uuid + `_merc_flavors` once + `bus_stats` flush (new connection + DDL every 10 s) + events thread connection | **1** persistent connection `application_name = 'tw-recorder'`, autocommit **on**, explicit `BEGIN`/`COMMIT`/`ROLLBACK` around each unit (7.2); `LISTEN rpc_requests` on the same connection (notifications are delivered between transactions); `bus_stats` flushes through it; events thread: no DB (11) |
| advisor session | journal per thread (main + one per campaign Watchdog) + read-only store per thread + transient in `session.py:99,121,205,734,994` + `metrics_db` DDL connects + `features_db` singleton + `catalogue` per load + `labels`... | **1** persistent connection per thread that needs one: main thread `tw-advisor`, Watchdog thread `tw-advisor-watchdog` (its own because it polls concurrently); `search_path = corpus, dict, ref, ops`; `features_db`/`catalogue`/`metrics_db`/`session` helpers take the thread's connection |
| API | per-thread corpus + per-thread analytics + process-wide labels | 1 per worker thread (`tw-api`), `search_path = corpus, dict, ref, analytics, ops`, read-only |
| analytics runner | 1 autocommit with string BEGIN/COMMIT + DDL per connect | 1 (`tw-analytics`), no DDL |
| reference builder | 1 | 1 (`tw-refbuild`) |
| trainers (child processes) | 1 + per shard worker | unchanged count (1 per process), `tw-train-<kind>` |

`decisions/pg.py:connect(app_name, readonly)` sets `application_name`, `connect_timeout = 5`, `keepalives = 1`, `keepalives_idle = 30`, `options = -c search_path=...`; a `Conn` wrapper owns the socket and, on `psycopg.OperationalError` at the start of a unit, reconnects once (bounded, logged) and re-raises the unit's error to the caller — the caller (recorder loop) retries the rpc from the journal cursor (idempotent by `req_id`, 7.5). No DDL is executed by any process except `db-init` (`pgbootstrap.py` replacement: applies 03 SQL files once) and the reference builder.

## 7.2 Recorder units: statement scripts

Every unit is one transaction; every store method logs `t0` on entry and `dt` on exit (`stderr` line `store.<method> <ms>`), including the internal phases `hash`, `sets`, `rows`.

### U1 `write_snapshot(record, decision_uuid)` — rpc `snapshot` (W1)

Python phase (no SQL): normalise (2.9), build the canonical encodings and SHA-256 for every collection (≈ 60 sets), resolve dictionary keys through the process cache (7.3), assign `entity_seq` in record order.

```
BEGIN
1  INSERT INTO corpus.decision ... ON CONFLICT (decision_uuid) DO NOTHING RETURNING decision_id     -- only after the snapshot row; see step 5 (idempotency check first):
1  SELECT decision_id FROM corpus.decision WHERE decision_uuid = $uuid                              -- hit → ROLLBACK, respond with the existing id
2  INSERT INTO corpus.campaign (campaign_key, faction_id, campaign_map_id, presave_radius, selector_id, difficulty, leader) VALUES (...) ON CONFLICT (campaign_key) DO NOTHING RETURNING campaign_id
   SELECT campaign_id FROM corpus.campaign WHERE campaign_key = $key                                 -- only when 2 returned nothing (cache miss once per campaign)
3  dictionary misses (7.3): per family one INSERT ... SELECT unnest ON CONFLICT (key) DO NOTHING RETURNING id, key; then SELECT for the rest
4  INSERT INTO corpus.state_set (kind, hash, n) SELECT unnest(...) ON CONFLICT (kind, hash) DO NOTHING RETURNING set_id, kind, hash
   SELECT set_id, kind, hash FROM corpus.state_set WHERE (kind, hash) IN (unnest(...))              -- for the sets not returned by 4 and not in the process cache
   per member kind with ≥ 1 new set: INSERT INTO corpus.<kind>_set_member SELECT unnest(...)      -- ≤ 22 statements, typically 3-6
5  INSERT INTO corpus.snapshot (campaign_id, kind_id, ts, turn, version_id) VALUES (...) RETURNING snapshot_id
6  INSERT INTO corpus.decision (decision_id, decision_uuid, n_entities) VALUES ($sid, $uuid, $n)
7  INSERT INTO corpus.snapshot_campaign ... ; INSERT INTO corpus.snapshot_read_failure SELECT unnest(...)  (usually 0 rows)
8  INSERT INTO corpus.snapshot_world ...
9  INSERT INTO corpus.world_army SELECT unnest(...) ; INSERT INTO corpus.world_hostile SELECT unnest(...)
10 INSERT INTO corpus.character (campaign_id, cqi, first_snapshot_id, last_snapshot_id) SELECT unnest(...) ON CONFLICT (campaign_id, cqi) DO UPDATE SET last_snapshot_id = EXCLUDED.last_snapshot_id RETURNING character_id, cqi
11 INSERT INTO corpus.snapshot_entity SELECT unnest(...)
12 INSERT INTO corpus.char_state SELECT unnest(...) ; INSERT INTO corpus.char_state_ext SELECT unnest(...)  (when emits_v31_block)
13 INSERT INTO corpus.province_state SELECT unnest(...)
14 INSERT INTO corpus.campaign_state ...
15 UPDATE corpus.campaign SET first_snapshot_id = COALESCE(first_snapshot_id, $sid), last_snapshot_id = $sid, first_ts = COALESCE(first_ts, $ts), last_ts = $ts,
     turns = GREATEST(turns, $turn), n_decisions = n_decisions + 1,
     first_settlements = COALESCE(first_settlements, $settlements), peak_settlements = GREATEST(COALESCE(peak_settlements, 0), $settlements),
     first_lord_level = COALESCE(first_lord_level, $ll), peak_lord_level = GREATEST(COALESCE(peak_lord_level, 0), $ll),
     allies_max = GREATEST(COALESCE(allies_max, 0), $allies), vassals_max = GREATEST(COALESCE(vassals_max, 0), $vassals),
     campaign_map_id = COALESCE(campaign_map_id, $map), selector_id = COALESCE(selector_id, $sel), difficulty = COALESCE(difficulty, $diff), leader = COALESCE(leader, $leader), presave_radius = COALESCE(presave_radius, $r)
   WHERE campaign_id = $cid
COMMIT
```
Statement count: 1 + (1-2) + (0-3) + (2 + 3-6) + 1 + 1 + 1 + 1 + 2 + 1 + 1 + 2 + 1 + 1 + 1 ≈ **22-30** (today ≈ 10 + 2E + 39 DDL + 3 connects; R1 A.19). All bulk rows go through `unnest()` array parameters (one round trip per table) — no `executemany`. `COPY` is not used on the live path (a COPY per table would be 10 round trips of a different protocol for ≤ 14 rows each).

Expected latency: Python phase ≈ 4-6 ms (json-free canonical encoding + 60 SHA-256 over ≈ 40 KB + dictionary cache lookups over ≈ 900 keys); SQL ≈ 25 statements × 0.3-0.5 ms + index inserts (≈ 350 index tuples) ≈ 15-25 ms; commit with `synchronous_commit = off` ≈ 0.1 ms → **≈ 25-35 ms p50**, ≤ 60 ms p90 (budget 01 W1). Today's `store_ms` p50 103 ms includes the 3 `gameref` connections (R1 G).

### U2 `write_decide(decision_id, offers, scores, pick, timings)` — rpc `decide` (W2+W3 merged)

The advisor sends **one** rpc after `pol.choose` carrying the surviving options in order, the score dicts (already joined by identity in the advisor, where both lists exist in memory), the pick and the timing dict. Why merge: the recorder today matches scores to offers by identity with first-wins on duplicates (R1 A.10: 12.1% of building offers collide, M2 A.1) and writes 3 transactions; the advisor holds the exact offer objects the scores were computed for, so it sends `scores` inline per offer and the recorder inserts each offer row once with its scores.

```
BEGIN
1  SELECT n_offers FROM corpus.decision WHERE decision_id = $did FOR UPDATE            -- NULL expected; non-NULL → duplicate rpc → ROLLBACK, log, done (idempotent)
2  dictionary misses for dict.action (type, key) — INSERT ... ON CONFLICT DO NOTHING RETURNING + SELECT (7.3)
3  INSERT INTO corpus.offer (decision_id, offer_seq, entity_seq, action_id, slot_index, score, exploit, rank, pct_global, gnn_impact, gnn_rank, ggnn_score, ggnn_rank) SELECT unnest(...)
4  UPDATE corpus.decision SET n_offers = $n WHERE decision_id = $did
5  INSERT INTO corpus.decision_timing (...) VALUES (...)
6  INSERT INTO corpus.taken (decision_id, campaign_id, offer_seq, entity_seq, action_id, policy_id, ts, executed, confirmed, counted, refusal_id) VALUES (..., false, false, false, $awaiting)
7  UPDATE corpus.campaign SET n_taken = n_taken + 1 WHERE campaign_id = $cid
COMMIT
```
7 statements, ≈ 120 offer rows + 121 index tuples → **≈ 8-12 ms**.

### U3 `write_verification(decision_id, result)` — rpc `verification` (W4)

```
BEGIN
1  UPDATE corpus.taken SET executed, confirmed, counted, refusal_id, confirm_signal_id, latency_ms, snapshot_ms, gates_ms, execute_ms, confirm_ms, confirm_wasted_ms, polls, total_ms, prechecks_passed, failed_precheck_id, doomed, stderr, action_id = COALESCE($forced_action_id, action_id), offer_seq = ..., entity_seq = ...
   WHERE decision_id = $did AND refusal_id = $awaiting                                  -- 0 rows → already verified (duplicate) or no placeholder (decide lost): logged as an error, no insert
2  UPDATE corpus.campaign SET n_counted = n_counted + 1 WHERE campaign_id = $cid AND $counted
COMMIT
```
Forced `end_turn`/`noop` picks that were not offered (R1 A.9) arrive with the action identity in the result; `action_id` is resolved through `dict.action` (7.3) and `offer_seq`/`entity_seq` stay NULL (9 rows today, M2 A.4). **≈ 2 ms.**

### U4 `write_interrupt(record)` — rpc `interrupt` (W5)

The launcher captures, immediately before the click, `campaign_state(bus)` and `world_state(bus)` (both already exist in `collect.py:243, 416`) and ships them in the record together with `ts_choice`, `campaign_uuid` (from the captured campaign state, fixing R4 C.5), `panel` (the retained keys only), `options` (without `subtree`), `chosen`, `policy` (NULL when no chooser ran, R4 C.4), scores. No `tree`, no `controls`. The recorder does not touch the bus for interrupts.

```
BEGIN
1  SELECT 1 FROM corpus.rpc_response WHERE req_id = $req_id                              -- duplicate guard (7.5)
2  campaign upsert as U1.2; dictionary misses; set upserts for the 6 world collections (regions/settlements/ruins/enemy_agents as sets; armies/hostiles as rows)
3  INSERT INTO corpus.snapshot (campaign_id, kind_id, ts, turn, version_id) VALUES ($cid, $interrupt, $ts_choice, $turn, $vid) RETURNING snapshot_id
4  INSERT INTO corpus.snapshot_campaign ... (eval_ms set; resource/hero_count/effect_bundle set ids NULL)
5  INSERT INTO corpus.snapshot_world (snapshot_id, region_set_id, settlement_set_id, ruin_set_id, enemy_agent_set_id) ...
6  INSERT INTO corpus.world_army ... ; INSERT INTO corpus.world_hostile ...
7  INSERT INTO corpus.interrupt (interrupt_id, prev_decision_id, ts_recorded, state_at_id, kind_id, root, dilemma_id, incident_id, root_context, region_id, chosen, answer, policy_id, executed, confirmed, counted, refusal_id, latency_ms)
   VALUES ($sid, (SELECT snapshot_id FROM corpus.snapshot WHERE campaign_id = $cid AND kind_id = $decision AND ts <= $ts_choice ORDER BY ts DESC, snapshot_id DESC LIMIT 1), $now, $panel, ...)
8  INSERT INTO corpus.interrupt_option SELECT unnest(...)
9  INSERT INTO corpus.interrupt_battle_panel ... | INSERT INTO corpus.interrupt_diplo_panel ...   (by kind; none for dilemma/event_ack/declare_war_cancel/ally_attacked)
10 UPDATE corpus.campaign SET n_interrupts = n_interrupts + 1, last_ts = GREATEST(last_ts, $ts_choice) WHERE campaign_id = $cid
COMMIT
```
**≈ 10-15 ms** (world rows 13 + 4 set lookups). Rpc payload ≈ 2-4 KB instead of 346 KB (R4 G).

### U5-U7 diplomacy / postmortem / ucb — single-transaction typed inserts

- `write_diplomacy_event`: `INSERT INTO corpus.diplomacy_event (...)` with `kind_id` from the **record's** `kind` (`deal`/`pair_checkpoint`/`campaign_end`; the journal no longer overwrites the body's `kind`/`ts` — the envelope fields travel as `rpc_kind`, `rpc_ts`), `campaign_id` resolved from the record's `campaign_key` (a key absent from `campaign` is an error: 65 rows today reference no campaign, R4 D — those are migrated with a `campaign` row created from the key with faction from the key prefix and `note`).
- `write_postmortem`: `INSERT INTO corpus.postmortem` + `INSERT INTO corpus.postmortem_growth_metric` + `UPDATE corpus.campaign SET outcome_id, defeated, picked_ts = COALESCE(picked_ts, $p) WHERE campaign_id = $cid`. Postmortems without `campaign_key` (628 today: `unhandled_screen`/`error`, R4 E) are stored with `campaign_id NULL`; the launcher fix (09 C.5/E) attaches the uuid to `UnhandledScreen` so new rows always carry it.
- `write_ucb_pick`: `INSERT INTO corpus.ucb_pick ... RETURNING pick_id` + `INSERT INTO corpus.ucb_pick_row SELECT unnest(...)`; the response carries `pick_id` back to the advisor (this rpc gets a `req_id` and a response), and the advisor includes `ucb_pick_id` in the first `snapshot` request of the campaign → `campaign.ucb_pick_id` (replaces the 120-s window join, R3 F.8).

## 7.3 Dictionary lookup strategy

- Process cache per family: `dict[str, int]` loaded whole at recorder start (`SELECT id, key FROM dict.<f>`; largest family ≈ 6k keys except `dict.action` ≈ 700k → 700k × ~80 B ≈ 56 MB of Python dict: acceptable in the recorder, loaded in ≈ 1 s; the advisor and API load families on first use, `dict.action` never).
- Miss path inside the unit: collect all missing keys of the unit, one `INSERT ... SELECT unnest($keys) ON CONFLICT (key) DO NOTHING RETURNING id, key` per family, then `SELECT id, key FROM dict.<f> WHERE key = ANY($missing_still)` for keys that lost a race (only the migrator's parallel workers can race; the recorder is a single writer). `is_reference` for a new key is computed in the same statement: `INSERT INTO dict.<f> (key, is_reference, ref_build_id) SELECT k, EXISTS (SELECT 1 FROM ref.<tbl> r WHERE r.<col> = k), CASE WHEN EXISTS(...) THEN (SELECT build_id FROM ref.manifest WHERE status = 'live') END FROM unnest($keys) k`.
- Cache never cleared (keys are immutable; ids never change). Today's caches were cleared wholesale at 4,096 blobs / 200,000 actions (R1 H.6).
- Expected misses per decision after warm-up: `dict.action` ≈ 26 (new move coordinates, M2 D) → one statement in U2; all other families ≈ 0.

## 7.4 Kill semantics per table (C9)

Every unit is atomic; a kill (SIGKILL of the manager, Postgres crash with `synchronous_commit = off` losing the last ≤ 200 ms of commits) leaves the database in one of these states:

| kill point | state | recovery |
|---|---|---|
| during U1 | nothing of the snapshot; `state_set`/member rows of that unit are rolled back with it; dictionary rows inserted in the unit are rolled back too | advisor `_await` times out (180 s, R1 E) and re-sends `snapshot` with the **same** `decision_uuid`; the recorder, restarted, processes it as a fresh snapshot |
| after U1 commit, before response | snapshot + decision exist, `n_offers` NULL, no timing/taken | advisor resend → U1 step 1 finds the uuid → responds with the existing id (no double insert); if the advisor died instead, the decision stays a permanent "orphan" with `n_offers NULL` — the same class as today's 29/4,138 (R1 E) but now identifiable (`decision.n_offers IS NULL`) and excluded from training by that predicate instead of `timings IS NULL` |
| during U2 | no offers, no taken, `n_offers` NULL | advisor does not resend fire-and-forget rpcs (unchanged); the decision is an orphan as above |
| after U2, before U3 | `taken.refusal = awaiting_execution` | `finalize_stale_awaiting` at recorder start relabels to `campaign_died` (kept; R1 A.9) |
| during U3 | placeholder stays | as above |
| U4-U7 | all-or-nothing | duplicate resend guarded by `req_id` (7.5) |
| `campaign` counters (`n_decisions`, `n_taken`, `n_counted`, peaks) | updated inside the same transaction as the row they count → always consistent with the rows | 12 verifies with a recompute |

Postgres-crash loss window: `synchronous_commit = off` is kept for the recorder (R1 A) — the advisor's response arrives after the commit returns, so a crash inside the 200 ms window loses a snapshot the advisor believes stored; the advisor then fails its `read_decision` (row missing) and re-requests with the same uuid, which re-records the state at that moment. Stated and accepted: the alternative (`synchronous_commit = on`) costs ≈ 1-2 ms per commit on NVMe (ASSUMPTION; 12 measures) and is switched on if the measured cost is < 3 ms.

## 7.5 Idempotency

- `rpc_request.req_id` is a client-generated UUIDv4 on **every** request kind (today only snapshot/turn/hash); `decision_uuid` (= the snapshot request's `req_id`) is the decision's identity; `decide`/`verification` carry the `decision_id` returned by the snapshot response.
- The recorder records every processed request in `rpc_response` (also for fire-and-forget kinds: `payload NULL`, `error` set on failure) so a replay after restart — the journal cursor now resumes from `MAX(rpc_id)` **of the responses**, not of the requests (R1 E: today unprocessed requests at a kill are skipped forever) — reprocesses only requests without a response, and U1/U2/U4-U7 first check for an existing response/uuid.
- Failures of any kind are reported back (`rpc_response.error`) so the advisor logs them; today failures of options/pick/verification/interrupt are invisible (R1 A.20).

## 7.6 Fallback branches removed on the touched write paths (C13)

| site | fallback | replacement |
|---|---|---|
| `store.py:145-146` `campaign_key = f"{faction}@{run_id}"` when no uuid | 0 rows use it | a snapshot without `campaign_uuid` raises |
| `store.py:228-230` `ts or time.time()`, `turn or 0`, `state or {}` | defaults | NOT NULL columns; missing → error |
| `store.py:262, 344` `params or {}` | | params are not stored |
| `store.py:486-488` derive `counted` when absent | | launcher always sends `counted` |
| `store.py:498` interrupt `turn = int(cs.turn or 0)` | | turn from the captured state, NOT NULL |
| `store.py:414-415` `reason = ended_by if str else json(ended_by) else error` | | `ended_by TEXT[]`, `error TEXT` — two columns |
| `journal.py:108` envelope overwrite of body `kind`/`ts` | destroys emitter kind | envelope fields renamed `rpc_kind`, `rpc_ts` |
| `decisions_stream.py:84-93` `campaign_changed` always False; `seq = 0` unreachable | | `decision_seq` dropped |
| `decisions_stream.py:53-59` `_sync_reference` probes | | deleted with `gameref` |
| `store.py:110-127` blob dedup with 4,096-entry cache flush; `store.py:140-141` action cache flush | | dictionary/set caches unbounded |
| `store.py:114-118` `SELECT` then `INSERT` (no ON CONFLICT) for blobs | | `ON CONFLICT DO NOTHING RETURNING` |
| `journal.py:26-41` thread-local connection never reconnected; `decisions_stream.py:220-223` retry loop with a dead connection | | `Conn` reconnect-once + unit retry |
| `store.py:20-41 _SnapshotRead` "reads may be torn" continue-without-snapshot | | `BEGIN ISOLATION LEVEL REPEATABLE READ` failure raises |
| `store.py:75` `SET synchronous_commit = off` per session | kept as a documented setting (7.4) | |

## 7.7 Before/after counts (C13)

| metric | today | after |
|---|---|---|
| recorder DB connections | 2 persistent + 3-5 transient per snapshot | 1 |
| commits per decision (happy path) | 6 + 1 response | 3 + responses |
| statements per decision | ≈ 10 + 2E + 39 DDL + per-option 2 (cold actions) + 3 scores/timings/taken + 2 + 2 ≈ 320 for 120 offers | ≈ 25 + 7 + 2 = 34 |
| rpc rows per decision | 4 (snapshot, options, pick, verification) | 3 (snapshot, decide, verification) |
| rpc bytes per interrupt | 346 KB avg | ≤ 4 KB |
| DDL executed by application processes | on every store open, gameref probe, workspace call, bus_stats flush, metrics connect, analytics connect (R6 F) | none |
| processes | unchanged (manager, advisor, api, analytics runner, trainers) | unchanged; the manager's `events` thread stops writing to the DB |
