# 5. Query walk-throughs

Planner assumptions: PostgreSQL 17 on the D: cluster configured per 10 §10.2 (`shared_buffers` 8 GB, `random_page_cost` 1.1, `effective_cache_size` 32 GB, `work_mem` 64 MB) so the corpus (06: ≈ 4.5 GB after migration) is memory-resident; NVMe reads for cold pages ≈ 0.1 ms. Latencies are estimates from index shape and row counts; 12 measures them with pass thresholds. Parameters are `%s` placeholders (psycopg 3).

## Q-L1 Hydrate one decision (live loop, 01 L1; training T3/T7 use the same statements over a range)

One round trip: a `psycopg` pipeline (or a single `SELECT` returning multiple result sets via a set-returning SQL function) issuing the statements below with `$1 = snapshot_id` (range variant: `snapshot_id BETWEEN $1 AND $2`, ordered by `snapshot_id`).

```sql
SELECT s.snapshot_id, s.campaign_id, s.ts, s.turn, s.version_id, c.campaign_key, c.faction_id, v.emits_campaign_meta, v.emits_pending_queue, v.emits_v31_block, v.emits_missions
FROM corpus.snapshot s JOIN corpus.campaign c USING (campaign_id) JOIN corpus.collector_version v USING (version_id) WHERE s.snapshot_id = $1;
SELECT * FROM corpus.snapshot_campaign WHERE snapshot_id = $1;
SELECT * FROM corpus.snapshot_world WHERE snapshot_id = $1;
SELECT * FROM corpus.world_army WHERE snapshot_id = $1 ORDER BY ord;
SELECT * FROM corpus.world_hostile WHERE snapshot_id = $1 ORDER BY ord;
SELECT * FROM corpus.snapshot_entity WHERE snapshot_id = $1 ORDER BY entity_seq;
SELECT cs.*, ch.cqi FROM corpus.char_state cs JOIN corpus.character ch USING (character_id) WHERE cs.snapshot_id = $1 ORDER BY entity_seq;
SELECT * FROM corpus.char_state_ext WHERE snapshot_id = $1;
SELECT * FROM corpus.province_state WHERE snapshot_id = $1 ORDER BY entity_seq;
SELECT * FROM corpus.campaign_state WHERE snapshot_id = $1;
SELECT * FROM corpus.snapshot_read_failure WHERE snapshot_id = $1;
```
Then, for the set ids collected from those rows (≈ 60 per decision: 8 world + 5.5 entities × ~10) one statement per member table with `set_id = ANY($ids) ORDER BY set_id, ord` (≤ 22 statements, most with 1-6 ids); the adapter keeps a process LRU of hydrated sets keyed by `set_id` (sets repeat across consecutive decisions 93-99% of the time, change_rate.out), so on the live loop the member statements fire only for new set ids (≈ 5 per decision).

Index use: every statement is a PK/unique range (`snapshot_id` leading) — `Index Scan` on the PK with ≤ 14 rows; member tables `Index Scan` on `(set_id, ord)` returning 50-80 rows for skills/tech. Rows touched: ≈ 1 + 1 + 1 + 4.7 + 8.1 + 5.5 + 2.8 + 0.6 + 1.9 + 1 + ≈ 60 set headers (cached) + ≈ 300 member rows on a cold set miss. Expected: 11 pipelined statements ≈ 1.5 ms server + 22 member statements ≈ 3 ms + Python assembly of the record (≈ 1,000 dict/list objects) ≈ 3 ms → **≈ 8 ms**; today 1.7-8.6 ms fetch + json.loads of 42 KB ≈ 0.4 ms (R2 A1.3-4, M1 E). Slightly slower per decision on the live loop than a single 42 KB text fetch is accepted: it is < 1.5% of the 719 ms round trip, and it removes the 0-row offers query and every JSON decode. Range variant for training: 62,647 decisions → the PK scans become sequential index ranges; server ≈ 0.4 ms/decision, Python assembly ≈ 5 ms/decision (ASSUMPTION; 12 measures) → 5.4 min single-threaded, ≤ 1.5 min with 4 range workers (01 T3 budget 6.5 min).

## Q-W1 Write one decision (07 lists the statement script)

Set lookup, batched per member kind:
```sql
INSERT INTO corpus.state_set (kind, hash, n) SELECT k, h, n FROM unnest($1::smallint[], $2::bytea[], $3::smallint[]) AS t(k, h, n)
ON CONFLICT (kind, hash) DO NOTHING RETURNING set_id, kind, hash;
SELECT set_id, kind, hash FROM corpus.state_set WHERE (kind, hash) IN (SELECT * FROM unnest($1::smallint[], $2::bytea[]));
```
Index: `state_set (kind, hash)` unique — one B-tree probe per set (≈ 60 per decision, 0.02 ms each in cache). Member inserts only for the returned (new) ids: `INSERT INTO corpus.skill_set_member SELECT * FROM unnest(...)` — with 3-8% dedup ratios, ≈ 3-5 new sets per decision × ≈ 40 members. Row inserts for the snapshot (≈ 25 rows across 10 tables) + offers later. Expected transaction: ≈ 40 statements, ≈ 350 index tuples inserted, WAL ≈ 60 KB → **≈ 25-45 ms** with `synchronous_commit = off` (today store_ms p50 103 ms including 3 transient connections and 39 DDL statements, R1 G/H.4).

## Q-ST State at time T for campaign C (S3)

```sql
SELECT snapshot_id, kind_id, ts, turn FROM corpus.snapshot
WHERE campaign_id = $1 AND ts <= $2 ORDER BY ts DESC, snapshot_id DESC LIMIT 1;
```
Index `snapshot_campaign_ts (campaign_id, ts, snapshot_id)`: backward index scan, 1 row, < 0.1 ms; then Q-L1 by the returned id. Same query with `kind_id = decision` gives the "state the policy last saw" for an interrupt (`prev_decision_id` is stored precisely to avoid running it per row in training).

## Q-T1 `target_series` — turn_open scalars, whole corpus (01 T1)

```sql
SELECT b.campaign_id, b.turn, sc.income, sc.settlements, sc.allies, sc.vassals, sc.power_rank, sc.lord_level
FROM (SELECT campaign_id, turn, MIN(snapshot_id) AS open_id FROM corpus.snapshot WHERE kind_id = $decision GROUP BY campaign_id, turn) b
JOIN corpus.snapshot_campaign sc ON sc.snapshot_id = b.open_id;
```
Plan: `Index Only Scan` on `snapshot_campaign_turn (campaign_id, turn, snapshot_id) INCLUDE (kind_id)` (206,907 + 43,392 entries, ≈ 8 MB) with `GroupAggregate` (index order matches GROUP BY) → 27,772 groups; nested-loop `Index Only Scan` on `snapshot_campaign_scalars` for the 27,772 open ids. Rows: 250k index entries + 27.8k probes. Expected **≈ 40-60 ms**; today 100-170 ms via the `turn_bounds` view over the heap (R2 A5.4).

## Q-T2 `prebattle_attributions` (01 T2), windowed

```sql
SELECT i.interrupt_id, i.prev_decision_id, t.decision_id, t.action_id, a.action_key, s.ts, ib.enemy_cqi, ib.result_state, ib.casualties_text, i.chosen
FROM corpus.interrupt i
JOIN corpus.snapshot s ON s.snapshot_id = i.interrupt_id
JOIN LATERAL (SELECT t.decision_id, t.action_id FROM corpus.taken t JOIN corpus.snapshot ds ON ds.snapshot_id = t.decision_id
              WHERE t.campaign_id = s.campaign_id AND ds.ts <= s.ts AND (t.refusal_id IS NULL OR t.refusal_id NOT IN ($awaiting, $died))
              ORDER BY t.decision_id DESC LIMIT 1) t ON true
JOIN dict.action a ON a.action_id = t.action_id
LEFT JOIN corpus.interrupt_battle_panel ib ON ib.interrupt_id = i.interrupt_id
WHERE i.kind_id = $pre_battle AND i.counted AND a.action_type_id IN ($attack_army, $attack_settlement)
  AND s.ts - (SELECT ts FROM corpus.snapshot WHERE snapshot_id = t.decision_id) <= 120
  AND s.campaign_id = ANY($campaign_ids);
```
Indexes: `interrupt_kind (kind_id, interrupt_id)` → 20,236 pre_battle rows (or the campaign subset), `taken_campaign (campaign_id, decision_id)` backward scan for the lateral (≤ 3 rows read per interrupt), `dict.action` PK, `interrupt_battle_panel` PK. The target zone (`hostiles[].cqi → province` and `regions[].region → province` today via `bw.z::jsonb`, R2 A5.5) becomes `SELECT province_id FROM corpus.world_hostile WHERE snapshot_id = $prev AND cqi = $target_cqi` (PK range 8 rows) — resolved in Python per attribution or as another LATERAL. Rows: 14,272 attributions × ≈ 5 index probes. Expected **≈ 150-250 ms** whole corpus, < 50 ms for a 1000-campaign window; today 2.54 s.

## Q-T5 `interrupt_rows` for training (01 T5), windowed

```sql
SELECT i.*, s.campaign_id, s.ts, s.turn, c.campaign_key, ib.*, id.*
FROM corpus.interrupt i JOIN corpus.snapshot s ON s.snapshot_id = i.interrupt_id JOIN corpus.campaign c USING (campaign_id)
LEFT JOIN corpus.interrupt_battle_panel ib USING (interrupt_id) LEFT JOIN corpus.interrupt_diplo_panel id USING (interrupt_id)
WHERE s.campaign_id = ANY($window_campaign_ids) AND i.chosen IS NOT NULL ORDER BY i.interrupt_id;
SELECT interrupt_id, ord, option_key, text, option_id, payload, exploit FROM corpus.interrupt_option
WHERE interrupt_id = ANY($ids) ORDER BY interrupt_id, ord;
```
Plan: `snapshot_campaign_ts` per campaign (1000 probes) → ≈ 12,044 interrupts; PK joins; option rows ≈ 2.26 × 12k = 27k. Expected **≈ 100 ms**; today 5.7 s decoding 383 MB of world text (R2 A5.14).

## Q-A1 `/api/run` `current`

```sql
SELECT s.snapshot_id, s.turn, s.ts, s.campaign_id, c.campaign_key, c.leader, c.faction_id, c.campaign_map_id, sc.settlements, sc.power_rank, sc.lord_level
FROM corpus.snapshot s JOIN corpus.campaign c USING (campaign_id) JOIN corpus.snapshot_campaign sc USING (snapshot_id)
WHERE s.kind_id = $decision ORDER BY s.snapshot_id DESC LIMIT 1;
SELECT COUNT(*) FILTER (WHERE n_decisions >= 2), SUM(n_decisions), SUM(n_taken), SUM(n_counted) FROM corpus.campaign;
SELECT n_decisions, first_ts FROM corpus.campaign WHERE campaign_id = $1;
```
Plan: backward PK scan on `snapshot` (≤ 0.21 interrupt rows skipped per decision), 2 PK lookups; `campaign` seq scan of 4,827 rows (≈ 1 MB) for the totals — replaces `COUNT(*) FROM offers` (24.8M rows, 0.33 s; R3 A.1) and the campaign-blob decode. `collect_timing`/`cycle_timing`: `SELECT ... FROM corpus.decision_timing ORDER BY decision_id DESC LIMIT 400` (backward PK scan, 400 rows, no JSON). Expected **≤ 10 ms** for the whole route; today ≈ 0.5 s.

## Q-A2 `/api/campaigns` (`campaign_rows`)

```sql
SELECT c.*, f.key AS faction, m.key AS campaign_map, o.key AS outcome, e.when_text, e.error, e.verdict, e.growth_reason,
       (SELECT pick_id FROM corpus.ucb_pick p WHERE p.pick_id = c.ucb_pick_id) AS pick_id
FROM corpus.campaign c JOIN dict.faction f ON f.id = c.faction_id LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id
LEFT JOIN dict.enum o ON o.enum_id = c.outcome_id LEFT JOIN corpus.campaign_ending e USING (campaign_id)
ORDER BY c.first_ts DESC NULLS LAST;
```
Plan: seq scan `campaign` (4,827 rows) + hash joins to 3 tiny dictionaries; `campaign_ending` view = one `postmortem` index probe per campaign (`postmortem_campaign`). Rows ≈ 4,827 × 5. Expected **≈ 15 ms**; today 9 queries incl. two whole-corpus GROUP BYs (0.04 + 0.03 s) + `campaign_gains` (0.07 s) + `campaign_growth` + `outcome_join` twice + the cache-key collision (R3 §0, A.2). `ucb_pick` join replaces the 120 s timestamp-window pairing (`pick_campaigns`, R3 F.8): `campaign.ucb_pick_id` is written at W9 from the `ucb_pick` rpc that immediately precedes the campaign (07).

## Q-A4 `/api/campaigns/{key}/state`

```sql
SELECT cs.current_research_id, cs.equipped_all_set_id, cs.anc_pool_set_id
FROM corpus.campaign_state cs JOIN corpus.snapshot s USING (snapshot_id)
WHERE s.campaign_id = $1 ORDER BY cs.snapshot_id DESC LIMIT 1;
SELECT DISTINCT ON (cs.character_id) cs.character_id, ch.cqi, cs.is_leader, cs.region_id, cs.hp, cs.rank, cs.wounded, cs.skill_points
FROM corpus.character ch JOIN corpus.char_state cs USING (character_id)
WHERE ch.campaign_id = $1 AND NOT cs.is_hero ORDER BY cs.character_id, cs.snapshot_id DESC;
```
Plan: `snapshot_campaign_ts` backward scan for the first (≤ 1.2 rows skipped); second: `character (campaign_id, cqi)` unique index → ≈ 3 characters, each an `Index Scan` on `char_state_character (character_id, snapshot_id DESC)` whose group is read in full by `DISTINCT ON` (m4). Expected **≈ 1 ms**; today one DISTINCT ON over the campaign's entities with blob decode (R3 A.2).

## Q-A6 `_start_snapshots` (latest lord/hero per character for a start, 283 campaigns)

```sql
SELECT DISTINCT ON (cs.character_id) ch.campaign_id, ch.cqi, cs.subtype_id, cs.rank, cs.skill_points, cs.skill_set_id
FROM corpus.campaign c JOIN corpus.character ch USING (campaign_id) JOIN corpus.char_state cs USING (character_id)
WHERE c.campaign_map_id = $1 AND c.faction_id = $2 ORDER BY cs.character_id, cs.snapshot_id DESC;
SELECT set_id, skill_id, level, tier, total_levels FROM corpus.skill_set_member WHERE set_id = ANY($skill_set_ids) ORDER BY set_id, ord;
```
Plan: `campaign_faction_map` → 283 campaigns → `character (campaign_id, cqi)` → 835 characters → `char_state_character` first row each (835 probes); then ≤ 835 distinct skill sets (fewer: skill sets repeat across campaigns of the same start) × 50 rows via the member PK. Rows ≈ 835 + 40k. Expected **≈ 30 ms**; today 0.25 s + 8.15 MB JSON decode (R3 A.3).

## Q-A8 `_positions_data` (conditions over every decision's campaign scalars)

```sql
SELECT s.snapshot_id, s.campaign_id, s.turn, sc.settlements, sc.income, sc.power_rank, sc.lord_level, sc.allies, sc.vassals,
       sc.treasury, sc.armies, sc.is_researching, sc.ll_wounded, sc.resource_set_id, sc.hero_count_set_id
FROM corpus.snapshot s JOIN corpus.snapshot_campaign sc USING (snapshot_id) WHERE s.kind_id = $decision;
SELECT set_id, resource_id, value FROM corpus.resource_set_member WHERE set_id IN (SELECT DISTINCT resource_set_id FROM corpus.snapshot_campaign) ORDER BY set_id;
```
Plan: merge/hash join of two 207k-row tables (≈ 25 MB heap), plus the distinct resource sets (dedup ratio 8% → ≈ 17k sets × 5.4 rows ≈ 90k rows). Expected **≈ 0.3 s**; today ≈ 5 s decoding every campaign blob via a merge join over the whole `blobs_pkey` (R3 A.5, G). The Python side keeps its in-memory `decs` tuples and now looks resources up by `set_id`.

## Q-A9 `/api/decisions` page, `/api/decisions/{id}`

```sql
SELECT t.decision_id, t.ts, e.key AS entity_kind, se.character_id, se.region_id, at.key AS action_type, a.action_key, t.executed, t.confirmed, t.counted, r.key AS refusal, t.latency_ms,
       t.decision_id * 1048576 + t.offer_seq AS offer_id, p.key AS policy, c.campaign_key, s.turn, d.n_offers
FROM corpus.taken t JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id JOIN corpus.decision d ON d.decision_id = t.decision_id
JOIN corpus.campaign c USING (campaign_id) JOIN dict.action a USING (action_id) JOIN dict.action_type at ON at.id = a.action_type_id
LEFT JOIN corpus.snapshot_entity se ON (se.snapshot_id, se.entity_seq) = (t.decision_id, t.entity_seq) LEFT JOIN dict.enum e ON e.enum_id = se.kind_id
JOIN dict.enum p ON p.enum_id = t.policy_id LEFT JOIN dict.enum r ON r.enum_id = t.refusal_id
ORDER BY t.decision_id DESC LIMIT $limit OFFSET $offset;
SELECT o.offer_seq, o.entity_seq, at.key, a.action_key, o.exploit, o.pct_global, o.rank, o.gnn_impact, o.gnn_rank, o.ggnn_score, o.ggnn_rank
FROM corpus.offer o JOIN dict.action a USING (action_id) JOIN dict.action_type at ON at.id = a.action_type_id
WHERE o.decision_id = $1 ORDER BY COALESCE(o.rank, 9999), o.offer_seq;
```
Plan: backward PK scan on `taken` (200 rows) with nested-loop PK probes; detail: `offer` PK range (≈ 120 rows). Facets: `SELECT DISTINCT action_type_id FROM dict.action a WHERE EXISTS (SELECT 1 FROM corpus.taken t WHERE t.action_id = a.action_id)` — replaced by a `GROUP BY action_type_id` over `taken_action` index (index-only, 205k entries) ≈ 20 ms; today 0.37 s. The entity detail (`EntityState.features`) uses the Q-L1 adapter for one decision. Expected page **≤ 15 ms**, detail **≤ 20 ms**.

## Q-A10 `/api/decisions/menus` — interrupt coverage

```sql
SELECT k.key AS kind, COUNT(*) AS n, COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM corpus.interrupt_option o WHERE o.interrupt_id = i.interrupt_id AND o.exploit IS NOT NULL)) AS scored
FROM corpus.interrupt i JOIN dict.enum k ON k.enum_id = i.kind_id GROUP BY k.key;
```
Plan: seq scan `interrupt` (43k rows, ≈ 4 MB) + semi-join on `interrupt_option` PK (98k rows). Expected **≈ 40 ms**; today decodes 43,392 `options_json` texts per request (R3 A.6). The last-60 list is a backward PK scan with a PK-range join to `interrupt_option`.

## Q-A12 `/api/campaigns/picks/{id}`

```sql
SELECT * FROM corpus.ucb_pick WHERE pick_id = $1;
SELECT * FROM corpus.ucb_pick_row WHERE pick_id = $1 ORDER BY rank;
```
PK lookups, 130 rows, **< 1 ms**; today rebuilds the whole 3,853-pick series to find one head (R3 A.8, F.9).

## Q-U3 `acquisition` incremental fold (research family shown; skills/traits/items/building/settlement follow the same shape over their member tables)

```sql
WITH new_snaps AS (SELECT s.snapshot_id, s.campaign_id, s.turn FROM corpus.snapshot s WHERE s.snapshot_id > $watermark AND s.snapshot_id <= $hi AND s.kind_id = $decision),
seen AS (SELECT ns.campaign_id, m.tech_node_id AS key_id, ns.snapshot_id, ns.turn, m.researched
         FROM new_snaps ns JOIN corpus.campaign_state cs USING (snapshot_id) JOIN corpus.tech_set_member m ON m.set_id = cs.tech_set_id)
INSERT INTO analytics.acquisition AS a (campaign_id, family, key_id, ctx, first_seen_snapshot, first_seen_turn, acquired_snapshot, acquired_turn)
SELECT campaign_id, 'research', key_id, '', MIN(snapshot_id), MIN(turn), MIN(snapshot_id) FILTER (WHERE researched), MIN(turn) FILTER (WHERE researched)
FROM seen GROUP BY campaign_id, key_id
ON CONFLICT (campaign_id, family, key_id, ctx) DO UPDATE SET acquired_snapshot = LEAST(a.acquired_snapshot, EXCLUDED.acquired_snapshot), acquired_turn = LEAST(a.acquired_turn, EXCLUDED.acquired_turn)
WHERE a.acquired_snapshot IS NULL AND EXCLUDED.acquired_snapshot IS NOT NULL;
```
Plan: PK range on `snapshot`, PK probes on `campaign_state`, PK range on `tech_set_member` (58 rows per distinct set; the join fans out to 58 × decisions but dedup means the planner hashes ≈ 4% distinct sets). For a 5,000-decision step: 290k member rows scanned, ≈ **0.3 s**; today the step decodes 5,000 × 7.5 blobs (R3 C.1).

## Q-V Round-trip verification (12): `canon(hydrate(id)) == canon(blob)` is Q-L1 over id ranges (4 workers) plus the legacy `blobs` read — the migration's own read path; expected ≈ 6 ms/decision.

## Summary vs today

| path | today | expected | why |
|---|---|---|---|
| W1 store | 103 ms p50 | 25-45 ms | 1 transaction, no DDL/connects, set dedup |
| L1 hydrate | 2-9 ms + decode | ≈ 8 ms | narrow PK scans, cached sets |
| T1 turn_open | 100-170 ms | 40-60 ms | index-only GroupAggregate |
| T2 attributions | 2.54 s | ≤ 0.25 s | typed panel + lateral on taken index |
| T3 window gather | ~9 s DB + 2.1 GB text | ≤ 6 min single / 1.5 min ×4 (no text) | same statements over ranges |
| T5 interrupt rows | 5.7 s | ≈ 0.1 s | windowed, no world text |
| A1 run | ≈ 0.5 s | ≤ 10 ms | campaign aggregates |
| A2 campaigns | ≈ 0.3 s + rebuild storms | ≈ 15 ms | denormalised campaign |
| A6 start skills | 0.25 s + 8 MB | ≈ 30 ms | latest-per-character index |
| A8 positions | ≈ 5 s | ≈ 0.3 s | scalar columns |
| A10 menus | 43k decodes | ≈ 40 ms | option rows |
| A12 pick | series rebuild | < 1 ms | PK |
