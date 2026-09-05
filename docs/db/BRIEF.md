# Design brief: relational corpus, full reference import, cluster on D:

## What is being designed

A replacement for the Postgres schema of D:\tw_stack (a Total War: Warhammer III advisor). Today the per-decision game state is stored as plain-JSON text blobs (`public.blobs.z`, 949k rows, 2.4 GB, sha256-deduped, no compression) referenced from `decisions` (campaign + world blob) and `entities` (one features blob per lord/hero/province/campaign entity, ~5.5 per decision), plus JSON-in-text columns (`actions.params`, `taken.confirm_*`/`timing`/`diagnostics`, `decisions.timings`, `interrupts.options_json`, `postmortems.payload`, `diplomacy_events.payload`, `rpc_*.payload`, `app.game_event.payload`, ...). Every consumer decodes JSON in Python; the `analytics` schema exists mostly to re-parse blobs into columns. Game reference data is imported as 44 curated tables from 53 of the game's 1,521 pack tables, drop-and-recreate, no change detection. The cluster (PostgreSQL 17.10, 28 GB data dir) lives on C: (44 GB free); D: has 1.7 TB free.

## Requirements (owner's, paraphrased)

R1. Move the cluster to D:.
R2. Replace every blob and blob-like structure with understandable, efficient relational tables. The table design must be justified on logical grounds (identity, keys, functional dependencies, normal form, deliberate denormalisations) and on performance grounds (the measured workload, indexes, sizes, growth).
R3. Import the reference DB completely and properly: every pack table lands in our DB, rebuilt whenever a change in the game data is detected. Not one table at a time.
R4. Every file that reads or writes the DB is suspect: bloat, outdated access patterns (sqlite-era idioms, per-call connections, DDL on connect, JSON decode in Python, correlated subqueries over the corpus), things that no longer make sense against a relational schema with current tooling. Each gets a verdict and, where kept, a concrete change.
R5. Interrupts handled correctly: (a) the game's interrupt records modelled properly in the schema and their known defects fixed; (b) the writers and the migration are interruption-safe (kill mid-write leaves a defined state; migration resumes from a checkpoint).
R6. Project rules: no comments/docstrings in code; simplicity; no fallbacks (a fallback branch is a defect to be removed, not a feature); timing logs at function entry/exit; the plan has numbered sections executed strictly in order; anything blocked on the owner is pinned at the top of every status report.

## Evidence base (read before designing; cite lane + section when you use a fact)

Files under C:/Users/trist/AppData/Local/Temp/claude/D--tw-stack/503ea3b2-0e04-45e7-b92b-dfb232c06b94/scratchpad/research/:
- R1_write_path.md -- the complete record shape (every key per blob role with Lua source and Python type), every writer, transaction boundaries, failure semantics, process topology.
- R2_live_ml_readers.recovered.md -- every read on the live loop and the training paths, the consumed-key matrix, measured latencies (round trip p50 719 ms; read_decision 2-9 ms; training window read ~9 s / 2.1 GB JSON; retrain ~13 min).
- R3_api_analytics_readers.recovered.md -- every API query with UI cadence, analytics tenants and their consumers (19 of 29 analytics tables have no consumer), dead code, re-derivations, measured query times.
- R4_interrupts_side_tables.recovered.md -- interrupt lifecycle, as-is data model, 16 observed defects (C.1-C.16), diplomacy/postmortem/ucb/rpc tables, interrupt-model field matrix.
- M3_key_resolution.md -- every identifier family resolves ~100% against vanilla db.pack (exceptions: `rebels`, `ruins`, a truncated faction key, dilemma-id artefacts); which paths hold display names instead of keys; loc coverage.
- R5_reference_import.md, R6_infra_stats.md, M1_blob_profile.md, M2_flat_tables_profile.md -- reference schema/pack facts, server config + statistics + growth, blob data profile (types, nulls, order semantics, drift), flat-table and params profile. (If any of these is missing, say so and design from the others; do not invent numbers.)
- decode_probe.txt, type_census.txt -- all 1,521 pack tables decode in 2.7 s (1,520 ok; 865,404 rows; 93 MB raw); field-type census over 6,850 columns.
- Prior-session change-rate measurement (real, 8% campaign sample): C:/Users/trist/AppData/Local/Temp/claude/D--tw-stack/933238e8-241e-4cbf-b732-4a62797fde65/scratchpad/change_rate.out -- per collection: plain rows vs deduplicated-set rows vs as-of (SCD) rows, same% between consecutive snapshots. Corpus projection: plain 97.3M rows, dedup 13.6M, SCD 17.6M.

Rules for using evidence: do not re-research the code (you may open a file to check a specific line you rely on). Numbers in the design must trace to a lane section or to arithmetic you show. Anything not covered by evidence is marked ASSUMPTION with what would verify it.

## Hard constraints the design must satisfy

C1. Integer surrogate keys for every game identifier family (skill, unit, building, tech node/technology, region, province, faction, agent subtype, agent type, ancillary, ritual, ritual chain, trait, trait level, mission, provincial initiative/edict, effect bundle, pooled resource, stance, agent action, captive option, dilemma/incident, ...) held in one dictionary per family that references the imported reference table row. Pseudo-values (`rebels`, `ruins`) and unresolved artefacts get explicit dictionary rows flagged as non-reference, not NULLs.
C2. No JSON/JSONB column holds game state or anything a reader parses. Operational payloads (rpc, postmortem, diagnostics stderr, game_event) get an explicit per-column decision: relational if read; dropped if unread; a plain text column is fine for opaque text (a stderr string is a value, not a structure).
C3. Column types come from the data profile: integer where the corpus is always integral AND the meaning is a count/level/rank/index/id; real or double for measures; boolean for booleans; text only for free text. The collector's float/int instability is normalised at the boundary (collector or store), once, with the rule stated.
C4. Per-collection storage is chosen from the measured change rate, not uniformly: collections that are identical between consecutive snapshots >90% of the time (skills, tech, rites, lord_pools, slot_states, buildable, edicts, locked_slots, hidden_skills, horde_slots, recruitable, ...) are stored once per distinct set (deduplicated set + membership) or as as-of versioned rows; collections that change nearly every snapshot (move_tiles, armies, hostiles, reach_chars/setts, relations, unit_cards) are stored per snapshot in the narrowest form that answers the workload (rows, or arrays for pure coordinate lists). State the rule and apply it per collection with the numbers.
C5. Positional-only collections (unit_cards, pending_queue, equipped, armory, anc_pool, lord_pools lists) keep their ordinal in the membership row; an ordinal is never part of a shared, deduplicated tuple's identity. Set identity is a full-width hash (state width and collision policy) over a canonical, typed encoding (not JSON text).
C6. One snapshot spine: decisions and interrupts are both snapshots of the same state shape; interrupts link to the preceding decision and to their own captured state; the semantic question "which state does an interrupt row carry" (before or after the click; R4 C.2/C.3) is resolved explicitly and encoded.
C7. Every snapshot carries a NOT NULL collector version (historical rows with unknown version get a sentinel version row); key presence by version (M1 C) is documented so NULL means "not observed" only where the collector of that version did not emit the key.
C8. Null vs missing vs empty: for each key path where the data shows more than one of these states (M1 B), the design states what each means and which column/row encoding preserves the distinction the readers need; no per-row key-set/null-set side tables.
C9. Write path: one transaction per recorded decision (snapshot + entities + collections), explicit rollback on error, one persistent connection per process with application_name and reconnect, no DDL on connect, no transient connections per decision, idempotency via a client-generated decision uuid/rpc id so a retried request cannot double-insert, and every store method logs timing on entry/exit. Kill semantics stated per table.
C10. Reference import: all pack tables from every pack that carries db tables (load order respected), typed from the RON schema with the type mapping justified by the type census, PK from is_key where the data proves uniqueness (else a surrogate + a documented non-unique key), FKs from is_reference only where the data resolves ≥99.9% (else documented), loc as a table keyed by (table, column, key); versioned by a manifest (pack sha256 + size + mtime, schema hash, game exe version) so a rebuild happens exactly when a change is detected and is skipped otherwise; the build is transactional (build into a new schema/table set and swap, never drop-then-fill); consumers (features_db, catalogue.dense_ids, labels.py, collect._merc_flavors, the live-game gameref tables) repointed or retired. State the expected build time from the probe.
C11. Migration: runs against a copy or a new database on D:, batched by decision_id ranges with a persisted checkpoint table, resumable after a kill, idempotent per range, never an in-place UPDATE on a multi-GB table (new table + swap), disk headroom computed, throughput measured (M1 E / M2) and total time computed from it; validation is through-the-database: hydrate every migrated snapshot back to the canonical record dict and compare with the original blob's canonical JSON over the whole id range (100% required), plus row-count and FK checks; cutover with the stack down (runctl down + HARNESS_OFF), code committed before cutover, old tables kept until validation passes, rollback = repoint to old database.
C12. The analytics rebuild trap (analytics/store.py drops and rebuilds tenants when watermarks/row counts disagree) is neutralised before any id moves; unused analytics tables are dropped, not migrated.
C13. Simplicity: fewer moving parts than today (count tables, connections, statements per decision, processes; show before/after). No wide sparse tables (>~40 columns of which most are NULL); no table-per-key sprawl for operational payloads. Every fallback branch met on the touched paths is listed for removal.

## Required deliverable structure (use these headings; a missing section is a failing design)

1. Workload catalogue: every access path with trigger, frequency, rows, current measured latency, latency budget, and the question it answers (from R2/R3/R4). This is what the schema is derived from.
2. Logical model: entities, identities, temporal model, keys, FDs, normal form per relation, deliberate denormalisations with reasons.
3. Physical DDL: complete CREATE TABLE/INDEX statements for every table (corpus, dictionaries, reference manifest/loc, operational), types justified per column class, constraints, per-collection storage decision with the change-rate numbers, partitioning/retention decisions, fillfactor where updates occur.
4. Reference import design: schema layout (one table per pack table? naming), type mapping table, keys/FKs policy with numbers, manifest and change detection, build procedure and timing, loc, consumer repointing, retirement of gameref live-capture tables.
5. Query walk-throughs: for each hot path in (1): the SQL on the new schema, the index it uses, estimated rows touched, expected latency vs today.
6. Sizing: per table rows and bytes (tuple header 24 B + alignment + per-column widths + index sizes) from measured cardinalities; totals vs today's 15 GB; growth/day at measured ingest; 6/12-month projection.
7. Write path: per-decision transaction script (statement list), dictionary lookups strategy, expected latency vs today's store_ms p50 103 ms, kill semantics, idempotency, connection lifecycle.
8. Reader plan: per-file verdict table (keep / rewrite / delete, reason) for every DB-touching file (R1/R2/R3/R4/R6 list them); how ML training and the live loop hydrate (SQL → record dict adapter vs direct columns), API sites that stop decoding JSON, analytics tables dissolved.
9. Interrupts: the corrected model and the fix for each of R4 C.1-C.16 (or an explicit "not fixed, because").
10. Migration and cluster move: exact ordered steps, commands, checkpointing, validation, cutover, rollback, disk and time budget.
11. Cleanup inventory: leftovers to drop (databases, schemas, tables, files) -- listed for the owner's confirmation, not executed silently.
12. Verification plan: tests and benchmarks with pass thresholds (round-trip 100%, hot-path latencies, write latency, training-set equality on a window, API A/B equality).
13. Execution plan: numbered sections and subsections in strict order, each with its done-criterion.
14. Assumptions and open questions: only things the evidence cannot settle; each with the cheapest experiment that settles it. No design decision may be delegated to the owner as a choice.

## Scoring rubric (judges score 0-5 each with evidence)

S1 Query-derived: every table and index traces to catalogued queries; walk-throughs are concrete and plausible against Postgres 17 planner behaviour.
S2 Keys and integrity: candidate keys, FDs and normal forms stated and correct; constraints supported by the measured data (null rates, resolution %).
S3 Temporal/identity model: decisions and interrupts unified; entity identity across snapshots defined; state-at-time-T answerable.
S4 Measured sizing: arithmetic shown from measured cardinalities; growth and retention decided.
S5 Write path: single-transaction, kill-safe, idempotent, connection-disciplined, latency reasoned.
S6 Reader coverage: consumed-key matrix fully mapped to columns; nothing the ML/API/analytics reads is dropped; JSON decoding gone from hot paths.
S7 Reference import: complete, typed, keyed, versioned with change detection, transactional, consumers repointed, timing stated.
S8 Interrupts: model correct; each R4 defect addressed; migration/writers interruption-safe.
S9 Migration: throughput-based time, resumable, validated through the DB, cutover/rollback concrete, disk computed.
S10 Simplicity and project rules: fewer parts; no fallbacks; no sprawl; timing logs; numbered sections.
S11 Evidence discipline: claims cite lanes; no invented numbers; assumptions labelled.
S12 Coherence with requirements R1-R6 as written above, nothing delegated back to the owner.
