# Data gaps: what is collected, what is not, and what would force a wipe

Written 2026-08-10 after auditing every field in the stored record against the live game.

## The rule that decides everything

A corpus wipe is forced only when a fact is **(a) time-varying and (b) absent from the
stored record**. Static facts are backfillable forever — join them at feature time.

The store keeps the **entire nested record**, not a schema-selected projection: 374 distinct
field paths, of which the graph currently reads ~21 scalars. Everything else is headroom.
Any feature derivable from a stored field needs no wipe.

## Measure cardinality, not fill rate

Fill rate lies. `lord_pools.cqis` was 2177/2177 "filled" — every value the string `'0'`.
One distinct value carries no information whether it is `null`, `0`, `'0'`, `[]` or `True`.
Of 374 paths, **100 carry exactly one distinct value**. Most are a thin corpus (3 campaigns,
turns 1–7, no treaties signed); the ones below are not.

## Closed in this pass

| gap | resolution |
|---|---|
| per-unit army composition | `unit_cards` on every lord/hero: key, strength %, category, xp. Rides the `unit_list()` walk that already computed `hp` |
| lord hidden skills | `HiddenSkillList` — the hero probe always read it, the lord probe never did |
| province economics | growth/development from `FactionProvinceManagerContext` (**province**-scoped), income at settlement *and* province, port, walls |
| third-party wars | `world.war_graph`, met-clipped, O(n) via `factions_at_war_with()` |
| world diplo state | removed with the end-of-turn stream (2026-08-18): it was write-only, never read, and unmet-faction data stays out of the record by design |
| battle outcomes | structured facts off the results screen, positional rows, generic resources sweep |
| character wounded, loyalty | fields on the existing lord/hero eval. `hero.wounded` varies; `lord.wounded` and `loyalty` carry one distinct value — see the open items below |
| corruption | **not broken** — proven live: `vampiric=50` on Nuln. The 749 zeros were real |
| `skills[].background` | deleted. `SkillList` structurally excludes background skills; 0 of 265 keys are flagged in reference.sqlite. Join `reference.sqlite.skills` if the flag is wanted |
| `hostiles[].region` | **not a gap** — rows carry `province` 489/489 and the region reconstructs at 100% |

## Open, with the reason

**XP progress to next rank — BLOCKED.** `c:experience_percentage()` does not exist
(smoke-tested live: `NO-METHOD`). `rank` *is* character level and is collected and varies
(1→3 within a campaign), so the level is present; only the sub-level progress is missing.
Needs a different property; none found on `CcoCampaignCharacter`.

**`loyalty` — one distinct value.** `0.0` in 1642/1642 snapshots (815 lord, 827 hero),
corpus at 828 decisions / 25 campaigns. Not investigated further; no cause established.

**`lord.wounded` — one distinct value.** `false` in 815/815 lord snapshots. Measured
alongside it, same corpus: `hero.wounded` false 824 / true 3; `campaign.ll_wounded` false
825 / true 3; and the 3 decisions where `ll_wounded` is true contain 0 lord snapshots.
n=3 on every true. No cause established, and the sample is too small to support one.

**`lord_pools.units` (`MainUnitRecordContext.Key`) — UNRESOLVED.** `None` in 2177/2177.
`MainUnitRecordContext` is a *sibling* of `CharacterContext`, so nothing corroborates the
route the way `bg_skills`/`subtypes` corroborate `cqis`. Left collected and flagged rather
than justified away.

**`lord_pools.traits` — game-side limit, not a bug.** Empty in 2177/2177. The traits a
recruitable lord will come with are not available until the recruitment menu is opened, so
they cannot be read in the snapshot workflow. Not worth further chasing.

**`lord_pools.cqis` — honest, and dead.** Returns integer `0`, not null: an un-instantiated
pool candidate has no command-queue index. Corroborated because `bg_skills` and `subtypes`
return real per-entry values through the identical `CharacterContext` route. Carries no
information; kept because the game is answering truthfully.

**`world.settlements[].units` — MISLABELLED, mod-side.** `r:garrison_residence():unit_count()`
(twcontrol.lua) reports the *occupying field army*, not the garrison — proven at karak_norn
where it returned 5 (a field army) while the armed-citizenry garrison on the same tile had
10. Reads 0 on our own settlements only because no own field army was ever parked on one.
Not fixed here because it is a mod change needing a pack redeploy, and **the true garrison
is already derivable** from `world.citizenry` + `world.armies` (107/107 regions matched).

**Deal outcomes — present, not joined.** `diplomacy.jsonl` in the run dir carries `terms`,
`ok` and `treaty_before` per deal. It is written every run, so nothing is being lost and it
is joinable at analysis time by (campaign, turn). Not a wipe trigger; plumbing it into the
record is a convenience, not a rescue.

**Third-party treaties and standing — deliberately skipped.** Unlike wars there is no list
accessor, so they are O(n²). They are also the low-signal half: all eight treaty flags read
`False` across 887 rows even for the player. Wars carry the signal (who is tied down, who is
dogpiling) and are O(n).

## Permanently rejected — do not resurrect

Reading anything the player cannot see. Specifically: enemy per-unit **keys** and enemy unit
**HP** (the player sees question marks), enemy garrison counts joined from `hostiles`
settlement rows, `near_enemysett_1_strength`, full map topology, and `n_adjacent_unknown` or
any frontier/neighbour count.

The graph models **known information, not perfect information**. A model trained on facts it
will not have at play time does not transfer — it scores fine offline and is silently wrong
in the game. Shroud-clipped regions and shroud-clipped adjacency are correct by design; a
small known world is a campaign that has not explored yet, not a collection bug.

Since 2026-08-18 the pre-decision snapshot is the ONLY state collection. The end-of-turn
stream (its target/entity-target tables and the whole-world diplo table) is deleted: two
witnesses of the same campaign disagreed, and every turn-state consumer now reads the
`turn_open`/`turn_close` views over the decision snapshots. Nothing stores unmet-faction
data anymore. The pre-wipe corpus and models are archived at
`<TWDATA>/archive/wipe_20260818_prewipe/`.
