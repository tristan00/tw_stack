# 10. Migration and cluster move

## 10.1 Shape

- A **new cluster on D:** (`D:\pg17\data`, service `postgresql-x64-17-d`, port 55433 during migration) receives a logical copy of today's `tw_stack` (legacy tables stay in `public`/`app`/`analytics`/`reference`/… exactly as dumped), the new schemas (`corpus`, `dict`, `ref`, `ops`, `analytics2` → renamed `analytics` at cutover, `migrate`), and the migrated data. The C: cluster is never written by the migration and keeps serving until cutover. Rollback = point the stack back at C:.
- The stack is **down for the whole migration** (dump → validation → cutover): no delta/catch-up logic. Budget ≈ 70-90 min (10.6); recording loses one evening.
- The migration is a Python program `migrate/run.py` using `decisions.pg.Conn`, `COPY ... FROM STDIN (FORMAT binary)` for bulk rows, `unnest` upserts for dictionaries/sets, and the **same** encoder/normaliser as the live writer (`decisions/store.py` unit code paths, 07) so migrated rows and live rows are produced by one code path.

## 10.2 New cluster configuration (`postgresql.conf` on D:; R6 A for today's values)

```
port = 55433
data_directory = 'D:/pg17/data'
shared_buffers = 8GB                 # today 128 MB, hit ratio 66.7%, 2.07 G evictions
effective_cache_size = 32GB          # 63.5 GB RAM box, 34 GB free
work_mem = 64MB
maintenance_work_mem = 2047MB
max_wal_size = 8GB                   # today 1 GB: 38% of checkpoints forced, 72 "too frequently" warnings
min_wal_size = 1GB
checkpoint_timeout = 15min
checkpoint_completion_target = 0.9
wal_compression = zstd
wal_buffers = 64MB
random_page_cost = 1.1               # NVMe
effective_io_concurrency = 0
max_parallel_workers_per_gather = 4
max_parallel_maintenance_workers = 8
max_worker_processes = 24
autovacuum_vacuum_cost_delay = 0
autovacuum_naptime = 30s
log_min_duration_statement = 500ms
log_checkpoints = on
log_line_prefix = '%m [%p] %a '      # application_name in every line
track_io_timing = on
listen_addresses = 'localhost'
max_connections = 50
```
Data checksums are enabled at `initdb --data-checksums` (a new cluster is the only cheap moment). `synchronous_commit` stays the server default `on`; the recorder sets it `off` per session (07 §7.4).

## 10.3 Preconditions (checked by `migrate/run.py --preflight`, all must pass)

1. Code for 07/08/09 committed on `main` (C11: code committed before cutover); `git status` clean; VERSION bumped.
2. D: free ≥ 100 GB (need ≈ 46 GB peak, 06 §6.5, m5); `D:\pg17` absent or empty.
3. C: cluster reachable; `pgpass.conf` present (R6 H); `postgres` superuser usable for `pg_dump` of `tw_stack` and `optuna` only (the forbidden databases are not dumped: `-d tw_stack`, `-d optuna` explicitly).
4. Stack down: `python runctl.py down`; `HARNESS_OFF=1` exported in the shell that will run the harness later; `pg_stat_activity` on C: shows no `tw` client backends (`application_name` is empty today, so the check is "0 client backends of user tw").
5. Reference inputs present: `db.pack`, `local_en.pack`, `schema_wh3.ron`, `Warhammer3.exe` (04).

## 10.4 Ordered steps with commands

Every step is idempotent (re-running a completed step is a no-op keyed on `migrate.checkpoint`) and logs `stage start/end` with wall time.

```
S1  initdb + service (owner runs once, needs an elevated shell):
    "C:\Program Files\PostgreSQL\17\bin\initdb.exe" -D D:\pg17\data -U postgres --data-checksums -E UTF8 --locale="English_United States.1252"
    copy migrate\postgresql.conf D:\pg17\data\postgresql.conf   (10.2, merged over the generated file)
    "C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe" register -N postgresql-x64-17-d -D D:\pg17\data -S auto
    net start postgresql-x64-17-d
    psql -p 55433 -U postgres -c "CREATE ROLE tw LOGIN PASSWORD '<from pgpass>' CREATEDB"
S2  logical copy (both databases, custom format, parallel):
    pg_dump -p 55432 -U postgres -Fd -j 8 -f D:\pg17\dump\tw_stack   tw_stack
    pg_dump -p 55432 -U postgres -Fd -j 8 -f D:\pg17\dump\optuna     optuna
    createdb -p 55433 -U postgres -O tw tw_stack ; createdb -p 55433 -U postgres -O tw optuna
    pg_restore -p 55433 -U postgres -j 8 -d tw_stack D:\pg17\dump\tw_stack
    pg_restore -p 55433 -U postgres -j 8 -d optuna   D:\pg17\dump\optuna
    checkpoint: row counts of the 18 public tables on 55433 == on 55432 (SELECT count(*) each; ≈ 10 s)
S3  new schema: psql -p 55433 -d tw_stack -U tw -1 -v ON_ERROR_STOP=1 -f sql/03_tables.sql   (03a/03b/03c tables, PKs, UNIQUEs; FKs and secondary indexes are in sql/03_constraints.sql applied at S9)
S4  reference build: python -m advisor.reference.build_reference --port 55433   (04; ≈ 100 s; writes ref.manifest live)
S5  migrate stage M0 (single range): collector versions (32 rows + 3 sentinels), campaigns (4,827; ids preserved via OVERRIDING SYSTEM VALUE), dict seeds (non-reference rows), ops.launch from app.experiments/segments, ops.trial* from metrics.trials(+archive)
S6  migrate stage M1 decisions: ranges of 2,000 decision_ids (104 ranges), 6 worker processes:
      read: SELECT d.*, c.campaign_key, bc.z, bw.z, e.entity_seq, e.context_kind, e.context_id, be.z ... WHERE decision_id BETWEEN lo AND hi ORDER BY decision_id, entity_seq  (PK range scans)
      for each decision: rec = normalise(json.loads(...)); version = registered or sentinel by observed key signature (2.5); rows = encode(rec)   (the U1 code path minus the rpc)
      write: COPY snapshot/decision/snapshot_campaign/snapshot_world/world_army/world_hostile/snapshot_entity/char_state/char_state_ext/province_state/campaign_state ... FROM STDIN (FORMAT binary) with explicit snapshot_id = decision_id (OVERRIDING SYSTEM VALUE); sets via INSERT ... ON CONFLICT (kind, hash) DO NOTHING RETURNING + member COPY; characters via INSERT ... ON CONFLICT (campaign_id, cqi) DO UPDATE (last_snapshot_id = GREATEST)
      one transaction per range; migrate.checkpoint('M1', lo) = done with rows_in/rows_out
S7  migrate stage M2 offers+taken: same ranges, 4 workers:
      read offers ⋈ actions ⋈ offer_scores ⋈ offer_model_scores by decision range; dict.action upsert per range (type, key); COPY offer; taken ⋈ actions (+ json.loads of timing/diagnostics for the retained columns) → COPY taken; decisions.timings → COPY decision_timing
      M2 depends on M1 done for the range (FK to snapshot_entity holds by construction; validated at S9)
S8  migrate stage M4 interrupts (ranges of 2,000 interrupt_ids, 4 workers; 9 §9.4), M5 side tables (diplomacy_events → diplomacy_event with kind inferred from payload shape per R4 D: pair_checkpoint ⇔ 'pair' key without 'channel'; campaign_end ⇔ 'turns_played'; deal ⇔ 'channel'; postmortems; ucb_picks/rows with ids preserved; campaign.ucb_pick_id by the 600-s same-map/faction match that R4 F verified for 3,657/3,853 picks, else NULL)
S8b migrate stage M6 campaign aggregates (M9): one `UPDATE corpus.campaign ... FROM (SELECT ... GROUP BY campaign_id)` per aggregate family, own `migrate.checkpoint` row; runs before S9 and V5
S9  constraints and indexes: psql -1 -f sql/03_constraints.sql   (every FK validates; every secondary index builds with max_parallel_maintenance_workers = 8) ; ANALYZE
S10 validation (10.5) — must pass 100% before S11
S11 analytics: python -m analytics.runner --rebuild --once   (model_agreement from offer, acquisition/item_event folds, rollups; 5 §Q-U3)
S12 sequences: SELECT setval for every identity (snapshot_id → MAX+1 etc.)
S13 cutover (10.7)
```

Range idempotency: a worker starting a range with `state IN ('running','failed')` first deletes that range's rows from every corpus table keyed by `snapshot_id`/`decision_id` (`DELETE ... WHERE snapshot_id BETWEEN lo AND hi`, PK range) and re-runs; sets and dictionary rows left by the failed attempt are valid rows (they are values) and are reused by the retry. Workers claim ranges with `UPDATE migrate.checkpoint SET state='running', worker=$w, started_ts=now WHERE stage=$s AND range_lo = (SELECT range_lo FROM migrate.checkpoint WHERE stage=$s AND state='pending' ORDER BY range_lo FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING range_lo, range_hi`. A kill at any point leaves ranges `running` that the next start re-does; nothing is half-visible because each range is one transaction.

Bulk-load settings for the migration session: `SET synchronous_commit = off; SET maintenance_work_mem = '2GB'`; FKs and secondary indexes absent until S9 (`03_constraints.sql`), PKs/UNIQUEs present (needed for ON CONFLICT and range deletes). No `wal_level = minimal` toggle (it would need a restart; WAL volume ≈ 6 GB at zstd, acceptable).

## 10.5 Validation (through the database, C11)

| check | method | pass |
|---|---|---|
| V1 round-trip, every snapshot | 4 workers over `snapshot_id` ranges: `rec = hydrate.record(id)`; for each role: `canon(normalise(rec_role)) == canon(normalise(json.loads(blobs.z)))` (CB, WB, each EB, ICB, IWB); on inequality write `migrate.mismatch(stage='V1', snapshot_id, role, entity_seq, path, expected, actual)` for the first differing path | 0 mismatches over 250,299 snapshots (100%) |
| V2 offers | per decision: `hydrate.offers()` (2.6) vs legacy `offers ⋈ actions`: identity list equal in order; `params` equal (`json.dumps(sort_keys)`) | 100% inside the 1000-campaign window (`decision_id ≥ window_floor`); outside: mismatch rate per collector version reported to `migrate.mismatch('V2')`, non-blocking |
| V3 row counts | per legacy table vs new: decisions = decision rows; entities = snapshot_entity rows; offers = offer rows; offer_scores rows = offers with `score IS NOT NULL`; taken = taken; interrupts = interrupt; diplomacy_events = diplomacy_event; postmortems; ucb rows; campaigns − 78 orphans (9 C.5) = campaign | exact |
| V4 FKs | `03_constraints.sql` applied with validation (S9); plus `SELECT count(*) FROM corpus.decision d LEFT JOIN corpus.snapshot s ... WHERE s IS NULL` style orphan checks for every 1:1 subtype | 0 |
| V5 aggregates | recompute `campaign.n_decisions/n_taken/n_counted/peak_*/first_*/turns` from base tables; compare | exact |
| V6 sets | every `state_set` row referenced ≥ 1 time (orphans from failed ranges deleted: `DELETE FROM state_set WHERE NOT EXISTS (...)` per referencing column, then re-check); `n` = member count | exact |
| V7 interrupts | `chosen` ∈ option keys; panel presence by kind; `prev_decision_id` ≤ interrupt ts; C.5 reassignment count = 166 | exact |
| V8 training-set equality | `model.gather(window=1000)` on 55432 (legacy code, git tag `pre-migration`) and on 55433 (new code): identical row count and identical feature matrix (`MODEL_COLUMNS`, values compared with `numpy.array_equal` after sorting by decision_id); same for `interrupt_model.gather` and `train.walk` taken hashes | identical |
| V9 API A/B | for every route with a UI caller (01 A1-A13), fixed parameter set (10 campaigns, 3 starts, 20 decisions, 5 keys per family): JSON responses from the old API on 55432 and the new on 55433 compared after removing `Server-Timing` and the fields the new API no longer emits (listed in 12) | identical |

V1 is the migration's own read path (05 Q-V): ≈ 8 ms/snapshot → 250k × 8 ms / 4 workers ≈ **8 min**.

## 10.6 Time and disk budget

| step | basis | time |
|---|---|---|
| S2 dump + restore 16.3 GB | pg_dump/pg_restore -j 8 on NVMe, ASSUMPTION ≥ 150 MB/s effective incl. index rebuild (14) | ≈ 15 min |
| S4 reference build | 04 §4.6 | ≈ 2 min |
| S6 M1 decisions | per decision: read 42 KB (M1 E: EB fetch 194 MB/s, WB 320 MB/s → ≈ 0.25 ms), json.loads ≈ 0.4 ms (M1 E), normalise + encode + 60 × SHA-256 ≈ 3 ms (ASSUMPTION), COPY ≈ 0.5 ms → ≈ 4.5 ms; 206,907 × 4.5 ms = 15.5 min single, **≈ 3.5 min on 6 workers** (the C:-side read is on the D: copy, so both sides are on D: NVMe) |
| S7 M2 offers/taken | 24.8M rows read via PK ranges (≈ 150k rows/s per worker incl. the 3-way join, ASSUMPTION) and COPY-written (≥ 300k rows/s): 24.8M / 4 workers / 150k = 41 s read + 20 s write ≈ **2 min**; taken 205k × 2 ms (json of diagnostics) ≈ 1 min |
| S8 M4/M5 | 43,392 interrupts × 6 ms / 4 + side tables ≈ **2 min** |
| S9 constraints + indexes | ≈ 2,461 ref FKs already validated at S4; corpus FKs: offer → snapshot_entity (24.8M probes ≈ 60 s), others < 20 s; secondary indexes: offer PK is inline; `taken_*`, `snapshot_*`, `char_state_character`, etc. ≈ 5 M entries total ≈ 1 min | ≈ 4 min |
| S10 validation | V1 8 min; V2 (offers + generate ≈ 2 ms/decision → 207k × 2 ms / 4 = 2 min); V3-V7 seconds; V8 two gathers ≈ 2 × 4 min; V9 ≈ 5 min | ≈ 25 min |
| S11 analytics | model_agreement 207k decisions × 3 pairs from `offer` ≈ 3 min; acquisition/item_event folds over 207k snapshots ≈ 3 min; rollups 20 s | ≈ 7 min |
| **total** | | **≈ 60 min**, budget 90 min |

Disk on D: at peak: dump 16 GB + restored legacy 16 GB + new 5.1 GB + WAL ≤ 8 GB + ref 0.3 GB ≈ **46 GB** (06 §6.5 counted the dump once; both are kept until S13 succeeds); 1,771 GB free.

A **dry run** (step 0 in 13) runs S3-S10 against a 1% sample (`decision_id % 100 = 7`, the M1 sample) on the restored copy and measures every per-row cost above; the projections are then replaced by measurements before the full run.

## 10.7 Cutover and rollback

Cutover (stack still down):
1. `git tag pre-migration` exists on the last commit that ran against C: (S-precondition 1); the new code is on `main` and committed.
2. `ALTER SCHEMA analytics RENAME TO analytics_legacy; ALTER SCHEMA analytics2 RENAME TO analytics;` on 55433 (the new analytics tables were built under `analytics2` to coexist with the restored legacy schema).
3. Environment: `TW_PG_PORT=55433` in `config.toml`/`runctl` env (one value; every process reads `pg.py` defaults from it). Alternatively stop the C: service and re-register D: on 55432 — not done: keeping both ports live makes rollback a one-line change.
4. `python runctl.py up` (with `HARNESS_OFF` still set); smoke: one campaign for 10 decisions; check `store.write_snapshot` timing lines ≤ 60 ms p90, `hydrate.record` ≤ 15 ms, `/api/run` 200 OK; interrupts recorded with `state_at = panel`.
5. Unset `HARNESS_OFF`; normal operation. The legacy `public.*` tables on D: and the whole C: cluster are kept **untouched until 12 has passed on live traffic for 7 days** (11 lists them for the owner's confirmation).

Rollback (any failure in 4 or within the 7 days): `runctl down`; `TW_PG_PORT=55432`; `git checkout pre-migration`; `runctl up`. Data recorded on D: in the interval is not copied back (accepted: the interval is short and the old code cannot read the new schema). After a rollback, the D: database is dropped and the migration restarts from S2 after the fix.

## 10.8 Failure semantics of the migration itself

| failure | state | recovery |
|---|---|---|
| kill during S2 | partial restore | `dropdb tw_stack` on 55433, rerun S2 |
| kill during S6-S8 | ranges `running` | rerun the same command; the claimed ranges are re-done (10.4) |
| V1 mismatch | `migrate.mismatch` rows | fix the encoder/`LEGACY_TYPES`, re-run the affected ranges (`--redo-range lo`), re-validate; never proceed to S11 |
| S9 FK failure | constraint name in the error | the offending rows are a migration bug (the source data satisfies them by construction); fix, redo range, rerun S9 |
| disk full | D: 1.7 TB; not expected | -- |
