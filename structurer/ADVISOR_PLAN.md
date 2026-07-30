# Decision-Point Advisor — Plan

## Goal
While you play, detect each **handled decision point** — **research, recruiting, building, events,
items** — and for every *available option* print three scores to a log:
`exploit` (predicted future value), `explore` (novelty), and `combined`. The same decision-point
abstraction builds the offline training data + models.

**Out of scope (for now):** move / positional decisions. The model is not expected to advise moves
for a long time; the value is in the menu/event decisions above.

---

## Part I — The decision-point abstraction (the whole foundation)

Everything keys off one record. A run is an **ordered trajectory of decisions**, not a bag of actions:

```
Decision = {
  campaign, turn, order,          # ordered position in the campaign
  type,                           # research | recruit | building | event | item
  trigger,                        # the log record that OPENED the decision
  context,                        # LAST-KNOWN gamestate at this decision + faction + lord
  options: [ {option_key, <option features>, available} ],   # the CONSTRAINED candidate set
  chosen,                         # which option was taken
}
```

This fixes the three flaws of the first flat pass:
- **Ordered** — decisions carry an order index; sequence matters.
- **Last-known state** — `context` is the state *at* the decision, carried forward and updated by
  intervening events, not one static start-of-turn snapshot.
- **Constrained options** — options are the alternatives available in *that* menu/event only
  (techs vs techs, a dilemma's choices) — never "move vs research".

Scoring/eval is **within a decision's option set**.

---

## Part II — Reconstructing each decision type (multi-source; in scope)

| type | trigger (detect the decision) | option set (the alternatives) | chosen (what was taken) | option features | gap |
|---|---|---|---|---|---|
| **research** | `PanelOpenedCampaign` technology_panel | ui scrape `panel=technology` (available vs locked techs) | `ResearchStarted.tech` | tech → tier / branch / cost | clean |
| **recruit** | `PanelOpenedCampaign` recruitment | ui scrape `panel=recruitment` (units + local/global) | `_recruitable` click / `RecruitmentItemIssuedByPlayer` | unit → **category** (from `unit` state records) / cost / upkeep / pool | recruit local vs global pool |
| **building** | `PanelOpenedCampaign` building_construction_popup | ui scrape `panel=construction` (available vs locked) | `BuildingConstructionIssuedByPlayer.region` + recover WHICH building | building → type / cost / effect-class | `build_key` null → reconstruct from scrape@click-time or next-turn `slot` diff |
| **event** | `ScriptEventStartTransientIntervention` `component=[EVENT]…` / event panel open | the dilemma's choices (event panel scrape, or dilemma ref) | accept/decline/option click | dilemma key + choice + choice effect (`GetStateText`) | verify/add event-panel option scrape + effects |
| **item** | `PanelOpenedCampaign` equipment/character panel | ui scrape `panel=equipment` (items available vs not) | equip event (`CharacterAncillaryGained` / equip click) | item → type / effect | identify equip event reliably |

"Recover WHICH building" / "identify equip event" etc. are the **multi-source reconstruction** the
task explicitly allows: cross-reference the menu scrape (`ui_components.jsonl`), the event stream
(`logs/*.tail`), and the next-turn state dump (`slot`, `unit`, `char` records).

---

## Part III — Features, target, model

**Context features** (per decision; faction/lord-agnostic *except* the explicit categoricals):
- categorical: `faction`, `subculture`, `legendary_lord`
- generic last-known state: income, net_income, treasury, regions, num_provinces, forces, chars,
  generals, tax_level, imperium tier, power_rank, power_score, turn, at_war, is_researching, …
- decision-local: e.g. the target settlement's public_order/growth (building), the acting lord's
  rank (skills/items) — reconstructed from `region`/`char` records.

**Option features** (per option; generic so it transfers across factions):
research → tier/branch/cost · recruit → unit category/cost/upkeep/pool · building → type/cost/tier ·
event → choice-effect descriptor · item → type/effect.

**Targets — BOTH kept first-class** (neither is dropped for predictability):
- `target_delta` = Δ normalized value over H turns (H configurable, 5–10)
- `target_best`  = campaign best-future normalized value − now
- normalized value = the **equally-weighted mean** of the min-max-normalized {income, settlements,
  power_score, **vassals**} (equal weights, as agreed; needs the vassal-count capture fix — `num_allies`≡0 today).

**Rows:** one per **available option** per decision, with a `chosen` flag. Off-policy: the outcome is
observed only for the chosen option's trajectory; the model generalizes to score the alternatives.
**Eval:** within-decision ranking (does the chosen option rank where its realized value says it
should) + off-policy value estimate.

**Model:** CatBoost regressor (native categoricals + option features), one per target. `exploit
score` = predict per option, scored **within** the decision's option set.

**Exploration:** count-based novelty per `(type, option[, context bucket])`; UCB bonus
`sqrt(2 ln N / n)` (high for rarely-chosen options). `combined = exploit + β·explore`.

---

## Part IV — The live advisor (operationalization)

Runs while you play; on each handled decision, logs a 3-score table per option.

**Architecture — a separate advisor consumer that REUSES the recorder's capture** (capture threads
stay pure; the advisor never re-enumerates the bus). Two coupling options:
- **(a) Decoupled — recommended v1:** the advisor tails the run's `ui_components.jsonl` (menu opens
  the recorder already writes, with options) + `logs/*.tail` (gamestate). Zero recorder changes, no
  bus contention (ui-capture already did the enumeration).
- **(b) In-process consumer:** the manager runs an optional `advisor` consumer subscribing to
  ui-capture's menu_open callback directly (lower latency). Add only if (a)'s file-tail latency hurts.

**Live pipeline (per decision):**
1. Detect a handled menu/event open — piggyback on ui-capture's `PanelOpenedCampaign` detection; the
   advisor receives the `menu_open` with its options. No extra bus load.
2. Maintain **last-known gamestate incrementally** — parse only the log tail as it grows and update a
   running state dict; never re-parse the whole (multi-GB) log.
3. Featurize the available options + context (offline reference tables loaded once).
4. CatBoost predict → `exploit` per option; count model → `explore`; `combined`.
5. Print a table to `advisor.log`, sorted by combined:
   ```
   turn 7  RESEARCH  (5 options)
     combined  exploit  explore  option
      +0.42     0.31     0.11    wh2_main_tech_hef_1_03  (tier1, cost 800)
      ...
   ```

**Efficiency:** detection piggybacks ui-capture; gamestate is O(new bytes) incremental; model loaded
once, predict sub-ms/option; only the 5 handled panel types trigger scoring; reference tables
precomputed offline.

---

## Part V — Build phases

- **A. Decision dataset** — implement the Decision abstraction + reconstruct the 5 types (multi-source);
  emit one row per available option (chosen flag, both targets).
- **B. Reference tables** — unit→category/cost (from `unit` records), tech→tier/cost, building→type,
  item→type; from state dumps + a static game reference where captured data is thin.
- **C. Models** — within-decision value models (both targets) + exploration model; offline eval
  (ranking + off-policy).
- **D. Live advisor (decoupled v1)** — incremental gamestate + `ui_components.jsonl` consumer +
  scoring + `advisor.log` tables.
- **E. Efficiency + optional in-process integration** (manager `advisor` consumer).
- **F. Validation** — live sanity (sensible tables) + offline off-policy eval.

---

## Part VI — Data gaps that block full reconstruction (also in TODO.md)
- `build_key` (which building) — reconstruct via scrape/slot-diff, or fix in the mod.
- recruit **local vs global** pool.
- event-panel **option scrape** (verify it exists; add a panel config if not) + choice effects.
- item **equip event** identification.
- **vassal count** (the 4th value dimension; `num_allies`≡0 today).

---

## Part VII — Open questions (need your call before Phase A)
1. **Combined score's "exploit" target** — delta, best, or a blend of both? (β fixed, or annealed over a run?)
2. **Exploration granularity** — global per option, or context-conditioned (per faction / turn-band)?
3. **Option features** — derive purely from captured state dumps, or allow a small static game-data
   reference (unit/tech/building tables) to fill what capture misses?
4. **Advisor v1 coupling** — decoupled file-tail (recommended), or straight to in-process consumer?
5. **Service boundary** — advisor as a module inside `tw_stack/structurer`, or its own `tw_stack/advisor` repo/service?
