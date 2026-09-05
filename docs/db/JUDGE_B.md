# JUDGE_B -- hiring-committee review of DESIGN_B

Reviewed: BRIEF.md, all 14 DESIGN_B files, evidence lanes R1-R6/M1-M3, change_rate.out, and the code where a claim depended on a line. Two read-only queries were run against the live 17.10 server to test config claims.

# Scores

| # | score | one-line evidence |
|---|---|---|
| S1 Query-derived | 4 | Every table/index traces to 01 (03c §3.16); walk-throughs are concrete, but Q-A4/Q-A6 claim DISTINCT ON "stops at the first row" (it does not), hydration L1 is admitted slower than today (8 ms vs 2-9 ms + 0.4 ms), and the offer PK is under-sized. |
| S2 Keys and integrity | 3 | FDs/NF stated per relation (2.3), UNIQUEs only where M1 measured 100%; but `char_state.trait_set_id/trait_progress_set_id NOT NULL` contradicts M1 C (v31+ keys), SMALLINT identity + ON CONFLICT DO NOTHING burns ids (M2 B measured 854k burnt on the same pattern), `lord_pool_candidate.trait_levels` has no source. |
| S3 Temporal/identity | 4 | One spine, `state_at` enum, `prev_decision_id`, `character(campaign_id,cqi)`, Q-ST; but migrated interrupts get no defined `snapshot_id` and the "monotonic in ts" property is silently lost. |
| S4 Measured sizing | 4 | Arithmetic from M1/M2/R6/change_rate is shown and mostly reproduces; ignores intra-row alignment and line pointers (offer 80 B claimed, 92 B real), 31 GB vs 46 GB disk peak stated in two places, "100 GB in 2 years" vs "9 months". |
| S5 Write path | 4 | One transaction per unit, uuid idempotency, one connection per process, timing logs, latency reasoned; kill-safety of U4-U7 hinges on an `rpc_response` row whose transaction placement is never stated; `taken` updates are not HOT as claimed. |
| S6 Reader coverage | 3 | R2/R3 matrices mapped and JSON decode removed; `panel.region` (read by interrupt_model.py:63-92 for every screen) has no column in `interrupt_battle_panel`; battle panel keeps three unread keys against its own C2 rule; `trials.ruleset` read but dropped unlisted. |
| S7 Reference import | 4 | All packs in load order, RON type map justified by census, PK/FK policy with R5 numbers, manifest+fingerprint, build-into-`ref_build`-and-rename, consumers repointed, ~100 s stated; `ref.region_geometry` and `ref.manifest` live inside the schema that is renamed away. |
| S8 Interrupts | 4 | Every C.1-C.16 addressed, C.3/C.7 explicitly not fixed with reasons, launcher-side capture is the right fix; migration of interrupts is broken by the id defect (S3). |
| S9 Migration | 2 | Checkpoint table, SKIP LOCKED claiming, range idempotency, through-DB validation, cutover/rollback are all present; but S1 cannot start (config rejected, wrong locale name), S3 collides with the restored `analytics` schema, M4 has no id assignment, M1 overflows SMALLINT sequences, parallel ranges deadlock on value rows and corrupt `character.first_snapshot_id`, `campaign` aggregates are never populated. |
| S10 Simplicity/rules | 3 | 8+ connections -> 4-5, 320 statements -> 34, 0 DDL, timing logs, numbered plan; tables grow 63 -> 95 (+1,520 ref), three new fallback branches (loc split, version sentinel `else`, synthetic campaign rows), one decision delegated (`events_stream`). |
| S11 Evidence discipline | 4 | Nearly every number cites a lane and 20 of 22 spot checks reproduce; two misquotes (M2 B "95.7% single-use", M2 A.1 "12.1% with slot_index") and the sequence-burn evidence in M2 B was ignored. |
| S12 Coherence R1-R6 | 4 | R1-R5 met on paper; R6 numbered sections and BLOCKED-OWNER pinning present; one choice delegated to the owner (08 §8.2 `logs/events_stream.py`). |
| **Total** | **43 / 60** | |

# Defects

## Blockers

**B1 -- 10_migration §10.2 / §10.4 S1: the D: cluster cannot be initialised or started as written.**
Evidence: `SET effective_io_concurrency = 200` on the live 17.10 Windows server returns `invalid value ... must be set to 0 on platforms that lack posix_fadvise()` (verified); a postgresql.conf with that line makes the postmaster refuse to start. `initdb --locale=English_United_States.1252` is not a Windows locale name; R6 §0 shows the cluster collation is `English_United States.1252` (space). S1 is the BLOCKED-OWNER step, so the owner would hit two failures in an elevated shell with no guidance.
Fix: `effective_io_concurrency = 0`; `--locale="English_United States.1252"`; keep `wal_compression = zstd` (enumvals on this build include zstd, verified).

**B2 -- 03a §3.1 + 07 §7.3 + 10 §10.4 S6: SMALLINT identity dictionaries are exhausted by the parallel migration.**
Evidence: every dictionary is `id SMALLINT GENERATED ALWAYS AS IDENTITY` and the miss path is `INSERT ... SELECT unnest($keys) ON CONFLICT (key) DO NOTHING`; `nextval` is consumed for every proposed row including conflicts (M2 B: `max(action_id)` 5,962,665 vs 5,108,439 rows today, "~854k identity values burnt by ON CONFLICT DO NOTHING"; re-verified live). Six M1 workers each start with an empty cache and each proposes nearly every skill/building/effect-bundle key once: 6 x 5,944 (`dict.skill`, R2 C) = 35,664 > 32,767 -> `nextval: reached maximum value of sequence` aborts the range and no retry can succeed.
Fix: `INTEGER` ids on every dictionary; miss path = `SELECT id,key WHERE key = ANY($keys)` first, INSERT only the truly missing; seed all families in a single-worker M0 pass over the corpus key universe.

**B3 -- 09 §9.4 / 10 §10.4 M4: migrated interrupts have no defined `snapshot_id`.**
Evidence: M1 inserts `snapshot_id = decision_id` with `OVERRIDING SYSTEM VALUE`; S12 `setval` runs after M4; nothing assigns interrupt ids, so the identity default yields 1, 2, ... and collides with decision PKs. `migrate.id_map` (03c §3.12) records a mapping that is never defined. 2.1's "snapshot_id monotonic in ts (0 inversions)" is claimed for the new spine but cannot hold once 43k interrupts are appended after 207k decisions.
Fix: state the rule (e.g. `snapshot_id = 1,000,000,000 + interrupt_id`, or `setval` after M1 and insert in `ts` order recording the map); update Q-ST/turn_bounds text to say ordering is by `ts`, not id, across kinds.

## Major

**M1 -- 03a §3.4: NOT NULL on version-dependent collections.**
`char_state.trait_set_id` and `trait_progress_set_id` are `NOT NULL`, but M1 C lists `traits` and `trait_progress` in the v31+ block (absent on ~80% of lord/hero rows; sentinel/legacy versions have `emits_v31_block = false`). Either the migration fails NOT NULL or it coerces an empty set -- the exact silent coercion 2.5 forbids, and F7/F8 would flag it. Fix: move both to `char_state_ext`; add them to the 2.5 NULL-semantics list.

**M2 -- 03b §3.8 / 09 C.10: consumed key `panel.region` dropped for battle screens.**
`interrupt_model.py:63-92` reads `panel.region` on every kind (`isc_siege`, `isc_region_ours`, `isc_setts_in_province`); `launcher/interrupts.py:585` sets it in the pre_battle forecast. `interrupt_battle_panel` has no `region` column and 3.8/C.10 assign `interrupt.region_id` to the occupation panel only. F5's "retained keys" projection therefore loses a training feature. Fix: `interrupt.region_id` populated from any panel's `region` (state it), included in F5; drop the three unread battle columns (`settlement_captured`, `outcome`, `result_flag`) per C2.

**M3 -- 03a + 10 §10.4 S3/§10.7: `CREATE SCHEMA analytics` collides with the restored legacy schema.**
S2 restores `tw_stack` with its `analytics` schema; S3 runs 03a-03c under `psql -1 -v ON_ERROR_STOP=1`, whose first statements include `CREATE SCHEMA analytics` -> abort. 10.7 step 2 says the new tables were built under `analytics2`, but no DDL file says so. Fix: DDL and 08 use `analytics2` until cutover, or S3 renames the legacy schema first.

**M4 -- 10 §10.4 S6: parallel range workers corrupt `character.first_snapshot_id` and deadlock on value rows.**
The upsert is `ON CONFLICT (campaign_id, cqi) DO UPDATE SET last_snapshot_id = GREATEST(...)`; with SKIP LOCKED claiming, a later worker may process a lower range after a higher one, leaving `first_snapshot_id` = the higher id (I3 fails). Each range is one ~10 s transaction holding uncommitted speculative inserts on `state_set`, `dict.*`, `character`; starting states repeat across campaigns (M2 D: 2,118 world blobs span campaigns), so other workers block on those rows for the transaction lifetime and deadlock when two hold each other's sets -> range failures and retries thrash. Fix: `first_snapshot_id = LEAST(...)`; insert sets/dictionaries/characters in short separate transactions before the range transaction (V6 already tolerates orphan sets); sort value inserts by hash.

**M5 -- 07 §7.4-7.5: kill semantics of U4-U7 depend on an `rpc_response` row of unspecified transaction placement.**
The replay guard is `SELECT 1 FROM rpc_response WHERE req_id = $req_id`, and the cursor resumes from `MAX(rpc_id)` of responses. If the response INSERT is issued after the unit's COMMIT (as today's `journal.respond` is), a kill in between replays the request and double-inserts an interrupt/diplomacy/postmortem/ucb row (none has a natural key; K5 would fail at that kill point). Fix: the response INSERT is the last statement inside every unit transaction; say so in 7.2 and test it in K1 with a kill point between them.

**M6 -- 10 §10.4 S1: service account and data-directory ACLs are not handled.**
`pg_ctl register` without `-U` runs the service as `NT AUTHORITY\NetworkService`; `initdb` run by the admin user creates `D:\pg17\data` with that user's ACL. Whether `net start` succeeds depends on D:'s inherited ACL -- unstated. `copy migrate\postgresql.conf` over the generated file also discards initdb's `lc_*`/`timezone`/`default_text_search_config` lines. Fix: `icacls D:\pg17 /grant "NT AUTHORITY\NetworkService":(OI)(CI)F /T` before `net start`; ship the overrides as `include 'tw.conf'` or `conf.d`.

**M7 -- 03c §3.15 / 03b §3.7: `taken` updates are not HOT.**
U3 changes `counted` and `refusal_id`, which are INCLUDE columns of `taken_campaign` and `taken_action` (and sometimes `action_id`, the key of `taken_action`); any indexed-column change disables HOT regardless of fillfactor. Every decision writes two heap versions and four new index entries into `taken`. Fix: drop `counted/refusal_id` from the INCLUDE lists, or insert `taken` once at verification and keep the "awaiting" placeholder as `decision.taken_state`.

**M8 -- 04 §4.6-4.7: the swap renames away tables the design keeps in `ref`.**
`ref.manifest`, `ref.table_meta`, `ref.column_meta`, `ref.loc` and the new derived `ref.region_geometry` (written by `build_map_geometry.py`, keyed by `dict.region`) live in the schema that becomes `ref_prev` and is dropped. The manifest workaround ("rows copied into the new schema's table before the swap") is fragile and `region_geometry` is simply lost on every rebuild. Fix: metadata/manifest/geometry in a stable schema (`ops` or `dict`); rename only the pack-table schema.

**M9 -- 10 §10.4-10.5: `campaign` aggregates are never populated by the migration.**
M0 copies campaigns, M1/M2 COPY rows; no stage computes `first_*`, `peak_*`, `n_decisions/n_taken/n_counted/n_interrupts`, `turns`, `first/last_snapshot_id`, `outcome_id/defeated`, `ucb_pick_id`, yet V5 and every A2/A6/A11 query depend on them. Fix: an explicit M6 stage (`UPDATE corpus.campaign c SET ... FROM (SELECT ... GROUP BY campaign_id) a WHERE a.campaign_id = c.campaign_id`) with its own checkpoint row, before V5.

## Minor

**m1 -- 06 §6.2 sizing arithmetic.** Ignores intra-row alignment padding and the 4 B line pointer: `offer` is 32 (header+bitmap) + 50 aligned = 88 + 4 = 92 B (claimed 80); its PK entry is 8 + MAXALIGN(10) + 4 = 28 B (claimed 24). Offer ≈ 2.3 GB heap + 0.77 GB PK; corpus total ≈ 5.6 GB, not 5.06. Conclusions unchanged.

**m2 -- 02 §2.1/§2.6 misquote M2.** "95.7% of rows single-use" is M2 B's *move share of `actions` rows* (rows/identity 3.27-4.50, i.e. the opposite); "identity tuple (entity_seq, action_id, slot_index) not unique, 12.1%" was measured *without* `slot_index` (M2 A.1), and `options.py:528-532` already dedups `(slot_index, key)`, so the identity with `slot_index` is unique -- which is what 2.6 needs.

**m3 -- 03b §3.6 `lord_pool_candidate`.** `trait_levels` has no source (M1: `traits` is a list of key strings, non-empty 9/52,383; `cqis` always '0', `units` always null, `ranks` always 0). Twelve columns and 241k rows mostly of constants; a `trait_ids SMALLINT[]` on `lord_pool_set_member` would do.

**m4 -- 05 Q-A4/Q-A6.** `DISTINCT ON (character_id) ... ORDER BY character_id, snapshot_id DESC` reads every `char_state` row of each character (~40) and keeps the first; it does not stop at the first index row. Latency claims survive; the plan description does not.

**m5 -- Internal inconsistencies.** 06 §6.5 says 31 GB peak with `wal_level = minimal`; 10 §10.6 says 46 GB and "no wal_level toggle". 03c §3.15 "100 GB ≈ 2 years" vs 06 §6.4 "≈ 9 months" (283 days at 336 MB/day). 2.4-d says `faction` is not repeated per snapshot; `snapshot_campaign.faction_id` is `NOT NULL`. `snapshot_campaign.turn` duplicates `snapshot.turn` without being listed in 2.4. 10.3 requires code committed before *migration*, 13 §6.1 commits after validation.

**m6 -- C2 discipline.** `interrupt_battle_panel.settlement_captured/outcome/result_flag` and `interrupt_option.context` are stored though R2 B5 lists them as unread; `metrics.trials.ruleset` is read by `TrialRow` (R3 B) but dropped without appearing in 8.5.

**m7 -- New fallback branches (C13/R6).** 04 §4.6 loc split "remaining 8.7% get tbl = stem, col = ''" (a defined rule is still a branch on failure to match); 09 §9.4 interrupt `version_id` "else the sentinel `legacy:meta1:pq1`"; 07 U5 synthetic `campaign` rows "with faction from the key prefix" for 65 diplomacy rows; 08 §8.2 `logs/events_stream.py` "or deleted if the owner drops the stream" delegates a decision.

**m8 -- 03a dictionaries.** Lord `stance` is the literal `'none'` when absent (R1 B.4) and `char_state.stance_id` is `NOT NULL REFERENCES dict.stance`; the non-reference `'none'` row is not seeded (C1 asks for explicit rows), it only appears via the writer's miss path.

**m9 -- Constraints vs source types.** `world_army.cqi INTEGER NOT NULL` but R1 B.3 has `cqi: int|None`; `char_state.reach_rays` CHECK `cardinality = 8` while `collect.py:806` drops non-digit ray values, so a short list fails the CHECK rather than being stored (2,911/2,911 in the sample, unguarded at the boundary).

**m10 -- 03a `snapshot_campaign_scalars`.** A covering index duplicating the PK on a 120 B row buys little over a heap fetch and costs 12 MB plus a fourth index tuple per snapshot; index-only scans also need an all-visible VM, which the append-only workload only reaches after autovacuum.

**m11 -- 07 §7.3 `dict.action` cache.** 700k-entry Python dict (≈ 56 MB) loaded at recorder start "in ≈ 1 s" is an ASSUMPTION not labelled as one; 10.6's "40k new coordinates/day" is inferred from `distinct key` per context kind (284,716 + 341,521 double-count coordinates shared by lords and heroes).

# Spot checks

| verdict | claim (design section) | note |
|---|---|---|
| confirmed | store_ms p50 103 / p90 219 ms; roundtrip p50 719 / p90 1276 (01 W1, R2 A1.2) | R2 A1.2 line 18 |
| confirmed | 120 offers, 25.7 actions, 4.66 blobs, 5.5 entities per decision (01, 06) | M2 A.1 (119.7) and M2 D |
| confirmed | skills 97.0%, stances 78.6% (7,523/226,467), relations 84.4% (22,350/164,224), move_tiles 4.8%, armies 47.1%, hostiles 73.6%, reach_chars 72.1/78.8 (03b §3.5) | change_rate.out lines 7-91 |
| confirmed | corpus projection 97.3M / 13.6M / 17.6M and the 3.44M set-member subtraction (06 §6.1) | change_rate.out line 114; 13.61 − 4.24 − 3.22 − 1.04 − 0.75 − 0.92 reproduces |
| confirmed | taken.stderr p50 1.3 KB / p95 6.9 KB / max 20.9 KB, 2.09 KB/row (03c §3.15, 06) | M2 C line 133 |
| confirmed | shared_buffers 128 MB, hit ratio 66.7%, 38% forced checkpoints, 132 GB WAL/4.8 d, DB 16,327 MB, data dir 28.22 GB, D: 1,771 GB free (10.2, 06, 11) | R6 lines 11-65, 324 |
| refuted | `effective_io_concurrency = 200` is a valid setting (10.2) | live server rejects it: "must be set to 0 on platforms that lack posix_fadvise()" |
| confirmed | `wal_compression = zstd` available on this build (10.2) | live `pg_settings.enumvals` = {pglz, lz4, zstd, on, off}; 17.10 msvc |
| refuted | `--locale=English_United_States.1252` (10.4 S1) | R6 §0: collation is `English_United States.1252` |
| confirmed | ON CONFLICT DO NOTHING burns identity values (basis of B2) | live: `actions` max(action_id) 5,962,665 vs 5,108,439 rows; M2 B "~854k burnt" |
| refuted | "UNIQUE key made 95.7% of rows single-use" (2.6) | M2 B: 95.7% is the move *share*; rows/identity 3.27-4.50 |
| refuted | "(entity_seq, action_id, slot_index) not unique, 12.1%" (2.1) | M2 A.1 counted DISTINCT without slot_index; options.py:528-532 dedups (slot_index, key) |
| confirmed | `sample_index` = list position; reach_rays/reach_max per character (A11, 2.4-g) | collect.py:809-812 `enumerate`; M1 line 73 |
| confirmed | `options.generate(record)` takes only the record; `_hero_subtype_types` reads the reference DB (2.6, A15) | options.py:264-274, 686-709 -- regeneration is reference-build dependent, drift outside the window is real |
| confirmed | `interrupt_model` reads `panel.region` for every kind (basis of M2) | interrupt_model.py:63-92; interrupts.py:585 sets it for pre_battle |
| confirmed | v31 = `0.1.48`, decision_id ≥ 166,585; 131,716 NULL-version decisions; traits/trait_progress in the v31 block (2.5; basis of M1) | M1 C lines 143-146 |
| confirmed | x = y = 65535 sentinel on 35 WB rows; `diplo_hostile_rows` = len(hostiles) 2070/2070 (2.9, 2.3) | M1 F line 181; M1 A.2 line 53 |
| confirmed | (faction, map) → faction_cqi over 128 pairs, 0 conflicts; 452/931 cqi reuse (2.1, 2.4-e) | M3 D lines 229-230 |
| confirmed | 0 duplicate keys in 1,520 tables (952 single / 568 composite); 2,450 relations at 100%, 3 at 99.89-99.92%, 323 absent targets, 17 self-refs, 2 non-unique targets; loc 241,972 rows / 235 files; decode 1.6 s / 2.7 s wall, 865,404 rows (04 §4.1-4.4) | decode_probe.txt 3, 123; R5 B-C, F |
| confirmed | COUNT(*) offers 0.33 s; 43,392 options_json decodes per menus request; loc LIKE 271 ms; target_series 27,772 rows 0.10-0.17 s; window 62,647 decisions / 62,267 taken, 2.9 ms/decision, 9 s / 2.1 GB; attributions 14,272 rows 2.5 s (01, 05) | R3 A.1, F.15; R2 A3.3, A5.4, E, F.37 |
| confirmed | R4 C.2 12,393/43,220 next-turn rows; C.5 166 interrupts / 78 campaigns; 20,236 pre_battle rows; interrupt rpc avg 346 KB (09, 06, 01 W5) | R4 lines 33, 105, 115; M2 C line 138 |
| unverifiable | COPY ≥ 300k rows/s, FK validation 20 ms each, dump/restore 15 min, hydrate 5 ms Python (10.6, 14) | labelled ASSUMPTION with an experiment each -- acceptable |
| unverifiable | "235 of the 373 .loc files" (04 §4.1) | R5 F gives 235 files; 373 not found in the lane |

# Strengths

- The workload catalogue is complete and honest (it admits L1 hydration gets slower and shows why it does not matter on a 719 ms round trip).
- Per-collection storage is decided by rule from the measured change rate and the numbers reproduce; ordinals never enter shared-set identity; the canonical typed encoding + SHA-256 with an explicit collision policy is exactly what C5 asked for.
- Collector versions are sentinelled by observed key signature with exact `emits_*` flags, and NULL/absent/empty is resolved per key path (2.8) without side tables.
- Interrupts on the spine with `state_at`, `prev_decision_id`, launcher-side capture, and the 346 KB -> 4 KB rpc payload fix are the right shape; each R4 defect gets a fix or a reasoned refusal.
- Regenerating offer params from state (verified: `generate` is record-only, `sample_index` is positional) removes 8.8 GB and the 741 MB UNIQUE index.
- Write path: one connection, one transaction per unit, `unnest` batches, uuid idempotency, kill table per unit, before/after counts.
- Verification is unusually concrete: byte-exact round trip of every blob, training-set matrix equality, API A/B, five kill tests, simplicity counters.

# Verdict

This is a strong, evidence-driven design at the schema and read/write-path level, and the candidate clearly understands the data (nearly every number we checked reproduces). It fails, however, at the point where a senior database engineer is paid to be careful: the operational steps. The very first owner-run command sequence would fail twice (an illegal `effective_io_concurrency` on Windows, an invalid locale string), the DDL cannot be applied onto the restored database (`analytics` collision), migrated interrupts have no primary-key assignment, and the dictionary identity type plus the ON CONFLICT miss path -- a pattern the candidate's own evidence lane measured burning 854k ids -- would exhaust SMALLINT sequences under six parallel workers. Add the parallel-range deadlock/`first_snapshot_id` problem, the unpopulated `campaign` aggregates, the NOT NULL on v31-only sets and the unstated transaction placement of the replay guard, and the migration as written does not complete. None of these is hard to fix, and the design's structure makes the fixes local; but "would not survive the first evening of execution" is not a senior-level submission. Recommendation: not hire at senior level on this artefact; strong hire at mid level, or re-review after a revision that addresses B1-B3 and M1-M9 with the same rigour the candidate applied to sizing and interrupts.
