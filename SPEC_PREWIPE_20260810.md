# Pre-wipe spec: collector, schema, and the checks that keep them honest

Everything here is measured against the archived corpus
(`D:\twdata\archive\corpus_20260810_prewipe\`, 22,136 decisions / 9,013,360 offers /
583 campaigns / 4.78 GB) unless marked *inferred*.

The instruction that produced the current mess was "be complete". It failed because
completeness was a claim nobody could contradict — a silently-empty field looks exactly
like a legitimately-empty one. So every section below ends in a **check that fails**,
not a promise.

---

## 0. Decisions already taken

| | |
|---|---|
| Wipe | Yes. Criterion: any field distinguishing two actions that is *not recorded at all* justifies it. Two qualify (§2 b1, b3). |
| Store | **One sqlite.** No parquet sidecar, no second store. |
| Old corpus | **Does not migrate.** Archived, not deleted. Reasons in §3.5. |
| Gating | Applied **before** the candidate set is formed, as a layout invariant (§3.3). |
| `auto_vacuum` | **`INCREMENTAL`, set at creation.** Irreversible afterwards without a full VACUUM. |

### The one finding that reframes "two actions must differ"

Full scan, all 9,013,360 offers: collisions under `(action_key + every params field)` =
**0**. The database already distinguishes every action within a decision. Measured by
Weisfeiler-Leman refinement to convergence — which *proves* indistinguishability to any
message-passing network at any depth — the graph nonetheless has:

- **543 of 544 graphs** containing at least one violating class
- **24.8%** of action nodes in a violating class
- **47.4%** of taken actions indistinguishable from a candidate that was not taken

So the literal requirement fails **entirely downstream, in `build.py`**, and is fixable
retroactively. The wipe is justified by a different failure: positional identifiers that
alias *across* decisions, and candidates never emitted at all.

---

## 1. The mechanism behind the data loss

```lua
local function g(c,p) ... if ok and v~=nil then return v end return nil end
local function ts(v) return tostring(v) end
```

`g` correctly returns nil for a missing CCO property. `ts` is `tostring`, and
`tostring(nil)` is the **string `"nil"`** — which lands in the DB looking like data.
42 call sites use `ts(g(...))`. Measured damage: `params.item_key` (95,178 rows, 100%)
and `state.province` (277 rows). The other 40 happen to name their properties correctly.

**Fix:** `ts(nil)` must emit `''`, and the Python side must map empty to `None`. A
missing property then reads as absent instead of as a plausible three-character string.
This alone would have surfaced the item bug on day one.

**`D:\twdata\reference\ui3_extraction\CCO.tsv`** (738 KB) is a complete catalogue of the
game's CCO surface — every context, property, return type and CA's own doc string. It is
referenced nowhere in the repo. Every route below is verified against it.

---

## 2. Collector fixes — must land before the wipe

### b1. Ritual identity — CRITICAL
`rite_index_N` is a UI list position. **0 of 109 join** `rituals` (1,326 rows).
`rite_index_1` is offered by **88 factions across 23 races** and shares one embedding row.
The list length also changes mid-campaign in 68 of 497 campaigns, so the index aliases
across turns of a single campaign too.
**Route:** `g(r[i],'RitualContext.Key')` — `CcoCampaignRitual.RitualContext` →
`CcoRitualRecord.Key`. Verified in CCO.tsv. Per-decision, in-string, **free**.
Keep `rite_index` in params as the execution handle (`cco_actions.py:711-714` indexes the
list). Free extras on the same context: `InvalidRitualReason` (a *localised* reason,
strictly better than our hand-rolled `gate="cannot_perform"`), `IsOnCooldown`,
`RemainingCooldown`, `InfluenceCost`, `SlaveCost`, `CanAfford`.

### b2. Ancillary identity — CRITICAL
`CcoCampaignAncillary` has **no `Key` property** (confirmed against CCO.tsv). The call
returns nil → `"nil"` → and because `"nil"` is truthy in Python, the `a["key"] or name`
fallback at `collect.py:1231` never fires, so **`action_key` is `"nil"` too**, not just
`params.item_key`.
**Route:** `g(l[i],'AncillaryRecordContext.Key')`. Three identical strings to fix:
`_LUA_ANCILLARY_POOL`, `_LUA_EQUIPPED`, `_LUA_EQUIPPED_ALL`.
**Also fixes a silent bug:** `_free_by_type` reconstructs which ancillaries are free by
counting *display names*, and names are not unique — "Warhorse" appears 42 times among
2,671 loc rows. `CharacterEquippedToContext` answers it exactly.
**Retroactive note:** `(item_name, pool_index, equipped_index)` disambiguates with **0
collisions** across 22,541 multi-offer groups, so the 793 dropped decisions are
recoverable from the archive without re-collection.

### b3. Recruit candidates — CRITICAL, and not the bug it looks like
`collect.py:1613-1629` fetches all `n` pool entries; `collect.py:1539` then emits
**one** offer: `i = oks.index(True) if any(oks) else 0`. So `candidate_index == 0` in
**159,336/159,336** lord and **190,598/190,598** hero rows. The other candidates are
fetched and thrown away.
`CcoCampaignCharacterPoolEntry` has exactly four properties: `CanRecruitCharacter`,
`AgentRecordContext`, `MainUnitRecordContext`, `CharacterContext`.
**Route:** emit one offer per pool index, keyed `subtype@candidate_index`, carrying
`CharacterContext.BackgroundSkillContext.Key` (the real differentiator on a recruitment
card — and `reference.sqlite.skills` already has 961 background-skill rows to join),
plus `CQI`, `AgentSubtypeRecordContext.Key`, and the three attribute levels.
**Unresolved, and must not be guessed:** `cand_rank == 0.0` and `traits == []` in 100% /
99.8% of 349,934 rows. Either pool candidates are genuinely rank-0 and trait-free at
turn ≤ 13, or the read fails silently. Max turn in the corpus is 13 and p50 campaign is
4 turns, so the regime that would settle it is absent. **The collector must emit a
read-failure sentinel distinct from an empty list, or this looks identical after the
wipe.**

### b4. `recruit_unit` local/global is fabricated
`RECRUIT_QUEUES = ("local","global")` is cross-producted at `collect.py:985`. Both rows
are `available=1` in **all 221,504** offers, and `_parse_recruitable` hardcodes
`"state": "active"`. So `available`/`gate` carry zero information for this type.
**Record:** the pool the unit is actually recruitable from, its cost, pool availability,
turns-to-recruit, and a real per-queue availability flag.

### b5. Mercenary pools lose their origin
`agg` collapses the faction pool (`F~`) and region pool (`P~`) by unit key, summing counts
and min'ing cost; the origin is parsed then discarded. Units absent from
`reference.sqlite:merc_units` are **dropped entirely**, and multi-flavour units get their
action_type from `sorted(fset)[0]` — the type label itself is a guess.

### b6. `building` — a real action is deduped away
`collect.py:1515-1523` holds `seen` on the building key, so a building constructible in
two slots yields **one** offer carrying whichever slot came first. `CanUpgrade` is parsed
and never used.
**Record:** one offer per `(slot_index, building_key)`, plus cost and `CanUpgrade`.

### b7. Slot ops read the wrong building
`_LUA_SLOT_STATES` reads `ConstructionItemContext.BuildingLevelRecordContext.Key` — the
**queued** item, not the building occupying the slot. Null in **181,026/192,074 (94.2%)**.
**Route:** `g(sl,'BuildingContext.BuildingLevelRecordContext.Key')` — the identical
expression already runs at `collect.py:656-658`. Keep the queued key under `queued_key`;
they answer different questions and conflating them caused this.
Free extras: `Health`, `IsRuined`, `DismantleRefundAmount` (which is why `refund` is
always null), `IsDismantling`, `IsUpgrading`, `RepairCost`.

### b8. Enemy settlements — NOT a collection gap
Measured: **0 of 2,571** attack_settlement targets are missing from the snapshot. 55.4%
are in `world.regions`/`settlements`, **44.6% are in `world.hostiles[kind=settlement]`** —
exactly where the offer generator draws them (`collect.py:957`). The 60-row `hostiles` cap
is **never hit**: p50 7, p95 16, max 38.
This is category (a): `build.py`'s `MOBILE_KINDS` filter drops every enemy settlement, so
the target exists but never becomes a node. One fix (enemy-settlement nodes) resolves
`besieging` (0 edges in 653 graphs), `attack_settlement` targets, and `garrison`.

### b9–b20 (lower priority, same shape)
`skills` params empty — record rank/point cost (no multi-point offer has ever surfaced;
absent regime, not absent feature). `edict` params empty, and the same
`(province, edict_key)` is emitted once per region — **748 of 3,310 (22.6%)** are literal
repeats, which splits the listwise softmax between identical candidates. `stance`'s
`CanBeActivated` and `CanAfford` collapse into one gate string. `move` records `x,y` but
not the destination's meaning, and destinations are randomly re-sampled every decision so
the candidate set is not reproducible. `diplomacy` drops four relation flags that are
already parsed into `world`. `garrison` computes the current occupant and folds it into
the gate. **`leave_garrison` is declared, has an executor, and is never emitted — 0 rows
in 9M.** `building_repair`/`_cancel` are unavailable in **100%** of 384,148 rows.
`research.cost` is 0.0 in **96.2%** of rows.

### b21. Storage-level
`action_offers` has no uniqueness constraint on its identity tuple, so `attach_scores`
updates *all* matching rows and `attach_taken` `fetchone()`s an arbitrary one. Measured
**0.705%** of recent offers collide on it, rising from 0.361%. Fixed by §3.

### Opportunistic, all free (per-decision, in-string)
Army/agent/settlement **caps** (the hard constraints on `recruit_lord`/`recruit_hero`,
currently invisible). **Missions** — `MissionList`, `ActiveMissionList` — the game is
continuously stating objectives and none is recorded. Pooled-resource **maximums**
(`pr:maximum_value()`, already read in twstate.lua) so "near cap" is expressible. **Our
own characters' traits** (`c:all_traits()` runs in production; collect.py records none).
**Localised text** — no `Description`/`Tooltip` path exists anywhere in the repo.

---

## 3. Schema

Benchmarked on synthetic data built from real sampled text, at 1x and 4x.

| | today | interned |
|---|---|---|
| 22k decisions | 2,240 MB | **360 MB** |
| 88k decisions | 9,214 MB | **1,473 MB** |
| `write_decision` p95 @4x | 3.0 ms | **1.6 ms** |
| `attach_scores` @4x | 1.1 ms | **0.35 ms** |
| **projected at 250k** | ~54 GB | **~4.7 GB** |

### 3.1 The three levers
1. **Intern the action payload.** 474,685 distinct `(kind,id,type,key,params)` among
   9,013,360 — **19x**, growing to ~28x at target (vocabulary exponent 0.842, measured
   over six points). Set-interning does *not* work (only 1.66x); per-row does.
2. **Content-address the JSON blobs.** `world` is 58.2% byte-identical to the previous
   decision in the same campaign; 2.48x dedup × 6.16x zlib = **15x**.
3. **Stop writing `interrupt_decisions.tree_json`** — 886 MB, **18.5% of the database,
   zero readers**.

### 3.2 Tables
`meta` · `collector_versions` · `blobs`(sha, n, zlib) · `actions`(interned, UNIQUE over
all five identity columns) · `gates`(37 distinct) · `campaigns` · `decisions` ·
`entities` · `offers` · `taken` · `scores`(separate, prunable, packed float32) ·
`interrupts` · `target_rows` · `entity_target_rows`.

`offers` and `entities` are `WITHOUT ROWID`, clustered on `(decision_id, seq)` — the rows
*are* the index. That deletes `ix_offer_dp` and `ix_offer_key` outright: measured 90 B/row,
**8.5 GB at target**.

### 3.3 Gating as a layout invariant
`offer_seq` is assigned **after** gating: `0 .. n_available-1` are the candidates, gated
offers follow. The candidate set is then a contiguous prefix — no filter, no secondary
index, and 64.6% of rows are never touched by a training walk. `available` survives as a
1-byte column purely so the invariant is *checkable*:

```sql
SELECT count(*) FROM offers WHERE (offer_seq < n_available) != (available = 1);  -- must be 0
```

Gated offers are still stored verbatim: a gate reason is a diagnostic, and the whole point
of storing the payload is that an unprojected field is a re-derivation rather than a wipe.

**Metric discontinuity to state loudly when this lands:** uniform NLL over available-only
candidates is **4.818** vs **5.925** over all. Roughly **57%** of the model's apparent
skill today is reproducing the availability filter. val_nll will jump and must not be read
as a regression.

### 3.4 Read path
A merge join of four cursors ordered on `decision_id`, all clustered — sequential scans,
peak RSS of one decision plus two caches. `params` is parsed **once per distinct action**
(11.8 s → 0.49 s at today's scale). Finished campaigns are skipped by decision-id range:
**all 583 campaigns occupy a contiguous range**, verified, zero exceptions.

Cold full walk: **~9 s at 22k, ~100 s at 250k** (vs 21 s / ~238 s today, and today's path
materialises 9.28 GB RSS first — ~107 GB at target, fatal).

### 3.5 Why the old corpus does not migrate
The *mechanics* are easy (~60 s). The **semantics** don't survive: (a) 0.705% of recent
offers collide on the old identity and the disambiguating `params` was never in the key,
so the label is genuinely ambiguous for 1,643 of ~4,200 recent decisions; (b) collector
version cannot be reconstructed without guessing, which is the failure mode
`collector_versions` exists to kill; (c) campaign outcome was never in the DB at all.

### 3.6 Known cliffs
**Concurrency is the first wall, not size** — WAL gives one writer, so parallel collection
into one file and the one-database constraint are mutually exclusive. Symptom:
`database is locked`, silently dropped decisions. `ux_actions` is the only non-append-only
index. **And the graph cache is already the bigger store: 36 GB at 250k, 7x the database** —
the e2a channel is exactly a2e mirrored (verified 60/60 graphs) and every index but `x` is
a needless int64; together ~3.6x.

---

## 4. Graph fixes — retroactive, no wipe needed

`schema.cat_index` is `crc32(kind\0key) % buckets` and is **not injective**: **31.8% of
skills** and **34.4% of tech** share an embedding row with a different key. The docstring
already says every key joins the reference DB, so dense ids are available and the hash was
never needed.

Slot nodes are keyed by **province** but populated per **region**: of 558 shared
`(province, slot)` pairs, **341 (61.1%)** hold two different built buildings — corrupted,
not merely collapsed. Province entities share an ego in **40.1%** of cases.

Also: read `move.x,y`; read the `stance` key; stop stripping `@queue` from `recruit_unit`;
take `building`'s key from `action_key` (joins **912/912**, while `params.building_key`
does not exist for that type); strip the `settlement:` prefix in `_target_of` (**1,973/1,973**
garrison targets fail on it); add an `agent_subtype` catalogue kind; wire `horde_building`'s
`slot_id`; give factions an identity embedding; give enemy settlements nodes (§2 b8).

**Free win available immediately:** `MAX_FIELDS` is 6, set by `lord`, so every action row
already carries **five unused zero columns**. Adding `x,y` to `TYPE_FIELDS["action"]`
changes the encoder input width by nothing.

---

## 5. The checks

Each one fails the build. None is a promise.

1. **Identity** — Weisfeiler-Leman refinement to convergence over each graph. Two action
   nodes sharing a colour are provably indistinguishable to *any* message-passing net at
   any depth. Tolerance is a derived predicate, never a per-type whitelist: a class is
   acceptable only if every offer in it has identical `(action_type, action_key, params)`.
   Under that rule only `edict` and `noop` collisions are legitimate today.
2. **Coverage** — any recorded field constant, null or empty across a sample **fails**
   until explicitly justified as genuinely N/A, with a reason and a name attached.
   `campaign.defeated` (hardcoded `False`, 22,136/22,136, while the harness recorded 18
   defeats) would have failed on day one.
3. **Layout** — the `offer_seq < n_available ⇔ available` invariant above.
4. **Catalogue injectivity** — no two live game keys may share a `cat_index`.
5. **Holdout stability** — already landed (`a84cc09`): campaigns removed from the holdout
   per growth step, `grouped_split` 71 vs `stable_split` **0**.

---

## 6. Validation gate

Pure random runs (no model) on an empty DB via `runctl`. Not "it ran" — the acceptance
criterion is the checks in §5 passing on freshly collected data, plus: identity present
for all 28 emitted action types, `leave_garrison` actually emitted, `defeated` firing on a
real defeat, and no always-empty field lacking a justification.
