# 13. Execution plan

Strict order: a section starts only when every subsection before it is done, except where a subsection is marked **BLOCKED-OWNER** (needs an action only the owner can perform); blocked items stay at the top of every status report until cleared. Each item has a done-criterion. Commit granularity: one commit per subsection, message ≤ 20 words, no comments/docstrings in code, timing logs at entry/exit of every DB-touching function.

## 0. Baseline and dry run

0.1 Tag `pre-migration` on `main`; record the legacy benchmark numbers (12.4 "today" column) with a script `bench/legacy.py` against 55432. Done: tag exists; `bench/legacy.json` written.
0.2 Write `sql/03_tables.sql`, `sql/03_constraints.sql`, `sql/03_views.sql`, `sql/03_seed.sql` from 03a-03c. Done: `psql -1 -f` of all four on an empty scratch database on 55432 (`tw_stack_design_scratch`, created and dropped by the test) succeeds; `pg_dump --schema-only` diff against the files reviewed.
0.3 **BLOCKED-OWNER**: create the D: cluster (10.4 S1: initdb, service registration, role) — elevated shell. Done: `psql -p 55433 -U tw -d postgres -c 'select 1'` works; `postgresql.conf` per 10.2.
0.4 Implement `collect.normalise` (2.9) and `LEGACY_TYPES` inverse table, with the canonical encoder + SHA-256 (2.7) as `decisions/canon.py`. Done: on the M1 sample (15,390 blobs) `canon(normalise(legacy_view(normalise(blob)))) == canon(normalise(blob))` on 100% with 0 semantic differences; byte-exact on 90.7% (the residue is `hp` int/float rendering, 02 2.9).
0.5 Implement `decisions/hydrate.py` and the migrator's encoder (`migrate/encode.py`) as one module used by both. Done: F6 property test passes.
0.6 Dry run on the 1% sample: restore the dump copy to 55433 (S2), S3-S10 with `--sample 100:7`. Done: V1 100% on the sample; per-row costs recorded in `migrate/dryrun.json`; projected full-run time ≤ 90 min.

## 1. Writers and transport (recorder side)

1.1 `decisions/pg.py`: `connect(app_name, readonly, search_path)`, `Conn` with reconnect-once. Done: test kills the backend (`pg_terminate_backend`) and the next unit succeeds after one reconnect log line.
1.2 `decisions/journal.py`: UUID `req_id` on every kind, envelope fields `rpc_kind/rpc_ts/rpc_id`, `log_decide`, responses for every kind, cursor on responses. Done: K5 passes on all 7 kinds.
1.3 `decisions/store.py` units U1-U7 (07). Done: F3 self-check 200/200 on a scratch run; U1 p50 ≤ 45 ms on 55433.
1.4 `decisions/decisions_stream.py` dispatch; remove `_sync_reference`, `campaign_changed`, `decision_seq`, `workspace`. Done: recorder runs 10 decisions end-to-end on 55433 with `TW_STORE_VERIFY=1`.
1.5 `bus/bus_stats.py` → `ops.bus_call_stat` through the process connection; `logs/events_stream.py` and `ui-capture/actions_stream.py` stop writing to the DB. Done: `pg_stat_activity` during a run shows exactly the connections of 07 §7.1.
1.6 Delete `pg_schema.py`, `gameref.py`, `workspace.py`, `coverage.py`, `dilemma_audit.py`, `_bus_worker.py`; `pgbootstrap.py` applies the SQL files. Done: `grep` finds no import of the deleted modules; `db-init` on an empty database succeeds.

## 2. Reference import

2.1 Builder (04): RON parse, all-pack walk with load order, typed COPY load into `ref_build`, PK/FK policy from `column_meta`, loc split, manifest + fingerprint, swap, `dict.resolve()`. Done: I7 passes; build time logged ≤ 180 s; second run exits in ≤ 1 s with "unchanged".
2.2 Compatibility views `ref.buildings … ref.merc_units, ref.skill_actions` (04 §4.7). Done: for each view, `SELECT * ... ORDER BY key` equals the legacy `reference.<table>` content on 55433 (row-by-row diff script), except the documented differences (no 4-dp rounding; NULL for absent optional strings).
2.3 Repoint `features_db.py`, `catalogue.py`, `corpus.py`, `labels.py`, `collect._merc_flavors`, `build_map_geometry.py`; delete `schema_db.json`. Done: `catalogue.dense_ids()` counts equal R2 C (building 5259, chain 1943, unit 2609, tech 2056, skill 5944, ritual 1326, agent_action 176, item 2671, agent_subtype 565→586, effect 15064) with the subtype difference explained by 04 §4.5; `features_db` warm lookup ≤ 0.01 ms; L3 ≤ 5 ms.
2.4 `runctl.up` and recorder start call the builder check. Done: log line `reference unchanged build_id=N` at every start.

## 3. Launcher and advisor changes

3.1 `launcher/interrupts.py`: panel-time capture, `ts_choice`, `campaign_uuid`, no tree/subtree, `policy=None` without chooser, `panel closed` confirmation, delete `answer_diplomacy`. Done: a scripted campaign produces interrupt rows with `state_at = panel`, rpc payloads ≤ 4 KB, `war_declared` refusal rate over 20 screens = 0.
3.2 `advisor/loop.py`: `hydrate.record`, `log_decide` after `choose`, delete `verify_streams`/`stream_watermark`, `_verify_action_catalogues` via PK. Done: 10-decision run: `roundtrip_ms` p50 ≤ 700 ms; no 0-row offers query in `log_min_duration_statement` output.
3.3 `advisor/session.py`: 08 edits (`ops.trial`, `campaign_gains`, `start_counts`, evidence queries, models loaded once). Done: a 2-campaign session with `--ucb 2 --width` completes; `ops.trial` row written; no transient connections in `pg_stat_activity`.
3.4 `advisor/memory.py` `prebattle_attributions`/`replay_stamps` on columns; `DecisionStore` read facade. Done: T2 ≤ 400 ms whole corpus on 55433 after migration (verified in 5.4).

## 4. API and analytics rewrite

4.1 `advisor_api/db.py`, `labels.py`, delete `analytics_db.py`. Done: one connection per worker; `stamp()` one query.
4.2 `advisor_api/queries.py` + `app.py` per 08 (route by route, A1 → A13). Done per route: V9 A/B equal on 55433 for the sample parameters (legacy API on 55432 vs new on 55433 against the migrated copy from 0.6 — the dry-run copy suffices for equality on the sampled campaigns).
4.3 `analytics/*` per 08 §8.2 and §8.6; rebuild trap removed. Done: I-run `runner --rebuild --once` on the dry-run copy; `agreement_summary` values equal the legacy table for the same watermark; a second `--once` writes 0 rows.
4.4 `analytics/health_extract.py`, `debugging/timeline.py`, `decisions/cycle_audit.py`. Done: each runs on 55433 and produces the same report fields as before.

## 5. Full migration and validation (stack down)

5.1 Preflight (10.3) passes; `runctl down`; `HARNESS_OFF=1`. Done: preflight report clean.
5.2 S2 dump/restore. Done: S2 checkpoint counts equal.
5.3 S3-S9. Done: every `migrate.checkpoint` row `done`; S9 applied.
5.4 S10 validation V1-V9 (12.1-12.3, 12.6). Done: all green; `migrate.mismatch` empty for V1; report file `migrate/validation_<ts>.md`.
5.5 S11 analytics, S12 sequences. Done: `analytics.state` watermarks = MAX(snapshot_id).

## 6. Cutover

6.1 Commit everything; `git status` clean; VERSION bumped. Done: commit hash recorded in `migrate/validation_<ts>.md`.
6.2 10.7 steps 2-4 (schema rename, `TW_PG_PORT=55433`, `runctl up`, smoke). Done: smoke thresholds (12.4 rows for store/hydrate/API) met on live traffic for 10 decisions; interrupts with `state_at = panel` present.
6.3 Unset `HARNESS_OFF`; K1 interruption test on the live stack (5 kills). Done: K1 pass.
6.4 `bench/live.py` snapshot of 12.4 metrics and I1-I6 on the live stack. Done: I1-I6 green, analytics and reference checks green, unit timings and write path within 12.4. The 6.2 read-path and API rows stay open and reported, not gating.

## 7. Cleanup (owner confirmation per item in 11)

7.1 Drop legacy schemas on D: and `migrate`. Done: `\dn` shows `corpus, dict, ref, ops, analytics, public(empty)`.
7.2 Stop/remove the C: cluster service or keep it (owner's call on `tw_stack_post_refactor`/`bench`). Done: owner's decision recorded; if removed, C: free space ≥ 70 GB.
7.3 Delete repo files and dead code listed in 11.3. Done: `pytest` green; `grep` clean.
7.4 Remove the design's transient tooling (`bench/legacy.py`, `migrate/` except `verify.py --counts` which stays as a health check). Done: 12.7 counts reported ≤ targets.
