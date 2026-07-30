# MENU_EXTRACTION.md — verified per-menu data-extraction recipes for the advisor

> This document is the **foolproof, executable spec** for pulling the advisor's data out of a
> recorded run. Every recipe here has been verified against a real **v6** run by extracting the
> data — not assumed. If a fresh agent follows a section and cannot get the data, the SECTION is
> wrong; fix the section, not the agent. Sections are added only after live+captured verification.
>
> Verified against run `D:/twdata/runs/human/20260727_144203` (recorder_version **v6**).

---

## 0. What the advisor needs (the requirement every recipe must satisfy)

The advisor scores the options at a decision point. For **each decision** it needs FOUR things:

1. **Full option set** — *every* option available at that moment (not just the chosen one), so it
   can score options the player did **not** pick.
2. **Per-option features** — enough to score each option:
   - identity: the option **key** → human name
   - **availability** at that moment (buildable-now / locked / already-owned / unaffordable)
   - **scoring features**: cost, time, effects/tier (from the game DB)
3. **Chosen** — which option the player actually took.
4. **Decision context (state)** — the value-relevant state at decision time: turn, faction,
   treasury/income, and the local state the decision acts on (e.g. the region + its current
   buildings). Lets the advisor learn *value*, not just imitate.

A recipe is only "done" when all four are obtainable. Where a leg is not yet obtainable, the recipe
says so explicitly with the exact blocker.

---

## 1. Where the data lives — v6 run layout

Find valid v6 runs (never hand-pick a path):

```python
import sys; sys.path.insert(0, r"D:/tw_stack/runs"); import runs
v6 = [r for r in runs.list_valid_runs() if r["recorder_version"] == "v6"]
# each r: {"run", "path", "recorder_version", "player_faction", "campaigns":[...]}
```

A v6 run directory `<run>/` contains:

| Path | Stream | Holds |
|---|---|---|
| `meta.json` | run meta | `recorder_version`, `t0_epoch`, `screen`, `game_dir` |
| `ui_components.jsonl` | **UI menus** | `menu_open` (panel + **`options[]`**), `ui_status` |
| `events.jsonl` | **input/peripheral** | `mouse_down/up` (x,y), `key_*`, `move`, `shot`, `focus`, `log_tail` (copy-metadata only: `src`,`dst`,`bytes`) |
| `logs/script_log_*.tail` | **game state (TWSTATE)** | one JSON per line prefixed `TWSTATE ` — `kind` ∈ {`event`,`slot`,`region`,`faction`,`char`,`unit`,`force`,`*_pooled`,`tech_count`,`player_snapshot`,…} |

Notes that bite (each cost a prior agent real time):
- The game-state stream is **inside `logs/*.tail`**, NOT in `events.jsonl`. `events.jsonl`'s
  `log_tail` records are just `{src,dst,bytes}` copy metadata. `os.listdir(run)` is non-recursive —
  the `logs/` subdir is easy to miss.
- Each TWSTATE line is `... TWSTATE {json}` — slice from the `TWSTATE {` marker, then `json.loads`.
- The `logs/script_log_*.tail` files are **huge** (GBs). Stream them line-by-line; never read whole.
- A run is **multi-campaign** (several turn-1 sessions, different factions). Filter to the human
  faction's campaign; the human faction is the `kind:faction` record with `"is_human":true`.

**Offline game-data reference** — `D:/tw_stack/advisor/reference/reference.sqlite`, table
`loc(key TEXT, text TEXT)` = 241,972 localisation rows (every record key → its in-game name/desc).
Built from the game packs by `D:/tw_stack/advisor/reference/build_reference.py`.

---

## 2. MENU: BUILDING (construction browser)

**Status:** option set ✓ · availability ✓ · name ✓ · chosen ✓ · context ✓ ·
**cost/effects features = game-DB table decode PENDING** (names done; numeric features not yet).

### 2.1 Option set + availability  — `ui_components.jsonl`
Filter `kind == "menu_open"` and `panel == "construction"`. Field `options` is the full list shown:

```json
{"t":..., "kind":"menu_open", "panel":"construction", "n":3, "options":[
  {"id":"wh2_main_hef_settlement_minor_1","key":"wh2_main_hef_settlement_minor_1",
   "clickable":false,"state":"built", "visible":true,"text":"","text_label":null,"x":999,"y":719,"w":74,"h":74},
  {"key":"wh2_main_hef_settlement_minor_2","clickable":true, "state":"normal", "x":999,"y":613,"w":74,"h":74},
  {"key":"wh2_main_hef_settlement_minor_3","clickable":false,"state":"greyed", "x":999,"y":507,"w":74,"h":74}
]}
```

- **`key`** = the building key (a `building_levels` record). This is the option identity.
- **`state`** semantics (verified values): `built` = already constructed in that slot ·
  `normal` (with `clickable:true`) = **buildable right now** · `greyed` = shown but locked
  (tech/resource/tier not met) · `hover` = same as normal, cursor is over it.
- `x,y,w,h` = on-screen rect of the option card (used for chosen-click correlation, §2.4).
- The construction browser is per-slot; one `menu_open` = the option set for the slot the player
  opened. Multiple opens across a session cover multiple slots/categories.
- `text`/`text_label` are empty here (leaf read only) — names come from the DB (§2.2), not inline.

### 2.2 Option name  — `reference.sqlite`
Building display names are **culture-specific**. Get the human faction's culture from the faction
dump (§2.5, field `culture`, e.g. `wh2_main_hef_high_elves`), then:

```sql
SELECT text FROM loc WHERE key = 'building_culture_variants_name_' || :building_key || :culture_key;
-- e.g. key  building_culture_variants_name_wh2_main_hef_settlement_major_1wh2_main_hef_high_elves -> "Hamlet"
-- robust fallback (no culture needed): WHERE key LIKE 'building_culture_variants_name_'||:building_key||'%'
```
Description (culture-independent): `building_short_description_texts_short_description_<building_key>`.

### 2.3 Per-option scoring features (cost / build time / effects)  — **PENDING**
Not yet extractable. `reference.sqlite` currently holds **localisation only** (names/descriptions).
The numeric features live in `db.pack` tables that are not yet decoded into the reference:
- `building_levels` → gold cost, construction turns
- `building_effects_junction` (+ `effects`) → the effect bundle each building grants
Blocker: the PFH5 reader (`build_reference.py`) can extract these table blobs but the **row schema
decode** is not implemented. Until then, options can be named + ranked by availability, but not
scored by cost/effect from captured data alone.

### 2.4 Chosen  — `logs/script_log_*.tail` (+ correlation)
- **Decision event:** TWSTATE `kind=="event"`, `event=="BuildingConstructionIssuedByPlayer"`.
  Carries `turn`, `in_player_turn`, and **`garrison`** = the region key. The building key in this
  event is **null** (`"building":null`) on this patch — do NOT rely on it.
- **Recover the chosen key** (in priority order):
  1. **click-rect correlation** — take the `menu_open{panel:"construction"}` active at that time;
     find the `events.jsonl` `mouse_up` whose `(x,y)` falls in an option's `(x,y,w,h)` → that
     `option.key` is the chosen building, captured at decision time.
  2. **`BuildingCompleted`** — later TWSTATE `kind=="event"`, `event=="BuildingCompleted"`, carries
     `building` = the real key + `garrison` = region. Map back to the issue in the same region.
     (Verified: `{"event":"BuildingCompleted","building":"wh3_main_dae_growth_gold_kho_1","garrison":"wh3_main_combi_region_volcanos_heart"}`.)
  3. **slot state-diff** — a `kind:slot` for that region flips `has_building`→true with a new
     `building` key across turns (§2.5).

### 2.5 Decision context (state)  — `logs/script_log_*.tail`
- **Current buildings** (what's in every slot, every turn): TWSTATE `kind=="slot"`:
  `{"kind":"slot","turn","region","slot_index","slot_type","slot_type_name","has_building","building":<key>}`.
  Verified: `{"kind":"slot",...,"has_building":true,"building":"wh2_main_lzd_settlement_major_1"}`.
  `(region, slot_index)` is the stable slot identity.
- **Faction value state:** TWSTATE `kind=="faction"` with `"is_human":true`:
  `treasury`, `income`, `net_income`, `expenditure`, `regions`, `subculture`, **`culture`**, `at_war`, `turn`.
  Verified: `{"kind":"faction","faction":"wh2_main_hef_nagarythe","is_human":true,"treasury":5000,"income":3000,"culture":"wh2_main_hef_high_elves",...}`.
- **Region state:** TWSTATE `kind=="region"` (owner, public_order, growth, gdp, corruption, num_buildings…).

### 2.6 Worked target (what an assembled building decision record looks like)
```json
{
  "menu": "building",
  "turn": 1,
  "region": "wh3_main_combi_region_the_monoliths",
  "context": {"treasury": 5000, "income": 3000, "culture": "wh2_main_hef_high_elves",
              "current_buildings": ["wh2_main_hef_settlement_major_1", "wh2_main_hef_port_1"]},
  "options": [
    {"key": "wh2_main_hef_settlement_minor_2", "name": "…", "state": "normal", "buildable": true,
     "cost": null, "effects": null},        // cost/effects null until §2.3 is done
    {"key": "wh2_main_hef_settlement_minor_3", "name": "…", "state": "greyed", "buildable": false}
  ],
  "chosen": "wh2_main_hef_settlement_minor_2"
}
```
The advisor is trainable from this **once §2.3 (cost/effects) lands**; option set, availability,
chosen, and value-context are all obtainable from captured v6 data today.

---

---

# MASTER COVERAGE REPORT (all menus, offline v6 forensic audit)

All verdicts below were verified against the one v6 run `D:/twdata/runs/human/20260727_144203`
(multi-campaign: sessions `1449`=Nagarythe/High-Elf, `1539`=Wood-Elves/Orion, `2140`=Nagarythe)
plus the offline `reference.sqlite` loc DB and `db.pack`. **Nothing was launched.**

## 3. Coverage table

Legend: `COV`=derivable offline now · `DEC`=NEEDS_DB_DECODE (offline table decode, no re-record) ·
`PART`=partial · `MISS`=missing · `CAP`=needs a mod/recorder change + a NEW recording.

| Menu | Panel captured | Option set | Availability | Chosen | Features | Context | **Overall** | Capture change? |
|---|---|---|---|---|---|---|---|---|
| **building** (construction) | yes | COV | COV | COV | DEC | COV | **NEEDS_DB_DECODE** | no |
| **recruitment** | yes | COV | COV | COV | DEC | COV | **NEEDS_DB_DECODE** | no |
| **technology** | yes | COV | COV | COV | DEC | COV | **NEEDS_DB_DECODE** | no |
| **skills** | yes | COV | COV | COV | DEC | COV | **NEEDS_DB_DECODE** | no |
| **occupation** | yes | COV | COV | COV | **COV** | COV | **COVERED** | no |
| **post_battle_captives** | yes | COV | COV | COV | DEC | COV | **NEEDS_DB_DECODE** | no |
| **rites_rituals** | yes | COV | PART | COV | DEC | COV | **NEEDS_DB_DECODE** | no |
| **dilemmas_incidents_events** | no (reconstructed) | COV | PART | COV | DEC | COV | **NEEDS_DB_DECODE** | no |
| **equipment_ancillaries** | yes | PART | PART | COV | DEC | COV | **NEEDS_CAPTURE_CHANGE** | **yes** |
| **diplomacy** | yes (rows only) | PART | PART | COV | MISS | COV | **NEEDS_CAPTURE_CHANGE** | **yes** |
| **army_stances** | no | MISS | MISS | COV | DEC | COV | **NEEDS_CAPTURE_CHANGE** | **yes** |
| **lords_heroes_recruit** | yes (roster only) | PART | MISS | COV | DEC | COV | **NEEDS_CAPTURE_CHANGE** | **yes** |
| **edicts** | no | MISS | PART | COV | DEC | COV | **NEEDS_CAPTURE_CHANGE** | **yes** |

Recorded `menu_open` panel volumes (this run): army 145 · recruitment 32 · pre_battle 22 ·
post_battle_captives 19 · skills 17 · diplomacy 13 · equipment 11 · construction 9 · occupation 8 ·
technology 7 · rites 2 · lords_heroes 2. No panel for edicts, army-stances, or dilemmas.

## 4. Grouping by what it takes to make each menu advisor-ready

### 4A. FULLY COVERED offline — advisor-ready **now**
- **occupation** — every leg (option set, availability, chosen, *features incl. name+effects text*,
  context) is offline-derivable from v6 + loc. Only per-instance runtime magnitudes (computed sack
  gold, colonise cost) are absent everywhere — a universal limitation, not a gap in this pipeline.

### 4B. NEEDS_DB_DECODE only — offline, **no re-record**; the ONLY missing leg is numeric cost/effects
All four other legs (option set, availability, chosen, context) are already extractable; names come
from `reference.sqlite` loc; the sole blocker is `build_reference.py::decode_db_tables` (a documented
TODO) which would materialise numeric cost/effect/tier values from `db.pack`. Fully offline.
- **building** — decode `building_levels` (gold/turns) + `building_effects_junction`→`effects`.
- **recruitment** — decode `main_units_tables` + `land_units_tables` (cost/upkeep).
- **technology** — decode `technologies_tables` (cost/turns) + `technology_effects_junction_tables`.
- **skills** — decode `character_skills_tables`, `character_skill_level_to_effects_junctions_tables`,
  `character_skill_nodes/_node_links_tables`, `character_skill_level_details_tables`.
- **post_battle_captives** — decode `campaign_post_battle_captive_options_tables`→effect-bundle+resource-cost.
- **rites_rituals** — decode `rituals_tables` + `ritual_payloads_tables` +
  `ritual_payload_resource_transactions_tables`/`_effect_bundles_tables`. (Also a minor availability
  caveat: binary state only, lock-reason recoverable statically from loc `campaign_group_rituals_unlock_text_*`.)
- **dilemmas_incidents_events** — no UI panel, but the option set is fully reconstructable from the
  event's `dilemma` key + loc (static DB data); decode `cdir_events_dilemma_choice_details` +
  `effect_bundles_to_effects_junction` for per-choice numeric effects.

### 4C. NEEDS_CAPTURE_CHANGE — a datapoint is genuinely not in v6; requires a mod/recorder change **and a fresh recording to validate**
Each entry lists the EXACT missing datapoint, the mod/recorder change, and that re-recording is required.

- **equipment_ancillaries**
  - **Missing:** the equipment panel emits only the ancillary **instance cqi** per option
    (`{"id":"CcoCampaignAncillary55","key":"55"}`); `text`/`text_label` are always empty and **no v6
    stream maps an instance cqi → ancillary record key**, so option rows are un-nameable and
    availability can't be bound to a named ancillary.
  - **Change:** recorder must emit each `CcoCampaignAncillary` option's `ancillary_key` (and/or its
    `GetStateText` label) so cqi→record_key is on disk.
  - **Re-record:** yes — needs a NEW recording to validate. *(Chosen is already covered via the
    `FactionGainedAncillary`→`CharacterAncillaryGained` event pair; numeric effects are a further DEC.)*

- **diplomacy**
  - **Missing:** the **deal-assembly sub-panel** — proposal/clause list, payment slider, and the
    trade / NAP / military-access / defensive-alliance / vassal / gift-region toggles + their
    per-clause pickable/greyed state + terms (gold lump, per-turn payment, regions/tech exchanged).
    v6 scrapes only faction **rows** (`faction_row_entry_<key>`); 0/389 options carry any text; every
    diplomacy event has `proposer/recipient/second_faction/target_faction/payment == null`.
  - **Change:** extend the recorder to scrape the diplomacy deal-assembly sub-panel and emit the
    proposed clauses + payment values.
  - **Re-record:** yes. Deal terms are a **capture** gap, not a DB-decode gap — no offline decode can
    recover the specific terms of a negotiation that were never recorded. *(Chosen deal type +
    counterparty are covered via `kind:diplo` matrix diff + initiator events; counterparty name via loc.)*

- **army_stances**
  - **Missing:** the **stance button bar** (full stance option set) and per-stance
    pickable/locked/greyed/already-active state. The `army` panel captures only unit contents; grep
    `stance` over ui_components.jsonl AND events.jsonl = 0 hits; `ForceAdoptsStance` events carry
    `component:""`.
  - **Change:** add a recorder/mod scrape enumerating the stance button group (each button key +
    enabled/greyed) at decision time.
  - **Re-record:** yes. *(Chosen is covered: `ForceAdoptsStance` integer enum intersected with the
    player's `kind:force` mf_cqi set, enum decoded via the co-logged `kind:force` stance STRING.
    Numeric effects are a further DEC via `campaign_stance` + effect junctions.)*

- **lords_heroes_recruit**
  - **Missing:** the **recruit pool** ("Recruitment Options": Global renown candidates + regional),
    each candidate's availability (recruitable/greyed/unaffordable), and per-candidate cost
    (renown + gold). The v6 `lords_heroes` panel is the **existing-character roster** (dismiss side,
    `character_row_<cqi>`), NOT the recruit pool — which is pixel-only (`shots/02602.jpg`).
  - **Change:** mod must dump the recruit pool (candidate subtype keys + recruitable/greyed flags +
    renown/gold cost).
  - **Re-record:** yes. *(An actual completed recruit IS covered via `CharacterCreated`
    (`in_player_turn:true` + player faction) → subtype→loc name; numeric candidate cost is a further DEC.)*

- **edicts**
  - **Missing:** the game-rendered **edict option set** per province (every option, not just chosen)
    + per-option pickable/locked/greyed state. No edicts `menu_open`; grep
    `edict|commandment|incentive|initiative` over ui_components.jsonl AND events.jsonl = 0. The edict
    UI is a HUD commandment stack (`hud_campaign|BL_parent|stack_incentives`), not a modal, so the
    watch trigger never scans it.
  - **Change:** add `PANELS['edicts']` (root `hud_campaign|BL_parent|stack_incentives|clip_parent|stack_background`,
    option_re for `button_*edict*/*commandment*`) — the bus path is already proven in
    `twapi/verbs/province.py` (`_EDICT_DROPDOWN`/`_edict_dropdown()`/`_is_edict_button`) — plus a
    HUD-stack open trigger.
  - **Re-record:** yes. *(Chosen is covered two ways: `kind:region` `selected_edict`→`active_edict`
    state-diff, and `FactionLeaderIssuesEdict.initiative_key`; province tie via region state-diff since
    `edict_province` is null. Offline fallback for the option universe = decode `db.pack`
    `provincial_initiative_records` by subculture — the HE edict universe, not the per-province render.)*

---

# 5. Extraction recipes (COVERED / NEEDS_DB_DECODE menus) — file → field → join

Each recipe below is offline-executable today for every leg **except** the numeric cost/effects noted
`[DEC]`. For the building recipe see §2. Shared helpers:
- Human faction = the `kind:faction` line with `"is_human":true` (per session). Its `culture` field
  drives culture-suffixed loc joins.
- Stream `logs/script_log_*.tail` line-by-line, slice from the `TWSTATE {` marker, `json.loads`.
- Names/descriptions: `reference.sqlite` `SELECT text FROM loc WHERE key=?`.

## 5.1 recruitment  — **NEEDS_DB_DECODE**
- **Option set + availability** — `ui_components.jsonl`, `kind=="menu_open" & panel=="recruitment"`.
  Each `options[]` = one recruit card: `key`=unit key (==`land_units` key), `id`=`key+"_recruitable"`,
  `source`=pool (`local`/`global`/`renown`/`allied`). Union `options[].key` across the opens near a
  turn = full set. Availability: `clickable:true`(active/hover/down)=pickable-now; `state:"inactive"`
  +`clickable:false`=locked. (Binary; panel gives no lock *reason*.)
  - Real: `{"id":"wh2_main_hef_inf_spearmen_0_recruitable","key":"wh2_main_hef_inf_spearmen_0","clickable":true,"state":"active","source":"global"}` (t=868.931). Source census: global=147, local=90.
- **Chosen** — TWSTATE `ComponentLClickUp` with `component ~ ^<unit>_recruitable$` (carries turn +
  in_player_turn); corroborate with `UnitTrained` (`unit_faction==human`) and
  `RecruitmentItemIssuedByPlayer`. Source-of-chosen (pool) recovered by joining the click to the
  nearest `menu_open` down/hover card.
  - Real: `{"event":"UnitTrained","turn":2,"unit":"wh2_dlc10_hef_inf_shadow_warriors_0","unit_faction":"wh2_main_hef_nagarythe"}`.
- **Features** — name: loc `land_units_onscreen_name_||unit_key` → `'Shadow Warriors'`. `[DEC]`
  cost/upkeep: decode `db.pack` `main_units_tables` + `land_units_tables`.
- **Context** — `kind:faction` `is_human:true`: turn/treasury/income/net_income.
  Real: `{"treasury":5000,"income":3000,"net_income":3000,"culture":"wh2_main_hef_high_elves"}`.

## 5.2 technology  — **NEEDS_DB_DECODE**
- **Option set** — `menu_open & panel=="technology"`; each `options[]`=tech node `{key,x,y,w,h}`.
  Union keys across the 7 opens = complete set (74 keys: HEF 49-node tree + Wood-Elf 25 + 5 dlc27).
  Off-screen nodes are key-only (geometry/state null) per snapshot.
- **Availability** — `options[].state` ∈ {available, locked, complete, researching} + `clickable`.
  Union across opens gives state for 60/74 keys; full authoritative availability alternatively from
  `db.pack technology_required_technology_junctions_tables` + completed set.
  - Real: `{key:wh2_main_tech_hef_0_00,state:available,clickable:true}`; `_0_04` locked.
- **Chosen** — TWSTATE `ResearchStarted` (mirror `ResearchCompleted`): `tech`=key (direct match),
  `faction`, `turn`. Attribute by session's human faction. Do NOT use faction dump's `researching`
  (null). Real: `ResearchStarted turn=1 tech=wh2_main_tech_hef_0_00`; WEF `ResearchCompleted turn=5
  tech=wh_dlc05_tech_2_morai_heg` exactly matches menu#5 panel (morai_heg=complete, ellinill=researching).
- **Features** — loc `technologies_onscreen_name_<key>` → `'The Endless Muster'`;
  `technologies_short/long_description_<key>`. `[DEC]` cost/effects: decode `technologies_tables` +
  `technology_effects_junction_tables`.
- **Context** — `kind:faction is_human:true` + `kind:tech_count {faction,completed}` per turn.

## 5.3 skills  — **NEEDS_DB_DECODE**
- **Option set + availability** — `menu_open & panel=="skills"`; `options[]` =
  `{key(skill key, globally unique), state, clickable, visible, x,y,w,h}`. State→availability:
  `available/hover`=pickable-now; `locked/locked_rank/locked_upgrade_rank`=locked;
  `locked_auto_unlock`=auto-grants; `maxed/maxed_hover/complete`=already-owned; `null`=tree node not
  rendered (still enumerated → full tree universe). `text/text_label/source` always empty → labels
  come from DB only. State histogram: locked=409, available=70, maxed=35, locked_auto_unlock=22, …
- **Chosen** — TWSTATE `CharacterSkillPointAllocated`: `skill`=allocated key (NB `skill_key` is null),
  `char_cqi`, `char_subtype`, `char_faction`, turn, in_player_turn. Isolate player: `char_faction==
  human` + `in_player_turn:true` + drop `*_agent_action_success_scaling`/`*_innate_*`/`*_dummy_*`.
  Join chosen↔panel by (session, char_cqi, turn) or the `{maxed,complete}`-set fingerprint (the
  chosen skill flips one option `available`→`maxed`). Event x,y is the portrait, NOT the node — no
  pixel-join. Real: `turn=1 char_cqi=56 wh2_dlc10_hef_alith_anar skill=wh_main_skill_emp_lord_campaign_iron_disciplinarian`.
- **Features** — loc `character_skills_localised_name_||key` → `'Iron Disciplinarian'`;
  `..._description_||key`. `[DEC]` cost/tier/effect: decode `character_skills_tables` +
  `character_skill_level_to_effects_junctions_tables` + node/link/level-detail tables.
- **Context** — which char via `CharacterSelected`; budget via `CharacterSkillPointAvailable`;
  rank via `CharacterRankUp`; economy via `kind:faction is_human:true`.

## 5.4 occupation  — **COVERED** (all legs incl. features)
- **Option set + availability** — `menu_open & panel=="occupation"` (8). `options[]`=
  `{id(numeric UI component id), label, state, clickable, visible, x, positional:true}`. Positional
  `label` (occupy/loot/sack/raze L→R) applied ONLY when exactly 4 cards; a 3-card offer falls back to
  `option0/1/2`. Unavailable options are OMITTED (card count is the signal), so which options were
  offered = the card set. Availability = `state`(active/hover/inactive) + `clickable`.
- **Chosen** — TWSTATE `CharacterPerformsSettlementOccupationDecision`, `in_player_turn:true`. Chosen
  option id = **`occupation_decision`** (== `options[].id`, unique per decision → joins without clock
  alignment). Settlement = `garrison` region key. NB `occupation_decision_key`/`settlement_option`
  are null on this patch — use the numeric id.
  Real: `{turn:1,char_cqi:56,garrison:"wh3_main_combi_region_shrine_of_ladrielle",occupation_decision:"1063"}`.
- **Features** — **the linchpin**: loc `culture_settlement_occupation_options_tooltip_<id>` keyed by
  the SAME numeric id → `"<Name>||<effects text>"`. Resolves stable ids (1063→Occupy, 1058→Loot &
  Occupy, 1078→Sack, 1070→Raze) AND dynamic 3-button ids the positional labeler missed
  (658217422→'Occupy Heathland', 441→'Raze'). Strictly better than positional labels. *(Only runtime
  numeric magnitudes — computed sack gold, colonise cost — are absent everywhere; not offline-recoverable.)*
- **Context** — `kind:faction is_human:true` by turn; settlement name via loc `regions_onscreen_<key>`.
  All 8 player decisions resolved (Shrine of Ladrielle=Occupy ×2, Quenelles=Occupy Heathland, … Massif Orcal=Raze).

## 5.5 post_battle_captives  — **NEEDS_DB_DECODE**
- **Option set + availability** — `menu_open & panel=="post_battle_captives"` (19). `options[].key`=
  `button_captive_option_{kill,enslave,release}` (constant n=3); `state`(active/hover)+`clickable`.
  `text`/`text_label` empty → names from DB. All 19 captures active/clickable (no locked instance).
- **Chosen** — TWSTATE `CharacterPostBattleCaptureOption`, `in_player_turn:true` (drops 1730 AI →
  13 player). `captive_outcome_key` ∈ {kill,enslave,release}; **`captive_record_key`** = DB option id.
  Real: `{captive_outcome_key:"enslave",captive_record_key:"537208489",char_cqi:56,turn:1}`. Player
  tally: kill 7 / enslave 5 / release 1.
- **Features** — loc `campaign_post_battle_captive_options_onscreen_name_||record_key` →
  `537208489`→'Force Labour'; `..._description_||record_key`. For the non-chosen buttons, map
  player subculture + category → record_key via `db.pack campaign_post_battle_captive_options_tables`
  (95 rows). `[DEC]` magnitudes: that table links record_key→effect-bundle + resource-cost; decode
  effect_bundles→effects.
- **Context** — `kind:faction is_human:true` at event turn.

## 5.6 rites_rituals  — **NEEDS_DB_DECODE**
- **Option set** — `menu_open & panel=="rites"`; `options[].key`=ritual key (e.g.
  `wh2_main_ritual_hef_vaul`), `id="CcoCampaignRitual30"+key`, `n`=count. `text`/`text_label` empty.
- **Availability** — per-option `state`(active/inactive)+`clickable`; binary only. Lock REASON
  recoverable statically from loc `campaign_group_rituals_unlock_text_*<key>` (e.g. vaul → "unlock
  after researching 3 technologies"). *(Weakness: the one player ritual decision (vaul, turn 11) had
  no coincident rites open — chosen joins BY KEY, but option-set at that exact moment wasn't sampled;
  a capture-frequency issue, not a missing data type.)*
- **Chosen** — TWSTATE `RitualStartedEvent`, `performing_faction==human` + `in_player_turn:true`;
  `ritual_rkey`==option.key for `STANDARD_RITUAL`. Real: `{turn:11,ritual_rkey:"wh2_main_ritual_hef_vaul",ritual_category:"STANDARD_RITUAL"}`.
  MUST filter `performing_faction` (fires for all AI factions too).
- **Features** — loc `rituals_display_name_<key>` → 'Invocation of Vaul';
  `ritual_additional_ui_explanation_texts_onscreen_text_<key>`, effect-bundle title/desc. `[DEC]`
  numeric cost/cooldown/magnitudes: decode `rituals_tables` + `ritual_payloads_tables` +
  `ritual_payload_resource_transactions_tables`.
- **Context** — `kind:faction is_human:true` (treasury/income/has_rituals/…) by turn.

## 5.7 dilemmas_incidents_events  — **NEEDS_DB_DECODE** (no panel; reconstructed from event + loc)
- **Chosen** — TWSTATE `DilemmaChoiceMadeEvent`: `dilemma`=key, `choice`=0-indexed ordinal int,
  `choice_key`∈{FIRST..FOURTH}, turn, faction, in_player_turn. **WARNING: `choice==0` is falsy —
  don't drop with a truthiness filter.** Pair with preceding `DilemmaIssuedEvent`. Filter faction to
  the session's human. Real: `{turn:8,faction:'wh2_main_hef_nagarythe',dilemma:'wh2_main_hef_intrigue_you_can_dance',choice:3,choice_key:'FOURTH'}`.
- **Option set + labels** — loc, keyed by dilemma+ordinal. Full set = every row
  `LIKE 'cdir_events_dilemma_choice_details_localised_choice_label_'||dilemma||'%'`; strip prefix →
  ordinal word; text = choice name. Title = `dilemmas_localised_title_||dilemma`. The event's
  `choice_key` maps chosen ordinal directly. Real: `you_can_dance` = 4 choices (FIRST 'Dance with the
  matriarch of Chrace' … FOURTH 'Do not dance at all'=chosen).
- **Availability** — PARTIAL: no panel → per-choice locked/greyed not in v6; choices practically
  all-pickable; true conditions live in `db.pack cdir_events_dilemma_choice_details`.
- **Features** — labels/effect-bundle titles via loc. `[DEC]` per-choice numeric effects: decode
  `cdir_events_dilemma_choice_details` + `effect_bundles_to_effects_junction`.
- **Context** — `kind:faction is_human:true` at turn.
- **Incidents** — `IncidentOccuredEvent` (key in the reused `dilemma` field) + `faction` + `turn`;
  informational, no choice set. `EventFeedEventRecordedEvent` likewise informational.

---

*Menus with `NEEDS_CAPTURE_CHANGE` overall (equipment_ancillaries, diplomacy, army_stances,
lords_heroes_recruit, edicts) have their chosen/context legs covered — see the exact capture change +
re-record requirement in §4C. Their partial recipes are documented in the audit and are NOT repeated
as "executable now" here because the option-set/availability leg cannot be validated without a fresh
recording.*
