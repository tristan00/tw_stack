# 12. Verification plan

Tests live under `tests/` (pytest) and `migrate/verify.py`; benchmarks print one line per measurement (`name p50 p90 max n`). Thresholds are hard pass/fail. "Sample" = `decision_id % 100 = 7` (2,070 decisions; the M1 sample) unless "all".

## 12.1 Fidelity (the gate for everything else)

| test | method | pass |
|---|---|---|
| F1 legacy byte round-trip, all snapshots | 10 §10.5 V1: `_dumps(hydrate.record(id, legacy=True)[role]) == blobs.z` for CB, WB, every EB, ICB, IWB | 100% of 250,299 snapshots, 0 `migrate.mismatch('V1')` rows |
| F2 canonical round-trip | `canon(hydrate(id, legacy=False)) == canon(json.loads(blob))` for the same set | 100% (implied by F1; run because `canon` is what the live writer's self-check uses) |
| F3 live write→read | for 200 live decisions after cutover: the recorder keeps `canon(record)` in memory for the unit, then `canon(hydrate.record(id, legacy=False))` on the same connection must equal it (self-check enabled by `TW_STORE_VERIFY=1` for the smoke run; off in normal operation) | 200/200 |
| F4 offers | 10 §10.5 V2 | 100% in the 1000-campaign window; report outside |
| F5 interrupt projection | for all 43,392 interrupts: `options` rebuilt from `interrupt_option` equals `options_json` minus `subtree` (per option: `context, text, exploit, score, gnn, answer, dilemma_id, option_id, payload`); panel rebuilt from the panel tables equals `panel_blob` restricted to the retained keys | 100% |
| F6 set hash determinism | encode + SHA-256 of every set twice (migrator process and a fresh process) | identical; plus a property test: 10k random member permutations/edits change the hash |
| F7 version flags | for every snapshot: observed key signature (`has_v31`, `has_missions`, `has_pending_queue`, `has_campaign_meta`) == `collector_version.emits_*` | 100% |
| F8 NULL/absent/empty | for each row of 2.8: count snapshots per state in the legacy blobs and in the hydrated records | equal counts |

## 12.2 Integrity

| test | pass |
|---|---|
| I1 every FK validated at S9; orphan queries for 1:1 subtypes (`decision`/`interrupt` vs `snapshot`, `snapshot_campaign`/`snapshot_world` 1:1 with `snapshot`, `campaign_state` 1:1 with decision, `char_state_ext ⊂ char_state`) | 0 orphans |
| I2 `campaign` aggregates recomputed from base tables (n_decisions, n_interrupts, n_taken, n_counted, first/peak settlements & lord level, allies/vassals max, turns, first/last ts & snapshot) | equal |
| I3 `character.first/last_snapshot_id` recomputed from `char_state` | equal |
| I4 `state_set.n` = member count; no unreferenced sets after the orphan sweep; UNIQUE (kind, hash) holds by constraint | equal / 0 |
| I5 interrupts: `chosen` ∈ option keys; panel rows by kind; `prev_decision_id` ts ≤ ts; C.5 reassignments = 166; C.6 twin count query (same campaign, root, root_context within 60 s) reported | 100% / report |
| I6 dictionaries: every `dict.*` row with `is_reference` resolves against the live `ref`; every non-reference row has a `note`; `dict.resolve()` after a rebuild flips 0 rows when the pack is unchanged | exact |
| I7 reference: `ref.table_meta` count = 1,520; row totals = 865,404; loc rows = 241,972; every declared FK valid; `manifest` unchanged fingerprint → builder exits without writes (`ref.manifest` row count unchanged, no `ref_build` schema) | exact |
| I8 constraints under load: the pytest suite inserts a decision whose collection contains a fractional `rank`, a duplicate `skills[].key`, a NULL `turn`, an unknown `context_kind` — each must raise from the writer (`CollectError`/`IntegrityError`), never store a coerced value | raises |

## 12.3 Training-set equality and model outputs

| test | pass |
|---|---|
| T1 `model.gather(window=1000)` legacy (git `pre-migration` against 55432) vs new (55433): row count, `MODEL_COLUMNS` matrix (`numpy.array_equal` after sort by decision_id, NaN-equal), labels | identical |
| T2 `interrupt_model.gather` legacy vs new: rows and matrix; `isc_option_label` differs by design for non-dilemma screens (C.8 fix) → compared with that column excluded, and the new column's distinct values listed | identical except the listed column |
| T3 `train.walk` (GNN) taken hashes and `tally` legacy vs new for the window | identical (`taken_missing` count equal) |
| T4 `replay_options.py --n 500`: stored offers vs regenerated | 0 diffs in the window |
| T5 offer drift outside the window: `OfferDriftError` rate per collector version | reported; 0 for versions ≥ v31 |

## 12.4 Latency benchmarks (measured on the D: cluster with the stack idle, then during a live 1-campaign run; each n ≥ 200)

| path | threshold |
|---|---|
| `store.write_snapshot` (07 U1) | p50 ≤ 45 ms, p90 ≤ 80 ms (today 103 / 219) |
| `store.write_decide` | p50 ≤ 15 ms |
| `store.write_verification` | p50 ≤ 5 ms |
| `store.write_interrupt` | p50 ≤ 20 ms |
| `hydrate.record` live (warm set cache) | p50 ≤ 12 ms, p90 ≤ 25 ms |
| `hydrate.records` range, 4 workers, 62,647 decisions | ≤ 3 min wall (01 T3 budget 6.5 min single) |
| `roundtrip_ms` (advisor, end to end) | p50 ≤ 700 ms (today 719; the DB share falls from ≈ 110 ms to ≈ 50 ms; collect dominates) |
| `target_series` | ≤ 80 ms |
| `prebattle_attributions` whole corpus / window | ≤ 400 ms / ≤ 100 ms |
| `interrupt_rows` window | ≤ 300 ms |
| API routes (01 A1-A13), cold cache, fixed params | A1 ≤ 30 ms, A2 ≤ 80 ms, A3 ≤ 30 ms, A4 ≤ 15 ms, A5 ≤ 50 ms, A6 ≤ 150 ms per route, A7 ≤ 200 ms, A8 ≤ 800 ms build, A9 page ≤ 40 ms / detail ≤ 40 ms, A10 ≤ 80 ms, A11 ≤ 120 ms, A12 ≤ 80 ms, A13 ≤ 2 ms |
| analytics pass with 15k new decisions | ≤ 20 s |
| reference build (changed fingerprint) / check (unchanged) | ≤ 180 s / ≤ 1 s |
| migration dry run 1% | per-row costs recorded and extrapolated ≤ 90 min for the full run |

## 12.5 Interruption safety

| test | pass |
|---|---|
| K1 kill the manager (`Stop-Process`) 50 times at random points during a 10-decision run (instrumented sleep points inside U1-U4 via `TW_STORE_KILLPOINT`) | after each restart: no partial snapshot (every `snapshot` has its 1:1 rows), no duplicate decision uuid, every unprocessed rpc replayed exactly once, advisor recovers without manual action |
| K2 kill Postgres (`pg_ctl kill ABRT`) during writes | recovery clean; the ≤ 200 ms `synchronous_commit=off` loss is bounded by re-request |
| K3 kill the migration at random ranges (10 times) | `--resume` completes; V1-V7 pass |
| K4 kill the reference builder mid-load | `ref` untouched (live build unchanged), `ref_build` dropped on the next run |
| K5 duplicate rpc resend (same `req_id`) for every kind | one row; `rpc_response` answered twice with the same content |

## 12.6 API A/B (10 §10.5 V9)

Fields the new API no longer emits (compared after removal): `DiploEvent.outcome/deal_score/standing/state` (always null today, R4 D), `TrialRow.hist`, `EntityState.features` key types (compared through `canon`), interrupt `chosen_context` (always null), `ArmCoverage` counts for legacy `gnn` keys (C.13). Everything else byte-equal after JSON canonicalisation.

## 12.7 Simplicity metrics (C13) reported by `migrate/verify.py --counts`

| metric | today (evidence) | target |
|---|---|---|
| tables (excluding `ref` pack tables and leftovers) | 63 + 44 reference + 152 leftovers (`game`, `test_fixture*`, analytics twins) | ≤ 95 (`corpus` 45 incl. 22 member tables, `dict` 35, `ops` 7, `analytics` 8) — more explicit tables than the 18 blob-bearing ones, fewer than the 259 objects that exist today |
| DB connections while recording | ≥ 8 (R1 F) | 4 (recorder, advisor main, advisor watchdog, api) + analytics runner |
| statements per decision (recorder) | ≈ 320 | ≤ 40 |
| DDL statements executed by application processes per hour | thousands (R6 F) | 0 |
| JSON decode sites on read paths | 15 API + 12 training/live (R2, R3) | 0 (rpc transport excluded) |
| fallback branches on the touched paths | 07 §7.6 (13) + R2 F.11-F.28 + R3 F list | 0 remaining; each removal is a test that the condition raises |
