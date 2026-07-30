# structurer TODO — capture / schema gaps found while building data views

Each item is a place where a requested data view could NOT be produced cleanly from the current
capture — i.e. the eventual schema/mod must record more, or an offline correlation is required.

## Recruitment: local vs global pool  [OPEN]
`RecruitmentItemIssuedByPlayer` carries only the faction; the `_recruitable` ComponentLClickUp
carries only the shared leaf id. The SAME unit is recruitable from BOTH pools (verified: every unit
appears local AND global in the recruitment scrape), shown together at different positions — so the
pool is **not in the event stream**.
- Recoverable only by correlating the click's INPUT mouse position (`events.jsonl`) with the
  recruitment scrape's local/global option positions (`ui_components.jsonl`) — needs the client-rect
  coordinate mapping (see the `correlation` repo). A first attempt did not line up cleanly.
- **Clean fix (capture):** have the mod log the recruit SOURCE / full component path on the
  `_recruitable` click, or populate `RecruitmentItemIssuedByPlayer.recruit_unit_key` + a pool field
  (both exist in the TWSTATE schema but are null). Until then recruit rows read "pool: unknown".

## Building key not captured  [OPEN]
`BuildingConstructionIssuedByPlayer` gives only the region/garrison, not WHICH building was queued.
`build_key` / `build_record_key` exist in the schema but are null. Need the construction-panel scrape
correlation, or a populated mod field.

## Opaque numeric codes  [OPEN]
- Settlement occupation: `occupation_decision` = "1063" (numeric code), not occupy/raze/sack;
  `occupation_decision_key` exists but is null.
- Army stance: `ForceAdoptsStance.stance` = 5 (numeric), no stance-name key.
Need the `*_key` fields populated by the mod, or an offline code→name mapping table.

## Skill: auto (rank-up) vs deliberate  [HANDLED heuristically]
`CharacterSkillPointAllocated` fires for BOTH auto rank-up passives and deliberate picks. Currently
separated by the heuristic "same game-second as a CharacterRankUp for the same char" (confirmed by
audit). A dedicated capture flag (player-initiated vs engine-granted) would be more robust.

## Recruit quantity / aggregation  [NOTE]
Recruiting the same unit N times shows as N separate `_recruitable` clicks / N
`RecruitmentItemIssuedByPlayer` events (turn 1: 5× shadow_warriors). Currently emitted as N rows;
decide later whether a view should aggregate to "recruit ×N".

## Menu availability: `unknown` (clickable=null)  [OPEN]
Most panels give clean available/not_available, but `army` is all-null (unit cards, not gated --
expected) and technology/skills/pre_battle have some null from options scrolled off-screen in the
scrape. A full-availability view needs the scrape to page/scroll the panel, or the mod to report
availability for off-screen options.

## Multi-campaign runs + campaign stitching  [PARTIAL]
A run dir holds several campaigns (223229 had 6). `list_campaigns` enumerates each script_log tail
with an is_human faction and drops turn-subsets. STILL TODO: a single campaign split across a
recorder restart shows as two contiguous same-faction tails (e.g. nagarythe t1-7 + t7-10) -- stitch
contiguous same-faction tails into one campaign. Also a min-turn floor for run_compare (t<=2
campaigns share too few turns -> degenerate 0.00 / `--`).

## Value-model target: vassals not captured  [OPEN]
The normalized value metric wants {income, settlements, power_score, vassals}, but `num_allies` is
ALWAYS 0 in the data -- vassal/ally count is not captured, so that term is dead. Capture a real
vassal/ally count (cm faction vassal/ally list) to give the target its 4th dimension. (Deltas cancel
the constant, so current targets are unaffected -- the metric is just missing a dimension.)

## Value-model choice generalization  [OPEN]
`action_key` is kept raw (faction-specific unit/tech keys). To generalize choices across factions
(the stated goal), map keys to faction-agnostic categories: recruited unit -> its `category` (the
`unit` state record already has it: inf_melee, cav, ...); tech -> branch/tier; building -> type
(needs the `build_key` gap fixed first). faction + legendary_lord stay explicit categoricals.
