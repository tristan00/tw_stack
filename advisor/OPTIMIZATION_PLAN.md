# v7 pipeline speed — measurements, plan, and RESULTS

**STATUS: Wins #1 and #2 are IMPLEMENTED and live-verified.**
Collect went **3563 ms → 1183 ms median (−67%)** with an identical offer set (183 offers,
25 available), measured in a running session. Remaining items (#3 world_state merge,
#4 dirty-set) are still just plans.

| phase | before | after |
|---|---|---|
| legal_stances | 1388 | **0** (deleted) |
| province_offers | 708 | 202 |
| lord_offers | 455 | 304 |
| campaign_offers | 298 | 101 |
| campaign_state | 152 | 53 |
| world_state | 297 | 303 (unchanged, needs #3) |
| **total collect** | **3563** | **1183** |


## The one number that explains everything

**Every bus round-trip costs a flat ~101 ms, regardless of payload.** Measured:

| probe | ms |
|---|---|
| trivial eval (`return 1`) | 101 |
| 3 trivial evals, separately | 303 |
| the same 3 values in ONE eval | **101** |
| one `find` | 101 |

The work inside a call is free; only the **number of round-trips** matters. So collect time is
arithmetic, and the prediction matches the live profile almost exactly:

| phase | round-trips | predicted ms | measured ms |
|---|---|---|---|
| legal_stances | 1 find + 13 finds | 1414 | 1388 |
| province_offers | 7 evals | 707 | 708 |
| lord_offers | 5 | 505 | 455 |
| world_state | 3 channels | 303 | 297 |
| campaign_offers | 3 evals | 303 | 298 |
| campaign_state | 2 evals | 202 | 152 |
| lord_state | 1 | 101 | 101 |
| province_state | 1 | 101 | 101 |
| **total (1 lord, 1 region)** | **~35** | **3535** | **3510** |

At 1 lord + 1 region the whole decision cycle is ~6.5 s, of which **3.5 s is collect**. Collect grows
by ~6 round-trips (~600 ms) per extra lord and ~8 (~800 ms) per extra region, so a mid-game faction
with 5 lords and 8 regions would spend ~11 s per action on collection alone.

---

## Win #1 — delete `legal_stances` entirely (−1.39 s, 40% of collect)

**This is also a correctness fix, which is why it is first.**

The code currently treats the HUD stance stack as the faction-legality whitelist. That claim is
FALSE. Live, with an army selected, the stack reports **all 13 stances as `active`, including
`TUNNELING`** — which High Elves can never use. The earlier observation that MARCH/DEFAULT read
`inactive` was an artefact of *nothing being selected*, not legality.

The cco `StanceList` — which `lord_offers` ALREADY reads in an eval we are already paying for —
does discriminate correctly on the same lord, same AP:

```
DEFAULT/MARCH/AMBUSH/CHANNELING   CanBeActivated=true
TUNNELING/SET_CAMP/MUSTER/...     CanBeActivated=false
```

So `CanBeActivated` is **not** "an AP gate, not legality" as the comment in `collect.py` says: it
varies per stance on one force and already encodes the dynamic locks (movement exhausted, stuck in
a stance once recruitment starts).

Nothing has broken so far only because the offer is `available = can_activate AND can_afford AND
legal AND NOT active` — the `CanBeActivated` term was quietly carrying the whole gate while the 14
HUD round-trips contributed nothing but latency.

**Action:** drop `legal_stances()` and the `stances_legal` argument; gate on `CanBeActivated AND
CanAfford`. Also update `cco_actions._legal_stances` (the executor's defence-in-depth gate), which
reads the same misleading HUD state — `_stance_gate_state` already requires `can_activate`, so the
executor stays safe.
**Risk:** low. **Verify by:** confirming a TUNNELING offer is never `available` for a High Elf
faction, and that a stance still becomes unavailable mid-turn once recruitment is queued.

## Win #2 — one eval per phase instead of N (−1.0 s)

Pure round-trip folding; no semantic change.

| phase | now | after | saves |
|---|---|---|---|
| `province_offers` | 7 (buildings, building-in-progress, edicts, 4× lord pool) | 2 | ~500 ms |
| `lord_offers` | 5 (stances, units panel, skills, reach-chars, reach-setts) | 2 | ~300 ms |
| `campaign_offers` | 3 (current_research, tech, rites) | 1 | ~200 ms |
| `campaign_state` | 2 (scalars, campaign_uuid) | 1 | ~100 ms |

The four lord-pool evals are the clearest case: one Lua loop over the four subtypes returns the same
data in one trip. The two reachability evals already loop internally — they merge trivially by
prefixing each key with its kind.

## Win #3 — a `children` bus handler (−200 ms, needs a mod change)

`world_state` is 3 channel calls (`chars`, `setts`, `hostiles`) → 1 combined handler.
Separately, the reason `legal_stances` needed 13 calls at all is that `tree` is **visibility-gated**
and returns 0 nodes for a collapsed stack (verified), while `find` returns `child_ids` WITHOUT their
states. A `children` handler returning `[{id, state, visible}]` in one call would fix that class of
problem generally. Only worth doing if Win #1 does not remove the need — it does, so this drops to
low priority.

**Cost:** editing `bus/mod/twcontrol.lua`, rebuilding `tw.pack`, restarting the game.

## Win #4 — do not re-collect what cannot have changed (−variable, HIGHEST RISK)

The loop re-derives the entire faction after every action because an action changes what is
available. That is correct in general, but a `research` pick cannot change a lord's reachability.
A dirty-set model (execute → invalidate only the affected entity + the campaign block) would cut the
sweep to roughly one entity per action.

**Do not do this yet.** It trades a guarantee we currently have — every offer set was read at the
same instant as the decision — for speed, and the failure mode is silent stale offers, which is
exactly the class of bug that cost the most time this session (offers claiming `available` when the
game would refuse). Revisit only with a per-entity change-detector cheap enough to verify.

---

## Expected result

35 → ~10 round-trips ≈ **3.5 s → 1.0 s** collect, i.e. a ~6.5 s cycle → ~4 s (−40%), with Wins #1
and #2 alone and no mod change. Win #1 is also the only item that fixes a live correctness claim,
so it should land first and independently.
