# Handoff — data-collection rebuild, 2026-08-10

Branch `worktree-revert-unilateral-benign`. Read `SPEC_PREWIPE_20260810.md` first; it is
the design and the evidence. This file is state and next actions only.

**Goal, in Tristan's words:** fix data collection so we can start again from 0. He is not
adding features until the data is right. He wants to be told when it is.

**The run is DOWN deliberately.** `D:\twdata\BABYSIT_OFF` is set ("killed on request
2026-08-10 12:12"). Do not restart it. A babysit cron fires every 30 min and will report
`DEAD` — that is the kill switch, not a fault. **That cron is configured to relaunch the
OLD 100x20 catboost config against the live DB; it must be paused or repointed before any
fresh collection, or it will race the new run.**

---

## Done and committed

| commit | what |
|---|---|
| `a84cc09` | `stable_split` — holdout by campaign-id hash. Measured: campaigns *removed* from the holdout per growth step, `grouped_split` 71 vs `stable_split` **0**. Only `mapgraph3._fit` switched; CatBoost still uses `grouped_split` on purpose. |
| `33db229` | The spec. Plus `campaign.defeated`, which was the hardcoded literal `False` at `collect.py:266` — never queried, while the harness recorded 18 defeats. Now `f:is_dead()`, parsed to `None` when absent. |
| `03c3d40` | `ts(nil)` no longer yields the string `"nil"`. Real identity for **rituals** (`RitualContext.Key`), **ancillaries** (`AncillaryRecordContext.Key`, 3 Lua strings), **slot buildings** (`BuildingContext.BuildingLevelRecordContext.Key`, keeping the queued key separately). |
| `f7c73ac` | **One offer per recruit candidate.** Was one per subtype; 671,186 candidates were fetched across the bus and dropped in python. Key is now `<subtype>@<candidate_index>`; `click_actions._lord_execute` splits on `@`. |
| earlier | `bc6cfd7` v2 `mapgraph/` deleted + v3 wired properly; `4c74853` `common.py` (247 paths, 68 files); `e30ed9a` feature report. |

**Archive is complete and verified** — `D:\twdata\archive\corpus_20260810_prewipe\`:
`decisions.sqlite` (4.45 GB, WAL checkpointed, `quick_check: ok`, 9,013,360 offers) and
`raw/` (73 files, 4.36 GB — every jsonl, `session_*.json`, postmortems). **The raw files
are the only copy** of the per-candidate `explore` term, `choice.roll` (the behaviour
policy's propensity — without it no off-policy estimate is possible), ~7M harness drop
reasons, and the verified path to recover real rite/item keys from script logs.

**Live DB has NOT been swapped yet.** It is still at `D:\twdata\runs\human\run\`.

---

## Next actions, in order

### 1. Finish `decisions/coverage.py` (uncommitted, working)
Run: `python -m decisions.coverage <db> --sample 40000`. Against the archived corpus it
finds 262 fields, 29 constant, and correctly flags every known bug. It is the acceptance
gate for step 5.

Remaining flags to adjudicate — each is either a real bug to fix or needs a `JUSTIFIED`
entry naming a **game** reason (never "the recorder doesn't populate it"):
`relations.def_ally` (0 true in 22,136 — the agent believed genuinely dead),
`hostiles.visible` (always True), `hero.besieging`/`garrisoned`,
`diplomacy.their_vassal`, `building_repair.damaged`/`repairing`,
`building_dismantle.refund` (`DismantleRefundAmount` exists on the slot context — likely
a real fix), and `recruit_hero.is_agent` / `recruit_lord.is_agent` (constant *per type*
by construction — justify or drop).

### 2. Remaining collector fixes (spec §2)
`recruit_unit`'s local/global is **fabricated** — a cross-product where both rows are
always `available=1`. `skills` and `edict` carry `params = {}` on 100% of 2.13M rows.
`leave_garrison` is declared, has an executor, and is never emitted. `building` dedupes a
genuinely different action away (same building in two slots → one offer). Two type names
in our Lua **do not exist**: `CcoCampaignRegion` → `CcoCampaignModelRegion`,
`CcoCampaignBuildingChainRecord` → `CcoBuildingChainRecord`.

### 3. Schema rebuild (spec §3) — `decisions/store.py`
Benchmarked: **~4.7 GB at 250k decisions vs ~54 GB today**, and *faster* to write.
Intern the action payload (19x today), content-address the blobs, drop `tree_json`
(886 MB, 18.5% of the file, zero readers). Gating becomes a layout invariant:
`offer_seq 0..n_available-1` are the candidates. **`PRAGMA auto_vacuum=INCREMENTAL` must
be set at creation — it is irreversible afterwards.**

### 4. Graph fixes (spec §4) — retroactive, no wipe needed
`schema.cat_index` is `crc32 % buckets` and **not injective**: 31.8% of skills and 34.4%
of tech share an embedding row with a different key. Slot nodes are keyed by *province*
but populated per *region* — 61.1% of shared pairs hold two different buildings. Give
enemy settlements nodes (fixes `besieging`, `attack_settlement` targets and `garrison` in
one). Read `move.x,y` — **free**: `MAX_FIELDS` is 6, so action rows already carry five
unused zero columns.

### 5. Validation — the gate Tristan is waiting on
Pure random runs, no model, on an empty DB via `runctl`. Acceptance is not "it ran": it
is coverage clean, the WL identity check clean, `leave_garrison` actually emitted, and
`defeated` firing on a real defeat.

---

## Traps — these cost me real time today

**Verify every subagent claim before it reaches Tristan or drives a change.** Two of four
reports contained false claims stated as measured fact, both of the form "field X is
constant/absent":
- "`lord.stance` is CONST across 32,879 snapshots" — **false**, it has 14 values
  (DEFAULT 61.5%, MUSTER 14.1%, MARCH 10.3%, …). The report even noted stance-gated
  actions had 867 confirmed executions, which refutes it in the same sentence.
- "44.6% of `attack_settlement` targets aren't in the snapshot, needs the wipe" —
  **false**, 0 of 2,571 are missing; they sit in `hostiles[kind=settlement]` and
  `build.py`'s `MOBILE_KINDS` filter drops them. The 60-row cap is never hit (p50 7).

**Campaign depth is NOT a problem.** 420 of 560 campaigns end `stagnant` because the
harness abandons them on a growth check — that is the design, and train/deploy
distributions match. Do not raise it.

**`D:\twdata\reference\ui3_extraction\CCO.tsv`** (738 KB) is the game's own catalogue of
every context and property. Check every CCO route against it — that is how the three
identity fixes were confirmed, including that `CcoCampaignAncillary` has no `Key` at all.

**The Lua swallows its own failures.** Every emitter in `bus/mod/twstate.lua` is wrapped
in `try()`/`pcall`, the mod emits no diagnostics, and three emitters produce zero rows.
Making it report a failed `try()` is why these went unnoticed for a whole corpus.

**Do not touch** `session.py` / `manager.py` / `advisor_ui` to manage processes — Tristan
was explicit. Editing their code is fine; using them to start/stop things is not.

---

## Verify anything you land

```
python -m mapgraph3.invariants                     # 11 checks, all must hold
python -m decisions.coverage <db> --sample 40000
python -m mapgraph3.train overfit --limit 8
python mapgraph3/guard.py                          # cross-entity arithmetic selftest
python advisor_ui/lint_panels.py
```
