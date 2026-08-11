# Handoff — data-collection rebuild, 2026-08-10 (second pass)

Branch `worktree-revert-unilateral-benign`. `SPEC_PREWIPE_20260810.md` is the design; read
**§0 "Corrections" below before trusting any specific claim in it**, because two of its
measured statements turned out to be wrong.

**Goal, in Tristan's words:** fix data collection so we can start again from 0. He is not
adding features until the data is right. He wants to be told when it is.

**The run is still DOWN deliberately.** `D:\twdata\BABYSIT_OFF` is set. Nothing here
started, restarted or touched a run. The babysit cron still points at the OLD 100x20
catboost config against the live DB and **must be paused or repointed before any fresh
collection**, or it will race the new run.

---

## Where this stopped

Spec steps 1–4 are done and committed. **Step 5, the validation gate, has not run and
cannot run from here** — it needs a live game, and the policy is not to start one. That is
the only thing standing between now and "the data is right".

| step | state |
|---|---|
| 1. adjudicate coverage | **done** — all 21 flags resolved, 5 were real bugs, 1 was the checker's own false positive |
| 2. collector fixes | **done** — §2 b1–b9 plus the two ghost type names |
| 3. schema rebuild | **done** — 6.8x smaller, 0.82x write time, fidelity checked |
| 4. graph fixes | **done** — catalogue injective, slots per region, enemy settlements |
| 5. validation on fresh data | **NOT RUN — needs Tristan** |

---

## §0. Corrections to SPEC_PREWIPE_20260810.md

The spec's own handoff warned that "field X is constant/absent" claims had reached Tristan
as fact without being checked. Two more did.

1. **§2 b7 says `DismantleRefundAmount` "exists on the slot context". It does not.** It is
   a property of `CcoCampaignBuilding`, reached from the slot via `BuildingContext`. So is
   `IsDamaged`. And `CanBeCancelled`, which both the collector and the executor gated on,
   belongs to `CcoCampaignMission` and to nothing else. All three were being read off
   `CcoCampaignBuildingSlot`, where they return nil forever.
2. **§4 says `cat_index` collides for "31.8% of skills and 34.4% of tech".** That was
   measured over observed keys only. Over every key in `reference.sqlite` it was **48.6%
   of 19,313** — 60.6% of building chains, 51.1% of skills, 47.9% of buildings.
3. **§2 b3 calls `cand_rank` and `traits` "unresolved, and must not be guessed".** They are
   resolvable from the archive and now are. `_num('')` yields `None`, so a failed read and
   a real zero were already distinguishable: `cand_rank` is 0.0 in all 349,934 rows because
   a pool entry genuinely has no rank. `traits` is non-empty in 241 of 159,336 lord rows,
   so that read works too, and hero candidates genuinely carry none.
4. **§2 b4 asks for "a real per-queue availability flag" for `recruit_unit`. There is no
   such thing to read.** Neither CCO nor the script API distinguishes the local pool from
   the global one headlessly; only the recruitment panel knows, and only the executor sees
   it. See "One behaviour change" below.

---

## Landed, with the evidence

| commit | what |
|---|---|
| `3953f1c` | **`decisions/cco_audit.py`** — resolves the receiver type of every `g()` and `:Call()` in the collector *and* the executors and walks each route against `CCO.tsv`: **217 routes, 0 bad, 0 unresolved**. Arguments are routes too and are checked. UNRESOLVED fails the build. Found all five bad routes above, plus two context type names the game does not have (`CcoCampaignRegion`, `CcoCampaignBuildingChainRecord`). Also: `recruit_unit` stops fabricating local/global, `building` stops deduping a real action away, `leave_garrison` is emitted, recruit/edict carry the region and province that made them different actions, `skills` carry level/tier, and `try()` in both mods now reports its failures. |
| `c746615` | **v2 store.** Interned actions, content-addressed blobs, `WITHOUT ROWID` clustering, gating as a layout invariant, `auto_vacuum=INCREMENTAL`. |
| `f7257aa` | **Graph fixes.** Injective catalogue, slots keyed by region, enemy settlement nodes, `move.x,y`, `garrison`'s `settlement:` prefix, `building`'s key, `agent_subtype` catalogue. |
| `3564629` | **WL identity check** plus the last two non-injective hashes (`stance_index`, `subtype_index`). |

**The store numbers are a like-for-like comparison, not a projection.** 2,000 real
decisions from the archive were written through both the v1 and the v2 write path:
389.7 MB → 57.3 MB (**6.8x**) at **0.82x** the write time; 7.2 GB vs 48.7 GB at the
250,000-decision target. The v1 figure matches the archive's own 216 KB/decision.
Fidelity was checked rather than assumed: **300 decisions / 158,944 offers** read back out
of v2 are identical to what v1 held — same entities, same states, same offers as multisets
of `(type, key, available, gate, params)`.

---

## The five gates in §5

| gate | command | state |
|---|---|---|
| identity | `python -m mapgraph3.wl <run> --n 40` | implemented; **673 of 9,045 still violating on replayed OLD data**, all of it `items`/`item_unequip` (item_key was `"nil"`) and the three slot ops (building_key was null in 94.2%). `--selftest` proves the fix clears them: 8/8 inseparable before, 0/8 after. **Re-run on fresh data; it should be 0.** |
| coverage | `python -m decisions.coverage <db> --sample 40000` | implemented, sampling fixed |
| layout | `store.layout_violations()` | 0 on every write in every test and both replays |
| catalogue injectivity | `python -m mapgraph3.invariants` | 0 of 19,313 collide |
| holdout stability | landed earlier (`a84cc09`) | unchanged |

---

## Next actions, in order

### 1. Decide the live database (blocking, and it is a real decision)
`D:\twdata\runs\human\run\decisions.sqlite` is still v1. `DecisionStore` now **refuses** to
open it — deliberately, per spec §3.5. Raw-SQL readers (`advisor_ui/ui.py` and friends)
still work against it, because in a v1 file those table names are real tables rather than
views, so the state is mixed and quietly confusing. **Archive that run directory and start
a fresh one before collecting.** Nothing has been moved.

### 2. Pause or repoint the babysit cron
It will relaunch the old config against whatever is at the live path.

### 3. Run the validation gate (spec §6)
Pure random runs, no model, on an empty DB via `runctl`. Acceptance is not "it ran":

```
python -m decisions.coverage <new_db> --sample 40000   # every flag adjudicated or justified
python -m mapgraph3.wl <new_run> --n 200               # expect 0
python -m mapgraph3.invariants                          # needs torch for the network half
python -m mapgraph3.test_build <new_run> --n 40
python -m decisions.test_store
python -m decisions.cco_audit
python bus/test_lua_syntax.py
```
plus, by hand: identity present for all 28 emitted action types, **`leave_garrison`
actually emitted**, **`defeated` firing on a real defeat**, and `building_repair.damaged`
becoming non-constant once a settlement is actually attacked.

**State the metric discontinuity loudly when this lands.** Uniform NLL over available-only
candidates is **4.818** against **5.925** over all candidates. Roughly **57%** of the
model's apparent skill today is reproducing the availability filter. `val_nll` will jump
and that is not a regression.

---

## One behaviour change, which is Tristan's to review

`recruit_unit` no longer names a queue, because nothing the collector can read knows one.
The cost of inventing it was measurable: of 652 `global` picks the agent made, **371
(56.9%) died in `execute_failed`** because no global pool existed, against 225 of 1,128
for `local`. The pool is now chosen in `click_actions.py`, where the pools are actually
visible — cheapest by turns, then gold — and recorded as `queue_used` in the confirm
diagnostics, so the corpus says what happened instead of what was assumed.

This is the one place where I changed what the agent *does* rather than what is *recorded*.
If you would rather the executor refuse than choose, that is a two-line change in
`_recruit_execute_inner`.

---

## Not done, and deliberately so

- **`horde_building`'s `slot_id` is still unwired.** `_add_action` looks up
  `slot_index[(region, slot)]`, and a horde slot has no region, so it never resolves. Doing
  it properly needs horde slot nodes, which do not exist yet.
- **Factions still have no identity embedding** (only `is_player`). It is a new catalogue
  kind and a vocabulary decision; it did not seem like something to land unreviewed.
- **`recruit_unit.available` is still 1 in every row.** `_LUA_RECRUITABLE` only emits units
  `can_recruit_unit` already said yes to, so the gate carries no information for this type.
  Fixing it means emitting the faction's whole roster with real availability, and the extra
  per-decision CCO cost has to be measured on live hardware first. I could not measure it
  with the run down, and guessing at a latency budget is how the 30s timeouts got there.

---

## Traps

**Verify every claim of the form "field X is constant/absent" before it drives a change.**
This is now three for three across two sessions. `coverage.py --sample` was itself
producing them: `LIMIT N` with no `ORDER BY` returns the N lowest rowids, so
`diplomacy.their_vassal` was reported dead off the first 40,000 rows and is true in **8,256
of 2,131,584** — vassals do not exist on turn 1. It strides `decision_id` now.

**`D:\twdata\reference\ui3_extraction\CCO.tsv` is the authority, and `decisions/cco_audit.py`
now reads it for you.** Do not add a CCO route by hand without running it.

**`bus/test_lua_syntax.py` needs `pip install lupa`** and compiles all 55 Lua fragments,
mods and embedded strings alike. There is no Lua in the game's own environment to test
against here, so this is the only thing standing between an edit and a mod that silently
fails to load.

**No torch or catboost on this machine.** `mapgraph3.invariants` runs its catalogue half
and says out loud that the network half did not run; `mapgraph3.train` and
`advisor/interrupt_model.py` cannot run at all here. Two of the five commands in the old
handoff's verify list have therefore **never been executed against these changes**. Run
them somewhere with torch before trusting the training path.

**`advisor_ui/lint_panels.py` reports one problem** — `infra` column `started` empty in
every row. It predates this work and nothing here touched it.

**Do not touch** `session.py` / `manager.py` / `advisor_ui` to manage processes. Editing
their code is fine; using them to start or stop things is not.

**Campaign depth is NOT a problem.** 420 of 560 campaigns end `stagnant` because the
harness abandons them on a growth check — that is the design. Do not raise it.
