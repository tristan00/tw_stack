# Army-stances + Edicts — precise capture recipe (found live, 2026-07-28)

Both were flagged NEEDS_CAPTURE_CHANGE(hard) in the coverage audit. They are NOT hard — both are HUD
"incentive/stance" stacks under `hud_campaign|BL_parent`, captured by the SAME mechanism. This doc is
the exact recipe to develop the recorder + advisor support offline.

## The one mechanism (why the earlier scrape missed them)
`hud_campaign|BL_parent` holds three collapsible stacks:
`stack_incentives` (edicts), `land_stance_button_stack`, `naval_stance_button_stack`.
Each stack is `…|clip_parent|stack_background|<option buttons>` + a `stack_arrow` (expander) + a
`button_default`. **When collapsed, only the CURRENT option is `visible`; the rest are `visible=False`.**
The recorder's normal panel scrape descends only into VISIBLE nodes → it never sees the collapsed
options, AND these stacks don't fire `PanelOpenedCampaign` → the scan never triggers. That's the whole
reason both looked "uncapturable."

**Fix = enumerate by child-count, not by visible tree-walk.** The bus `find` handler returns a node's
direct children via `ChildCount + Find(i)`, which is NOT visibility-gated — so `find <stack_background>`
returns EVERY option button even while the stack is collapsed. VERIFIED live: `find` on the collapsed
`land_stance_button_stack|…|stack_background` (its own `visible=False`) returned all 13 stance buttons.

## ARMY STANCES
- **Root:** `hud_campaign|BL_parent|land_stance_button_stack|clip_parent|stack_background`
  (naval: `…|naval_stance_button_stack|clip_parent|stack_background`).
- **Option set:** `find` that root's `child_ids`. Each is `button_MILITARY_FORCE_ACTIVE_STANCE_TYPE_<KEY>`
  (+ `button_default`). **Stance key = the id with `button_MILITARY_FORCE_ACTIVE_STANCE_TYPE_` stripped**
  (SET_CAMP=Encamp, LAND_RAID=Raiding, MARCH, MUSTER, CHANNELING, ASTROMANCY, STALKING, TUNNELING,
  FIXED_CAMP, SETTLE, SET_CAMP_RAIDING, AMBUSH; `default`). No cqi resolve — the key is in the id.
  Real capture (Alith Anar): 13 buttons; `TUNNELING` state=`inactive` (unavailable), the rest `active`.
- **Availability per option:** `find <button>` → `state` (`active`=available, `inactive`=not); a child
  `icon_tech_required` present = tech-gated (check its own visible/state to confirm the lock is shown).
- **Chosen:** `ForceAdoptsStance` event already fires (field `stance` = NUMERIC code + `mf_cqi`). The
  numeric code needs a code→name map; the cleaner signal is the stance-button click
  (`ComponentLClickUp component == button_MILITARY_FORCE_ACTIVE_STANCE_TYPE_<KEY>` → readable key).
  (Live subagent scan is confirming which of these registers as the player flips stances.)
- **Current stance** (when only reading state): the button that stays `visible` while the stack is
  collapsed is the active one.

## EDICTS (province commandments)
- **Root:** `hud_campaign|BL_parent|stack_incentives|clip_parent|stack_background`.
- **Option set:** `find` root's `child_ids` → `button_<edict_key>` (+ `button_default`).
  **Edict key = the id with leading `button_` stripped** (e.g. `wh2_main_edict_hef_reaver_patrols`,
  `…_tribute_for_the_king`, `…_rebuild_lost_splendour`, `…_rally_citizen_militia`, `…_banish_corruption`).
  Names via `reference.sqlite` loc (edict/commandment name keys).
- **Availability:** button `state` (`active`/`hover`=selectable).
- **Chosen:** ALREADY CAPTURED — `kind:region` state dump carries `active_edict`
  (`r:get_active_edict_key()`) + `selected_edict` (`r:get_selected_edict_key()`) per province. No new
  event needed; join the edict option set to the region's active/selected edict.

## What to build offline
1. **Recorder** (`D:/tw_stack/ui-capture/ui_component_recorder.py`): add a capture path for these
   collapsible stacks that ENUMERATES BY CHILD-COUNT (find), not visible descent. Either a new PANELS
   entry variant with an "enumerate_children_of(root)" strategy, or a small dedicated poller that, when
   an army/province is selected, `find`s the three `stack_background` roots and emits a `menu_open`
   (`panel` ∈ {army_stances, edicts}) with `options:[{key, state, available, tech_gated}]`. Emit key =
   id-suffix-stripped. Because the stacks are always present when an army/settlement is selected, a
   selection-triggered or low-rate poll works (no PanelOpenedCampaign dependency).
2. **Advisor** (`decision_instances.py` PANEL_TYPE + `features.py`): add `army_stances`→`stance` and
   `edicts`→`edict`. Stance/edict featurizer: category + DB effects (campaign_stance / edict tables).
   Chosen: stance from ForceAdoptsStance/click; edict from region `active_edict`/`selected_edict`.
3. **Validation:** the parser/featurizer can be unit-tested offline against the live bus samples above;
   full end-to-end capture needs one fresh recording (game) with the new recorder — the user records it.
