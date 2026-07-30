# Menu / Decision-Point Extraction Spec

For EVERY WH3 decision menu, precisely how to extract **(a) the available options (full shown set)** and
**(b) the chosen option** from recorded data. This is the distilled result of a full knowability audit on
valid data — a future advisor agent should use this instead of rediscovering it. Each entry marks what
works from **current (v6) captured bytes** vs what needs the **game DB** (one-time offline) vs what **v7
capture** adds.

## Data sources (use every one)
1. `<run>/ui_components.jsonl` — menu scrapes `{t, kind:"menu_open", panel, options:[{id,key,clickable,state,visible,source?,text,text_label,x,y,w,h}]}`. Option `text`/`text_label` are EMPTY in v6 (leaf-only read); `source`∈{local,global} on recruitment. Recorder-time `t`.
2. `<run>/logs/script_log_*.tail` — TWSTATE JSON rows (state+events, game-time `<Ns>` prefix) **and** the game's `[ui] path from root:` click lines paired with the adjacent `ComponentLClickUp` (carry pool-group, building slot, ancillary cqi, dilemma ordinal). Intra-tail, same game clock, no offset.
3. `<run>/events.jsonl` — recorder input (mouse coords, keys, shots). Recorder-time `t`.
4. `D:/totalwar_runner/data/twcontrol.jsonl` — RAW bus `find`/`tree` results: every node's `GetStateText`/`GetStateTextLabel`/`GetTooltipText` (labels, costs, tooltips) + full pipe-path + `context` Cco id + rect. Shared append-only across sessions, keyed only by `seq`+`turn` (weak attribution — v7 fixes this with a per-run attributable stream).
5. `D:/totalwar_runner/data/structured/<session>/` — `run.db` (sqlite) + `csv/` (`panel_options`, `state_tech`, `state_unit`, `state_slot`, `state_faction`, `state_force`, ...): a structured form of a captured run.
6. **Game DB** (`db.pack` + `local_en.pack`; one-time RPFM extraction) — static features keyed by record key: unit cost/upkeep/tier, tech cost/effect, building income, ancillary/dilemma effect magnitudes, localized names.

## Join keys
game-time `<Ns>` (TWSTATE ↔ `[ui]` lines, adjacency); recorder-`t` ↔ game-time via `correlation.solve_offset`; `seq` (twcontrol ↔ `commands.txt`, current session only); record keys → game DB; `cqi` (runtime, per-campaign — never reuse across campaigns).

## Validity + attribution
Valid data = `runs.list_valid_runs()` (recorder v5/v6 + required streams + derivable `is_human` faction).
Player = the `is_human:true` faction. An event is the player's iff a `*_faction`==player **OR** (no faction
field **AND** `in_player_turn`); several events attribute differently (`char_faction`, `performing_faction`,
`char_cqi`, `mf_cqi`) — noted per menu. Never guess: unresolvable chosen/options = null, not a guess.

---

## Per-menu extraction (13)

### research — technology panel · all factions
- **Chosen:** event `ResearchStarted`, field `tech` (clean key, never null); player if `faction`==player. Drop AI noise (`in_player_turn:false`).
- **Options:** current = `ui_components menu_open panel="technology"` (rendered set; truncates off-screen) ∪ twcontrol `tree` of the tech panel. Full set = game-DB `technologies` filtered by completed-tech state (`state_tech`) + prereqs. v7: generic deep sweep captures every tech node + label + `dy_cost`.
- **Features:** game DB (cost/tier/branch/effect) by tech key; twcontrol `dy_cost` gives displayed cost where swept.

### recruit — recruitment panel · all factions
- **Chosen:** the `_recruitable` `ComponentLClickUp` (component ends `_recruitable`; in v5 the click `component` is null → identify via the adjacent `[ui] path` leaf ending `_recruitable`). Unit key = `structurer._strip_unit(leaf)`. **Pool** (local/global) = the container token after `list_box >` in the `[ui]` path (`local1`→local, `global`→global). Attribute by `in_player_turn`.
- **Options:** `ui_components menu_open panel="recruitment"` — each `_recruitable` with `source`∈{local,global}. Full roster = game-DB units filtered by settlement buildings + tech + pool rules (for datapoints the digest missed).
- **Features:** cost/upkeep = twcontrol `…RecruitmentCost|Cost` / `…UpkeepCost|Upkeep` (where swept) or game DB; unit category = TWSTATE `unit.category`.

### building — construction popup (per settlement slot) · all factions
- **Chosen:** event `BuildingConstructionIssuedByPlayer` (`build_key`/`build_record_key` NULL on this build). Recover the building key from the adjacent `[ui]` path segment (`…|slot_parent|<building_key>|square_building_button`), else the ordered `BuildingCompleted` join (process issues earliest-first per region; claim earliest unclaimed `BuildingCompleted(region, building, compl_turn≥issue_turn)`; overlaps with no slot disambiguation → null, never guess). v7 mod one-liner populates `build_key` directly.
- **Options:** `ui_components menu_open panel="construction"` per slot ∪ game-DB `building_chains`/`building_levels` valid for the slot's `slot_type` (TWSTATE `slot` / `state_slot`) given tech/resources.
- **Features:** game DB `building_levels` (cost/tier/income/effect).

### skills — character skills panel · all factions
- **Chosen:** event `CharacterSkillPointAllocated`, field `skill` (NOT `skill_key`, which is null). **Deliberate vs engine-auto:** drop allocations within ±0.1s of a `CharacterRankUp` for the same `char_cqi` (auto rank-up passives). Attribute by `char_faction`.
- **Options:** `ui_components menu_open panel="skills"` (all nodes + state) ∪ game-DB `character_skill_node_set` for the char's `subtype`.
- **Features:** game DB skill tables (tier/effect).

### occupation — settlement-captured decision · any faction (only on capturing a settlement)
- **Chosen:** event `CharacterPerformsSettlementOccupationDecision`, field `occupation_decision` (numeric code, e.g. `"1063"`; `occupation_decision_key` null). Map code→name via the occupation panel's **positional** labels (occupy/loot/sack/raze by x-order) / twcontrol sibling-card sweep. v7 one-liner populates the key.
- **Options:** `ui_components menu_open panel="occupation"` positional (4 options by x) / twcontrol sweep.
- **Features:** option identity; effect from DB if needed.

### post_battle_captives — after a battle with prisoners · faction-agnostic
- **Chosen:** event `CharacterPostBattleCaptureOption`, field `captive_outcome_key` (human-readable: kill/enslave/release). Fully self-sufficient from bytes.
- **Options:** `ui_components menu_open panel="post_battle_captives"` — the 3 fixed buttons `button_captive_option_{kill,enslave,release}`.
- **Features:** option identity (self-describing).

### pre_battle — on engaging a battle · faction-agnostic
- **Chosen:** no single event — triangulate: `BattleBeingFought.autoresolved==true` (autoresolve), battle completed without autoresolve (fought manual), `CharacterWithdrewFromBattle` (retreat). Attribute by human `char_cqi` (NOT `in_player_turn` — defensive fire fires on the AI turn).
- **Options:** `ui_components menu_open panel="pre_battle"` (autoresolve/retreat/attack buttons) ∪ fixed button-set by battle-type; tooltips in `menu_full`/twcontrol.
- **Features:** button identity + tooltips.

### rites — HEF-style rite factions
- **Chosen:** event `RitualStartedEvent`, field `ritual_rkey` (`ritual_key` null), `ritual_category=="STANDARD_RITUAL"`, `performing_faction`==player. (Event fires for ALL factions with `faction` null and `in_player_turn:true` even for AI — filter on `performing_faction`.)
- **Options:** `ui_components menu_open panel="rites"` (keyed rite options + state) ∪ game-DB rituals.
- **Features:** game DB (name/cost/effect).

### great_game_rituals — Slaanesh/mono-god Chaos factions
- **Chosen:** event `RitualStartedEvent`, `ritual_rkey`, `ritual_category=="GREAT_GAME_*"`, `performing_faction`==player.
- **Options:** `ui_components menu_open panel="great_game_rituals"` (the 8-option universe + locked/inactive/active/clickable state).
- **Features:** availability from the scrape; effect/cost magnitudes from game DB.

### event (dilemma) — all factions (content varies)
- **Chosen:** pair `DilemmaIssuedEvent` (`dilemma`=key, `choice`=null) → the following same-`dilemma` `DilemmaChoiceMadeEvent` (`choice`=index, `choice_key`=ordinal). **Exclude** `ScriptEventStartTransientIntervention "[EVENT]…"` advice and `IncidentOccuredEvent` notifications.
- **Options:** NOT a scraped panel in v6 → game-DB `cdir_events_dilemma_choice_detail` keyed by `dilemma` key (full ordered choice set + labels), and/or twcontrol `events` tree when swept (`CcoCdirEventsDilemmaChoiceDetailRecord<key><ORDINAL>` + `button_txt` label + payload). v7 generic sweep captures it live.
- **Features:** DB + `local_en` for choice labels/effects.

### item — equipment/ancillary panel · all factions
- **Chosen:** event `CharacterAncillaryGained` field `ancillary` (record key; `ancillary_key` null) or `CharacterArmoryItemEquipped.item_variant`, for a player `char_cqi`, **within** an equipment/character panel-open session (drop the many auto-grants outside a session). The scraped `CcoCampaignAncillary<cqi>` → record key via the adjacent `[ui] …|CcoCampaignAncillary<cqi>|ancillary_entry` join for the clicked item.
- **Options:** `ui_components menu_open panel="equipment"` lists the pool as **opaque cqis** (`text` empty). Un-clicked pool identities are NOT resolvable in v6 (no cqi→key bridge). **v7 mod one-liner:** `cco("CcoCampaignAncillary", cqi):Call(<record-key accessor>)` on the equipment sweep → every pool cqi → its ancillary key.
- **Features:** game DB `ancillaries` by key.

### diplomacy — all factions
- **Chosen:** coarse outcomes only in v6: `FactionLeaderSignsPeaceTreaty`/`DeclaresWar`, `TradeRouteEstablished`, `DiplomaticOfferRejected` (counterparty accessors null). Committed/state-changing subset via `[ui] faction_row_entry` + diplo-diff.
- **Options:** v6 = faction rows only; the deal-term UI is captured in twcontrol `diplomacy_dropdown` sweeps but those carry only `seq`+`turn` → **un-attributable** to a datapoint. **v7 attributable per-run sweep** captures the term controls tied to the datapoint.
- **Features:** from the attributable sweep + DB.

### army-stance — all factions
- **Chosen:** event `ForceAdoptsStance`, field `stance` (numeric code; `faction`/`char_cqi` null → attribute by `mf_cqi`); stance name via the same-`mf_cqi` force-dump stance string enum. v7 one-liner: stance code→name.
- **Options:** never fires a `PanelOpenedCampaign` in v6 → the stance row isn't scraped. **v7 hooks the force stance widget**; faction stance repertoire from game DB.
- **Features:** game DB stance table (name/effect).

---

## What v7 capture changes (so this doc's "needs a sweep / not scraped" rows become bytes-native)
- Remove the `PANELS` allowlist → generic `roots`+`tree` sweep of any open panel (captures dilemma, and future menus, with full labels/costs).
- Deep `tree` (not shallow `find`) → every option's label/cost/tooltip/availability (fills option `text`).
- Per-run **attributable** sweep stream (t + turn + panel) → fixes diplomacy/stance/dilemma attribution.
- Mod one-liners → `build_key`, `occupation_decision_key`, recruit unit/pool, reverse-diplo `has_as_vassal`
  (vassal count), dilemma `choice_key` label, ancillary `cqi→key`, stance code→name.
Static features (cost/tier/effect magnitudes) always come from the one-time game-DB extraction, joined by
the record keys captured above.
