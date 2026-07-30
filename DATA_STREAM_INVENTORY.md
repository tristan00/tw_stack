# WH3 Data-Stream Inventory + "Enable context viewer" findings

Purpose: a single reference for **every data source available in a live WH3 campaign**, what each
gives, and its limits — so tooling reads from the right stream instead of re-probing. Captured live
2026-07-29 on **Arbaal / Khorne (`wh3_dlc26_kho_arbaal`), turn 1**, with the game's **Options →
Modding → "Enable context viewer" = ON**. Numbers below are measured, not assumed.

Legend: **[measured]** = observed live this session · **[inferred]** = best-supported reasoning ·
**[TODO]** = still to confirm (e.g. the context-viewer OFF A/B; the web-research subagent).

---

## A. Live bus channels (the mod `twcontrol.lua`, over the command bus)

Reached via `Bus().send(channel, payload, timeout)` (bus project). Handlers present:
`roots, tree, find, children, clickidx, chars, setts, forces, hostiles, snapshot, eval` (+ actuators
`click, clickidx, move, move_leader, focus, autoresolve, end_turn`).

| channel | returns | limits / notes |
|---|---|---|
| `roots` | visible root panels: `id, visible, children`(count) | cheap; the reliable "what panel is open" signal |
| `tree <root> <depth> <count>` | full subtree: per node `id, state, visible, x, y, w, h` (**UI-virtual** coords, 1984×1116 ×1.2903→screen), `path` (embeds context/option keys), `context` (curated Cco id) | **default cap 500 nodes**, but the count arg overrides it — pulled **956 nodes, truncated=false** [measured]. **VISIBLE-ONLY descent**: a node with `visible==false` is NOT walked into (`twcontrol.lua:275`), so hidden subtrees (e.g. a collapsed build flyout) are pruned regardless of `count`. |
| `find <path>` | resolved node `describe` + `child_ids[]` + `child_contexts[]` | children enumerated **unconditionally** (incl. hidden) — but by name-path, which fails on deep context-id paths |
| `children`, `clickidx` | child enumeration / click-by-index helpers | index-based; survive context-id nodes |
| `chars` / `setts` / `forces` / `hostiles` | structured entity lists | this state: **3 / 1 / 154 / 8** [measured] |
| `snapshot` | faction, treasury, income, regions, turn | this state: treasury 5000, income 3080, 1 region, turn 1 [measured] |
| `eval <lua>` | arbitrary Lua → the `cm` / `cco` game script API | the widest lever; see B |

**Context (`Cco*`) coverage via the bus is SPARSE** [measured]: of 956 `technology_panel` nodes only
**11 carried a `context`, all `CcoCampaignFaction`** (not per-tech). The mod's `context_id()` only tries
**10 curated types** (`CcoCampaignUnit, CcoCampaignCharacter, CcoMainUnitRecord, CcoCampaignSettlement,
CcoCampaignRegion, CcoCampaignAncillary, CcoCampaignRitual, CcoCampaignBuildingChainRecord,
CcoCampaignFaction, CcoCampaignProvince`). Many option cards bind context-object types OUTSIDE this list
(e.g. `CcoTechnologyUiTabRecord`, `CcoCampaignBuildingSlot`, `CcoBuildingSetRecord`) and therefore return
no `context` over the bus — **but the option KEY is usually in the node `path`/`id`** (e.g. tech key
`slot_parent|wh3_main_tech_kho_6_7|technology_entry`), so the option-set is still recoverable.

## B. Game API via `eval` (cm / cco)

Confirmed working [measured]: `cm:get_local_faction_name(true)`, `region_list():num_items()`,
`...:item_at(0):name()`, `...:treasury()`, `is_currently_researching()`, and `cco("CcoType","key"):Call("Method")`
name resolution (from earlier sessions).

**NOT available**: a settlement "buildable buildings" enumerator — `settlement():get_buildable_buildings_list()`
**errors** [measured]. The CA script API does not expose a clean per-menu action-space; **option-sets come
from the UI tree or the reference DB, not the game API.**

## C. Script-log passive streams (`script_log_*.txt` in the game dir)

Written by the game+mod regardless of the bus. Three overlaid streams [measured, last 6000 lines]:

1. **TWSTATE events** — `ComponentLClickUp` (every click), `PanelOpenedCampaign` / `PanelClosedCampaign`
   (menu open/close — the recorder's capture trigger; components seen: `esc_menu, events,
   technology_panel, settlement_panel, units_panel, diplomacy`), and semantic events
   (`RitualStarted/Completed, CharacterSelected, MissionIssued, PooledResourceChanged`, and elsewhere
   `BuildingConstructionIssuedByPlayer, ResearchStarted, CharacterSkillPointAllocated, CharacterAncillaryGained`…).
2. **`[ui]` component traces** — see section D; this is the context-viewer stream.
3. **twstate per-turn state dumps** — faction / region / character state (the advisor's `context`/features base).

## D. "Enable context viewer" — VERDICT: adds nothing to our automated streams ⭐

**Question:** what does this setting give us that our existing methods (which all worked with it OFF)
didn't? **Answer, after a live A/B + authoritative research: essentially nothing for the automated
pipeline.** It is a human-facing interactive inspection overlay, not a data-stream enhancer.

### The `[ui]` script-log trace block (present in BOTH states)
The script log emits a describe block per **click** (not per hover):
```
[ui] <t>  uicomponent CcoTechnologyUiTabRecordkho_battle
          path from root:  root > technology_panel > tabs > CcoTechnologyUiTabRecordkho_battle
          position on screen: [44, 575], size: [133, 143], state: [selected_hover],
          visible: [true], priority: [40], interactive: [true]
```
It carries the component's **context-object id** (incl. types the bus `context` field can't resolve),
**full path**, **screen pixel rect**, and **state/visible/priority/interactive**. Useful — but it is
**click-driven**: `[ui] path-from-root` traces ran **1:1 with `ComponentLClickUp`** in every window
(ON: 17=17; OFF: 11=11). No excess hover-only blocks appeared in either state.

### Live A/B result [measured]
| signal | context viewer ON | context viewer OFF |
|---|---|---|
| `[ui]` path-from-root traces | 1:1 with clicks | 1:1 with clicks (**unchanged**) |
| bus `context` field populated | yes | **yes** (settlement_panel: 128/645 nodes carry `context` — CcoCampaignSettlement/Province/Faction/MainUnitRecord) |
| pure-hover (no click) `[ui]` blocks | none isolated | none isolated |
→ **No stream difference observed.** Bus context ids (`GetContextObjectId`) and the click-path `[ui]`
traces both work regardless of the setting. (Note: the `[ui]` describe-log itself is the separate WH3
**script-logging** mechanism / "script debug activator", already active — not this setting.)

### What the setting actually is [research — tw-modding.com wiki, well-sourced]
An **interactive on-screen debugging overlay** for modders, opened with the **`` ` `` (backtick)** key
once ticked in Options → Modding. You **middle-click** (or ALT+middle-click to disambiguate) any UI
element / unit / settlement / character to inspect its **CCO TypeID + ObjectID** (feed into
`cco(TypeID, ObjectID)`), browse the **Component Tree**, watch a live **Events Viewer**, and run an
**Expression Tester**; `CopyFullPathToClipboard()` gives the `find_uicomponent` path. It is **WH3-only**
and **explicitly can crash the game** on some elements/operations. It does **not** dump to script_log —
reading paths/ids is interactive.
Sources: tw-modding.com `Tutorial:Context_Viewer_(Warhammer_3)`, `Tutorial:Component_Context_Objects_(CCOs)`, `UI:Main_Page`.

### Practical verdict
- For **our automated pipeline**: **redundant.** We already read context-object ids over the bus
  (`GetContextObjectId`, the curated `Cco*` set) and click-paths from the script log — both independent
  of this setting. Leaving it OFF loses us nothing and avoids the documented crash risk.
- The one genuinely-richer artifact (per-touch describe with screen rect + non-curated context types) is
  the **click-driven `[ui]` log trace from script-logging**, which we already have; it does not extend to
  pure hovers, and a **bus-driven script does not trigger it at all** (synthetic `mouse move` logged 0).
- If we ever want the *full* context-object binding of an element the bus can't resolve, the tool's
  value is as a **one-off manual lookup** (middle-click → TypeID/ObjectID), not an automated feed.

Bus `context` coverage varies by panel [measured]: `technology_panel` 11/956 (all `CcoCampaignFaction`),
but `settlement_panel` 128/645 (settlement/province/faction/unit) — so entity-bound panels expose far
more curated context than option-grid panels. The option KEY is still in the node `path`/`id` regardless.

## E. Offline reference — `advisor/reference/reference.sqlite`

Tables `buildings (5259), building_chains (1943), tech, units, skills, rituals, loc, captive_*`. The
**full option universes** for DB-synthesis (names via `loc`). This is how `building`/`edict` get their
option-set without any UI capture.

## F. Recorder streams (`manager.py`, when recording)

`input` (mouse/keyboard events + click component ids), `logs` (tails the script_log incl. C+D above),
`ui-capture` (event-driven `menu_open` rows via bus find/tree on `PanelOpenedCampaign`), `shots` (opt-in).

## G. Visual — screenshots

Full-screen PNG capture (subagent-readable). Best for: reading on-screen text/values, verifying overlays,
and any case the bus can't enumerate. Delegate the actual reading to subagents (adversarial/informational).

---

## Per-menu: where the OPTION-SET and the CHOSEN come from (current best)

| menu | option-set source | chosen source | notes |
|---|---|---|---|
| **research** | **bus tree** — `technology_entry` nodes, key in `path`, `state` available/locked [measured: 36 entries, 6 available] | `ResearchStarted.tech` event | fully UI-capturable at rest; DLC tab needs tab-switch re-enum |
| **build** | **DB synthesis** (`features_db.building_options`, race-token, 1/chain) — live browser is transient/poll-only/visible-capped | `resolve_construction_choices` (click-path `[ui]` trace + `BuildingConstructionIssuedByPlayer`) | option-set now emitted (was `[]`); combined streams click+db+event [measured: 79 opts] |
| **recruit** | bus tree — `*_recruitable` cards (+ pool source) | `*_recruitable` `ComponentLClickUp` | |
| **diplomacy** | bus tree `diplomacy_dropdown` (WHO) + clicks (deal clauses) | `faction_row_entry_*` / `diplomatic_option_*` / `button_send` clicks + diplo state-diff | |
| **items** | equipment scrapes → `item_choices` (numeric ancillary ids → keys via loc) | add=`CharacterAncillaryGained`, remove=equipped scrape-diff (no event) | |
| **skills** | bus tree `skills` tree — `wh*_skill_*` cards | `CharacterSkillPointAllocated.skill` | |

General rule learned: **the option KEY is almost always in the component `path`/`id` even when the bus
`context` field is empty**; the `[ui]` trace (context viewer) adds the context-object binding + screen
rect for human interaction; the **DB is the fallback option universe** when the live UI is transient or
capped.

## Hard limits discovered (design around these)

1. `tree` **visible-only descent** — hidden subtrees never walked (build flyout is invisible until hover).
2. `tree` **default 500-node cap** — overridable via the count arg; big walks risk timeout / the
   whole-tree-walk crash the mod warns about.
3. bus **`context` field is sparse** (only 10 curated Cco types; most option cards bind others).
4. **synthetic bus mouse-moves do NOT trigger `[ui]` hover traces** — that stream needs real hardware
   interaction (or a driven click, which logs the clicked path only).
5. **no game-API option enumerators** — option-sets come from UI tree or the DB, never a clean getter.
