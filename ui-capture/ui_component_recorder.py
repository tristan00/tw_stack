r"""ui_component_recorder.py -- the OPTIONAL 4th recorder stream: at menu-open, capture every
KEY-EMBEDDED option and whether it is clickable, via the mod's find channel.

    python tools/ui_component_recorder.py --panel recruitment   # one-shot dump of a panel
    python tools/ui_component_recorder.py --scan                 # one-shot dump of every OPEN panel
    python tools/ui_component_recorder.py --watch                # event-triggered watch loop
  (normally run indirectly: `python tools/record.py --ui` adds this as the recorder's 4th thread)

WHY IT EXISTS
  The passive recorder captures POSITIVES -- what the human actually did. It cannot see the
  NEGATIVES -- the options that were available (clickable) or shown-but-disabled and NOT chosen.
  Those negatives are what a validity model has to learn. The game already computed validity and
  rendered it; the find channel reads that structure. So at each menu-open we list the panel's
  option rows with their clickable state -- ground-truth (state -> valid-option) labels.

WHY BUS-BASED, AND WHY OPT-IN
  Reading the UI needs the command bus (twcontrol). The main recorder is deliberately bus-free
  (a bus call can disturb live play), so this stream is OFF by default and, when on, degrades
  silently the instant the bus stops answering -- it never blocks or crashes the other streams.
  The bus IS armed during human play (twcontrol ships in the same tw.pack as twstate), so this
  works during play; the watcher self-reports `bus_available` / `bus_unavailable`.

WHY KEY-EMBEDDED ONLY (original user directive)
  Every option-bearing panel is KEY-EMBEDDED: its option leaves carry a game key --
  character_row_<cqi> (lords/heroes), row_entry_<province_key> (provinces),
  faction_row_entry_<faction_key> (known-factions / diplomacy), <unit_key>_recruitable
  (recruitment), wh*_skill_* (skills), wh*_tech_* (technology), wh*_... building cards
  (construction), CcoCampaignRitual<n><ritual_key> (High Elves rites). We record ONLY those
  leaves; positional cards (recruitment LandUnit<i>, the HE Intrigue court's option0..N faction
  dropdown) are skipped -- their identity needs a model join. A panel whose rows are purely
  positional simply records nothing for it.

WHY (NOW) ALSO CONTEXT-OBJECT (mod 6ab8bf1 -- POSITIONAL cards become identifiable)
  The mod's `find` channel now ALSO returns CONTEXT-OBJECT ids alongside the unchanged
  {result, child_ids[]}: `result.context` (the resolved node's own bound Cco* id) and
  `child_contexts[]` (parallel to child_ids -- each direct child's Cco* id, or null). A WH3 UI
  card that carries NO game key in any id still BINDS a context object the game reads to draw it,
  and that object's id names the entity. This unlocks the POSITIONAL cards the key-embedded rule
  had to skip. The ARMY BOX is the first: `units_panel|main_units_panel|units` holds positional
  `LandUnit N` (active) / `QueuedLandUnit N` (in-progress recruit) / `AgentUnit N` cards whose
  id carries no unit key -- but each card's `card_image_holder` child binds
  CcoMainUnitRecord:<unit_key> (the unit key VERBATIM, e.g. CcoMainUnitRecord:
  wh2_main_hef_inf_spearmen_0), for BOTH active and queued cards. So ONE find on a card returns
  that key in its child_contexts. This is the ONLY way to see a QUEUED RECRUIT: the model
  unit_list EXCLUDES queued recruits (military_force:unit_list() = COMPLETED units only; no
  recruitment-queue accessor exists), so which unit is being recruited is a UI+context read or
  nothing. enumerate_context_cards (below) is the GENERAL, reusable path: one find per card for
  its direct children's contexts (NEVER a tree walk), pull the key out of the child whose context
  starts with a configured Cco* prefix. It is parameterized by that prefix, so occupation
  (its numeric settlement_captured cards bind a settlement/faction Cco*) and the HE Intrigue court
  (its option0..N rows bind CcoCampaignFaction) can reuse this same path -- add a `context:True`
  PANELS entry with the right card_container / card_re / cco_prefix.

WHY RECRUITMENT CARDS ALSO CARRY A `source` (the recruit-pool distinction -- critical signal)
  A <unit_key>_recruitable leaf says WHAT is recruitable but not from WHICH pool, yet local vs
  global vs Regiments-of-Renown recruitment is critical training signal (a unit recruitable
  globally at a premium is a different action from the same unit recruitable locally). The
  recruitment panel already GROUPS its cards by source pool under positional group/display
  containers -- the standard pool's `local1`/`global_min`/... group wrappers, plus two sibling
  displays (allied/outpost recruitment, and the mercenary/Regiments-of-Renown display). The
  flat recursion USED to lose that grouping. So recruitment now runs a SOURCE-AWARE enumerator
  (enumerate_sourced_options) that walks each source region and tags every card with a
  normalized `source` derived from the CONTAINER it was found under (see _recruit_source):
  group name PREFIX for the standard pool (generalizable -- future factions' local2/renown/...
  groups map by name, an unknown group keeps its raw name rather than being dropped), and a
  fixed label for the allied ("allied") and mercenary/renown ("renown") displays. Verified live
  (Alith Anar, turn 1): the four local1 cards tag source="local"; the global_min / allied /
  mercenary pools are empty at turn 1 but their containers are recognised and would tag
  global/allied/renown the instant a card appears. Regiments of Renown are IN-PANEL (the
  `mercenary_display` behind button_hire_renown), NOT a separate tab, so they fold in here.

HOW ENUMERATION WORKS (the rebuild)
  The option leaves do NOT sit as direct children of a single node -- they hide several
  POSITIONAL containers deep (recruitment cards are ~5 wrappers below recruitment_options behind
  a pool-group layer; skill leaves live under chain<i>|chain|module_*; tech nodes under
  tech_template|tree_parent|slot_parent). So enumeration is a BOUNDED, key-embedded RECURSION
  from the panel root: descend into non-option children (one safe find per level -- NEVER a whole
  tree walk, which CTDs on thousands of components), collecting key-embedded leaves as options
  and NOT recursing into a leaf's internals. Bounded by max_depth and max_finds for latency.
  Clickable state is read from the correct SUB-NODE per panel (`state_at`): the recruitment card
  itself, a skill leaf's `card`, a tech leaf's `technology_entry`, a building card's
  `square_building_button`, a rite's `action|button_perform`, a list row itself.
  clickable = visible AND state not in the disabled set.

MENU-OPEN TRIGGER
  The game fires a timestamped PanelOpenedCampaign event on every panel open (and
  PanelClosedCampaign on close); the mod logs it to the script_log (verified: as a
  `TWSTATE {"kind":"event","event":"PanelOpenedCampaign",...}` line). The watch loop TAILS the
  newest script_log for that event and, on each occurrence, scans the configured panels for one
  that is now OPEN and populated, enumerates it, and emits -- trigger, not fixed-interval poll.
  NB the event line does NOT itself name which panel opened (all its ctx fields are null on this
  build), so the panel is identified by the bus open-scan, not by parsing the log line.

STREAM  <run>/ui_components.jsonl   (when driven by record.py --ui)
    {"t", "kind":"menu_open", "panel", "n", "options":[{"id","key","clickable","state","visible"
        [,"source"]}]}
    {"t", "kind":"ui_status", "status":"bus_available"|"bus_unavailable"}
  The `source` field is present ONLY on recruitment options (the recruit-pool label:
  "local" / "global" / "renown" / "allied" / "<raw group>" for an unrecognised pool); every
  other panel's options omit it.
"""
from __future__ import annotations

import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))                       # errors (this repo)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bus"))  # bus repo
from bus import Bus                 # noqa: E402
from errors import TWError          # noqa: E402

# CurrentState values (lower-cased) that mean "shown but NOT clickable" (the DENYLIST default:
# clickable = visible AND state not in this set). A panel whose leaf has MANY non-clickable
# states (e.g. construction's square_building_button: greyed / built / cannot_build_ever /
# insufficient_funds / ...) instead sets an ALLOWLIST via cfg["clickable_states"] -- there the
# ONLY clickable state is the one whitelisted (construction: {"normal"}, matching build.py's
# ground-truth `state == "normal"` buildable check) -- so a novel disabled state never
# false-positives as clickable.
_DISABLED_STATES = {"inactive", "locked", "disabled", "greyed", "grayed", "hidden",
                    "unavailable", "dummy"}

# Enumeration bounds. Each find is ONE safe level (never a tree walk); the caps guard latency,
# not safety -- if a bound is hit it is logged so the path can be widened deliberately.
MAX_DEPTH = 10
MAX_FINDS = 600     # raised from 300: big panels (equipment pool, skill trees) exceeded it

# ---- recruitment SOURCE-POOL tagging (Task 3) -----------------------------------------------
# Map a recruit pool-group / display CONTAINER id -> a normalized source label, by NAME PREFIX
# (checked in order; first hit wins), falling back to a raw-container-name substring match. This
# is the GENERALIZABLE join: the standard recruitment pool's group containers are discovered
# LIVE (Alith Anar shows local1 / global_min; other factions may add local2 / global / renown /
# ... groups), so mapping by name means a novel pool still gets a source rather than being
# dropped -- and an entirely unrecognised group keeps its RAW name as the label (never lost).
# mercenaries fold into "renown" per the user directive (renown/mercenary -> renown).
_RECRUIT_SOURCE_PREFIX = (
    ("local",     "local"),      # local1, local2, ...           -> local recruitment
    ("global",    "global"),     # global_min, global, ...       -> global recruitment
    ("renown",    "renown"),     # regiments of renown
    ("mercenary", "renown"),     # mercenary display / hire      -> folded into renown
    ("merc",      "renown"),
    ("allied",    "allied"),     # allied / outpost recruitment
    ("outpost",   "allied"),
)


def _recruit_source(container: str) -> str:
    """Normalized recruit-source label for a pool-group / display container id.

    Args:
        container: The pool-group container id (e.g. "local1", "global_min") or a fixed display
            label already normalized upstream ("allied", "renown").

    Returns:
        A normalized source label ("local" / "global" / "renown" / "allied"), or -- when the
        container name matches no known pool -- the raw container id itself (so a novel future
        pool is still labelled, never dropped); "unknown" for an empty/None container.
    """
    g = (container or "").lower()
    for pre, label in _RECRUIT_SOURCE_PREFIX:
        if g.startswith(pre) or pre in g:
            return label
    return container or "unknown"

# ---- PANELS: the option-bearing panels, keyed by name -------------------------------------
# root       the component to enumerate DOWN FROM (the list container above the option leaves).
# open_path  a cheap component to test whether the panel is up right now.
# open_by    how open_path proves open: "height" (dropdown rect expands; collapsed <=20px),
#            "visible" (found + not invisible), "found" (found at all).
# option_re  matches an option LEAF id (a key-embedded row/card/node); a non-match is a
#            positional container to recurse INTO.
# descend_re (optional) ids to DESCEND INTO even when they also match option_re -- for a
#            panel whose real option leaves sit UNDER a container that itself matches the
#            broad option_re (pre_battle: the button_set_siege/button_set_attack wrappers
#            under button_parent_when_no_countdown_active both match `^button_.+` yet must be
#            descended, not recorded; an INVISIBLE such wrapper -- the non-staged battle set --
#            is skipped so only the live set's buttons are captured).
# positional (optional, True) POSITIONAL MODE: the option cards carry NO semantic key (numeric
#            ids); meaning comes from LEFT->RIGHT x-order + a per-panel `labels` list. Uses
#            enumerate_positional (card_container / state_sub / labels) instead of the
#            key-embedded recursion. This is the FOUNDATION for the HE Intrigue menu (its
#            faction dropdown is option0..N positional) -- add that panel here the same way.
# state_at   the sub-node under a leaf carrying its clickable CurrentState: None = the leaf
#            itself; "card" (skills); "technology_entry" (technology).
# reason_path (optional) a panel-level component whose state is the human-readable reason a
#            whole panel is un-clickable -- skills' unspent-point flag ("no points").
#
# Paths are the exact live container paths proven in twapi/verbs/*.py + data/list_specs.json:
#   recruitment  recruit.py _RECRUIT_OPTIONS (the `base`) + three SOURCE regions hung off it
#                (see the recruitment entry's `sources`): the standard pool
#                (recruitment_listbox, grouped local1/global_min/... -> source per group), the
#                allied/outpost display, and the mercenary/Regiments-of-Renown display. A
#                SOURCE-AWARE enumerator (enumerate_sourced_options) tags each card's `source`.
#   skills       char.py _CD_SKILLS_LIST (+ chain<i>|chain|module_*), _CD_SKILL_PTS
#   technology   research.py _TECH_SLOTS (+ "|<node>|technology_entry")
#   diplomacy    diplo.py _DIPLO_LIST
#   dropdowns    data/list_specs.json (lords_heroes / provinces / known_factions)
PANELS = {
    "lords_heroes": {
        "root": ("hud_campaign|radar_things|dropdown_parent|dropdown_units|panel|panel_clip|"
                 "sortable_list_units|list_clip|list_box"),
        "open_path": "hud_campaign|radar_things|dropdown_parent|dropdown_units",
        "open_by": "height",
        "option_re": r"^character_row_\d+$",
        "state_at": None,
    },
    "provinces": {
        "root": ("hud_campaign|radar_things|dropdown_parent|dropdown_regions|panel|panel_clip|"
                 "listview|list_clip|list_box"),
        "open_path": "hud_campaign|radar_things|dropdown_parent|dropdown_regions",
        "open_by": "height",
        # rows are grouped under player_provinces / player_camps / other_provinces wrappers;
        # the recursion descends those; the hidden player_row/other_row templates never match.
        "option_re": r"^row_entry_.+",
        "state_at": None,
    },
    "known_factions": {
        "root": ("hud_campaign|radar_things|dropdown_parent|dropdown_factions|panel|panel_clip|"
                 "sortable_list_factions|list_clip|list_box"),
        "open_path": "hud_campaign|radar_things|dropdown_parent|dropdown_factions",
        "open_by": "height",
        # subculture HEADER rows are grouping, not options -- their ids carry a _sc_ subculture
        # key; exclude them (negative lookahead) so only real faction rows are recorded.
        "option_re": r"^faction_row_entry_(?!.*_sc_).+",
        "state_at": None,
    },
    # RECRUITMENT is SOURCE-AWARE (Task 3): `base` = recruitment_options; three source regions
    # hang off it and are walked by enumerate_sourced_options, each card tagged with its pool.
    # LIVE-DISCOVERED (Alith Anar / Nagarythe, turn 1):
    #   recruitment_listbox|recruitment_pool_list|list_clip|list_box holds the standard pool's
    #     GROUP containers (local1 -> 4 cards; global_min -> empty at turn 1); each group is
    #     `|<group>|unit_list|listview|list_clip|list_box|<unit_key>_recruitable`. `source:group`
    #     means DERIVE the label per group container name (_recruit_source: local1->local,
    #     global_min->global, ...), so future factions' extra groups still get a source.
    #   allied_recuitment_display -> allied/outpost recruitment (source=allied); its card list is
    #     `recruitment_holder|unit_list|listview|list_clip|allied_unit_list` (empty at turn 1: no
    #     allies yet).
    #   mercenary_display -> mercenaries + REGIMENTS OF RENOWN (source=renown); its card list is
    #     `frame|listview|list_clip|list_box`, revealed by the buttons_holder hire buttons
    #     (button_hire_renown / button_hire_mercenary). RoR is IN-PANEL here, NOT a separate tab
    #     (title_docker|button_holder holds only button_info/button_minimise -- no pool tabs);
    #     empty at turn 1 for Alith Anar (no renown/mercenaries available yet).
    # option_re + state_at are shared by all three regions (cards are the same `_recruitable`
    # key-embedded leaf; clickable state read from the card leaf itself).
    "recruitment": {
        "base": "units_panel|main_units_panel|recruitment_docker|recruitment_options",
        "open_path": "units_panel|main_units_panel|recruitment_docker|recruitment_options",
        "open_by": "visible",
        "option_re": r".+_recruitable$",
        "state_at": None,
        "sources": [
            # standard pool: source DERIVED per group container name (local1/global_min/...)
            {"list": "recruitment_listbox|recruitment_pool_list|list_clip|list_box",
             "source": "group"},
            # allied / outpost recruitment pool
            {"list": ("allied_recuitment_display|recruitment_holder|unit_list|listview|"
                      "list_clip|allied_unit_list"), "source": "allied"},
            # mercenary + Regiments-of-Renown pool (in-panel; NOT a separate tab)
            {"list": "mercenary_display|frame|listview|list_clip|list_box", "source": "renown"},
        ],
    },
    # the ARMY BOX (units_panel) -- CONTEXT-CARD MODE (mod 6ab8bf1). The selected army's unit
    # cards under `units_panel|main_units_panel|units` are POSITIONAL (LandUnit N active /
    # QueuedLandUnit N in-progress recruit / AgentUnit N embedded hero) -- no unit key in any id.
    # enumerate_context_cards does ONE find per card and reads the card_image_holder child's
    # CcoMainUnitRecord context, whose id is the unit key VERBATIM (both active AND queued cards).
    # So the capture is the army's units BY KEY, each flagged recruiting=True (QueuedLandUnit,
    # state=queued) vs active (LandUnit, state=active). This is the ONLY read that sees a queued
    # recruit: the model unit_list EXCLUDES queued recruits (no recruitment-queue accessor), so
    # "which unit is being recruited" is answerable ONLY here. Emits {id, key, state, visible,
    # recruiting, kind, source} per card. GENERALIZABLE: occupation (settlement_captured cards ->
    # their settlement/faction Cco*) and the HE Intrigue court (option0..N -> CcoCampaignFaction)
    # can REUSE this exact path -- add a `context:True` entry with the right card_re / cco_prefix.
    "army": {
        "context": True,                              # CONTEXT-CARD MODE (enumerate_context_cards)
        "card_container": "units_panel|main_units_panel|units",
        "card_re": r"^(?:AgentUnit|LandUnit|QueuedLandUnit) \d+$",
        "cco_prefix": "CcoMainUnitRecord",            # the unit-key context bound on card_image_holder
        "recruit_re": r"^QueuedLandUnit ",            # id prefix that means recruiting (state=queued)
        "open_path": "units_panel|main_units_panel|units",
        "open_by": "found",
    },
    "skills": {
        "root": ("character_details_panel|character_context_parent|tab_panels|skills_subpanel|"
                 "character_details_panel_skills|listview|list_clip|list_box"),
        "open_path": "character_details_panel",
        "open_by": "found",
        "option_re": r"^wh\d?_.*_skill_",
        "state_at": "card",
        "reason_path": ("character_details_panel|character_context_parent|skill_pts_holder|"
                        "skill_pts|dy_pts"),
    },
    # TECHNOLOGY renders ONE tab's tree at a time into slot_parent (37 Cathay / 49 HE-campaign cards --
    # a single find on slot_parent returns them all). The panel has vertical CATEGORY TABS
    # (technology_panel|tabs|CcoTechnologyUiTabRecord<subculture>_<campaign|military|battle>) that REBUILD
    # this one tree on switch; the inactive tab's tree is NOT instantiated (a tab record holds only
    # tx_tab_title / image_tab_category / turns_icon / search_tech_count -- NO tech leaves; list_box holds
    # exactly ONE tech_template), so the root cannot be broadened to reach it. PanelOpenedCampaign fires
    # only on panel OPEN (default tab) -- the OTHER tab (~half the techs: HE wh2_main rows 4-5 + the
    # wh3_dlc27 branch) is captured by the tech-tab RE-TRIGGER in watch() (a CcoTechnologyUiTabRecord*
    # component event re-enumerates this panel), and offline UNIONs all panel=technology snapshots.
    # `active_tab_container` stamps the SELECTED tab's key onto the row (`tab`) so offline can tell which
    # screen each snapshot is (best-effort: the selected tab's state starts with "selected").
    "technology": {
        "root": ("technology_panel|technology_list|list_clip|list_box|tech_template|tree_parent|"
                 "slot_parent"),
        "open_path": "technology_panel",
        "open_by": "found",
        "option_re": r"^wh\d?_.*tech",
        "state_at": "technology_entry",
        "active_tab_container": "technology_panel|tabs",
    },
    "diplomacy": {
        "root": ("diplomacy_dropdown|faction_panel|faction_panel_top|sortable_list_factions|"
                 "list_clip|list_box"),
        "open_path": "diplomacy_dropdown",
        "open_by": "visible",
        "option_re": r"^faction_row_entry_(?!.*_sc_).+",
        "state_at": None,
    },
    # the character-details Details-tab GLOBAL POOL of items available to equip -- the ADD/SWAP-IN
    # option set (char.py _CD_POOL_LB). CHILD-COUNT mode (children_of), NOT the flat recursion: the
    # CcoCampaignAncillary<id> entries are DIRECT children of magic_items_panel / general_ancillaries_
    # panel (the two pool sub-tabs, toggled by magic_items_button / general_ancillaries_button), and
    # the flat recursion returned 0 here -- child-count via the mod `find` returns every entry (the
    # proven diplomacy mechanism). Each entry's ancillary_entry state is active (UNASSIGNED ->
    # equippable/clickable) / inactive (assigned to a character -> NOT clickable; equipping an
    # already-assigned item CTD'd the game, so it must never read clickable -- the denylist marks
    # `inactive` not-clickable, which is exactly the read-only-safe subset). The component id is the
    # numeric ancillary CQI (key = that number); the REAL ancillary key is resolved OFFLINE
    # (item_choices.py) via the entry's GetStateText (item name -> loc reverse-map) and/or by
    # correlating a pool-diff with the concurrent CharacterAncillaryGained event. EMPTY until the pool
    # fills (battles/quests/Ivory Road) -> honestly records n=0. state_at reads the clickable state
    # from the ancillary_entry sub-node (proven), falling back to the node's own state.
    "equipment": {
        "children_of": True,
        "roots": [
            {"path": ("character_details_panel|character_context_parent|tab_panels|"
                      "character_details_subpanel|character_details_panel_details|equipment_holder|"
                      "global_pool|global_ancillaries_listview|list_clip|list_box|magic_items_panel"),
             "source": "magic_items"},
            {"path": ("character_details_panel|character_context_parent|tab_panels|"
                      "character_details_subpanel|character_details_panel_details|equipment_holder|"
                      "global_pool|global_ancillaries_listview|list_clip|list_box|"
                      "general_ancillaries_panel"),
             "source": "ancillaries"},
        ],
        "option_re": r"^CcoCampaignAncillary\d+$",   # skip template_item_holder / non-entry children
        "key_prefixes": ["CcoCampaignAncillary"],     # key = the numeric ancillary CQI (resolved offline)
        "state_at": "ancillary_entry",
        # D9 readable NAME at capture: the magic-item POOL cards are ICON-ONLY -- the card's own
        # GetStateText, the ancillary_entry GetStateText, AND the card TOOLTIP were all EMPTY (live-grabbed
        # on the open item screen). The item NAME is rendered on a dedicated `dy_name` CHILD text node
        # (context-bound to the card's CcoCampaignAncillary), read verbatim via GetStateText -> `onscreen`
        # (live: dy_name for CQI 6 -> "Stone of Midnight", 282 -> "Khaine's Ring of Fury", 283 ->
        # "Engineer's Knapsack", 54 -> "Longshot's Cap"). This is THE deterministic single source -- the
        # SAME pattern occupation uses for dy_option, NOT the tooltip and NOT an offline CQI->key->loc
        # fallback (the name is right there at capture time). `dy_label` is the item CATEGORY
        # (Talisman/Enchanted Item/Armour/Weapon), captured as `category`.
        "name_node": "dy_name",
        "label_node": "dy_label",
        "open_path": "character_details_panel",
        "open_by": "found",
    },
    # the character-details EQUIPPED items -- the REMOVE/SWAP-OUT option set. CONTEXT-CARD mode: the
    # equiped_items_list holds one CcoAncillariesCategoryRecord<n> per equipment slot; the slot's OWN
    # context is the CHARACTER (CcoCampaignCharacter:<cqi>) and the equipped item is bound as
    # CcoCampaignAncillary on the slot (child context / own context), whose id is the numeric ancillary
    # CQI (real key resolved OFFLINE like the pool). `container_context` stamps the panel character
    # (from the list container's own context) onto every slot so the OFFLINE remove-detection can DIFF
    # the equipped set PER CHARACTER across snapshots (a filled->empty slot, or a key that leaves the
    # set, is a REMOVE -- which fires NO event anywhere, so the scrape diff is the only scrape-channel
    # signal). An empty slot yields key=None (skipped by the diff). EVENT-DRIVEN (re-wired, FIX #3): it
    # rides character_details_panel's PanelOpenedCampaign alongside skills+equipment (COMPONENT_PANELS),
    # capturing the equipped set AS OF each panel open; the offline remove/swap diff compares consecutive
    # opens PER character (a tab/equip change WITHIN a single open does not re-fire the event, but the
    # cross-open snapshot is exactly what the diff needs). Context-card mode is already DE-POSITIONALIZED
    # (each slot keyed by its bound CcoCampaignAncillary / CcoCampaignCharacter context, never x-order),
    # so no positional treatment is needed. ‼ live-verify the Details tab is populated at panel open (else
    # the equipped list reads empty until the tab is shown) + the equipped-slot count == the real armory.
    "equipment_equipped": {
        "context": True,
        "card_container": ("character_details_panel|character_context_parent|tab_panels|"
                           "character_details_subpanel|character_details_panel_details|"
                           "equipment_holder|ancillary_parent|magic_items_equiped|"
                           "equiped_items_listview|list_clip|equiped_items_list"),
        "card_re": r"^CcoAncillariesCategoryRecord\d+$",
        "cco_prefix": "CcoCampaignAncillary",          # the equipped item's numeric id (child/own ctx)
        "container_context": "CcoCampaignCharacter",   # the panel character -> per-char diff key
        # D9 readable NAME at capture: the equipped item name is on the slot's `dy_name` CHILD text node
        # (GetStateText, context-bound to the equipped item's CcoCampaignAncillary -- live-grabbed on the
        # open item screen, tooltips EMPTY), read verbatim into `onscreen` so a remove/swap row shows the
        # item name, not the numeric CQI. THE deterministic single source (the dy_option pattern), not the
        # tooltip and not an offline cqi->key->loc fallback. `dy_label` = the item CATEGORY, into `category`.
        "name_node": "dy_name",
        "label_node": "dy_label",
        "open_path": "character_details_panel",
        "open_by": "found",
    },
    # the settlement BUILD BROWSER (build.py TREE): open a settlement slot -> the persistent
    # `building_construction_popup` toggles visible with the construction_building_tree. Each
    # CcoBuildingChainRecord* chain holds `building_chain|slot_parent` positional wrappers whose
    # option leaves are the KEY-EMBEDDED building cards (wh*_...). Their clickable state lives on
    # `square_building_button`: state=normal is buildable (clickable), greyed/built/locked are not.
    # Verified live (Alith Anar, Nagarythe home region, turn 1): resource->marble chain read
    # wh2_main_hef_resource_marble_1=normal (buildable), _2/_3=greyed (locked); the occupied
    # Aesanar-Camp slot read wh2_dlc10_hef_aesanar_camp=built; the military-recruitment category
    # read wh2_main_hef_hunters_1=normal, _2=greyed, wh2_dlc10_hef_hunters_3=cannot_build_ever.
    # `clickable_states={"normal"}` is an ALLOWLIST (build.py's ground truth): normal is the ONLY
    # buildable state, so greyed/built/cannot_build_ever/insufficient_funds all read not-clickable
    # without enumerating every disabled string. The hidden template_chain_parent holds only a
    # template_slot_entry (no wh*_ card) so it yields no phantom option.
    "construction": {
        "root": ("building_construction_popup|popup_panel|building_tree_holder|"
                 "construction_building_tree|chain_list"),
        "open_path": "building_construction_popup",
        "open_by": "visible",
        "option_re": r"^wh\d?_",
        "state_at": "square_building_button",
        "clickable_states": {"normal"},
    },
    # the HIGH ELVES RITES panel (HUD button_rituals -> root `rituals_panel`): the mod already
    # logs RitualStarted/CompletedEvent, so rites are live for Alith Anar. context_rituals_list
    # holds the KEY-EMBEDDED rite leaves CcoCampaignRitual<n><ritual_key> (+ a hidden
    # template_ritual, excluded by the matcher). Each rite's performable state lives on
    # `action|button_perform`: inactive = cannot-perform (locked/unaffordable), active/normal =
    # performable. Verified live (turn 1): all four HE rites read button_perform=inactive --
    # CcoCampaignRitual30wh2_dlc10_ritual_hef_morai_heg, ...wh2_main_ritual_hef_isha,
    # ...wh2_main_ritual_hef_asuryan, ...wh2_main_ritual_hef_vaul (all unlock later in the game).
    "rites": {
        "root": "rituals_panel|panel_frame|context_rituals_list",
        "open_path": "rituals_panel",
        "open_by": "found",
        "option_re": r"^CcoCampaignRitual",
        "state_at": "action|button_perform",
    },
    # SLAANESH "Great Game" rituals -- a DIFFERENT tree than the HE rites above (captured live from
    # the Masque of Slaanesh demo, [ui] trace): great_game_rituals (top-level root) > rituals_panel >
    # rituals_list > CcoCampaignRitual<cqi><key> (e.g. CcoCampaignRitual127wh3_main_ritual_sla_gg_1).
    # Same key-embedded leaf so _extract_key yields the wh*_ritual key; reuses enumerate_options +
    # option_re. Mapped to the same "rites" decision type in the advisor (PANEL_TYPE). state_at=None
    # reads each rite leaf's own state (active=performable) since the great-game leaf carries no
    # button_perform sub-node like the HE variant.
    "rites_great_game": {
        "root": "great_game_rituals|rituals_panel|rituals_list",
        "open_path": "great_game_rituals",
        "open_by": "found",
        "option_re": r"^CcoCampaignRitual",
        "state_at": None,
    },
    # ---- BATTLE-CHAIN choice panels (live-discovered this session, Alith Anar) ----------
    # PRE-BATTLE choice panel (KEY-EMBEDDED). Root = button_parent_when_no_countdown_active
    # (the same docker army.py drives); it holds the two mutually-exclusive button SETS
    # button_set_siege (settlement assault) / button_set_attack (field battle) -- only one
    # visible -- plus button_set_mp. The REAL choice buttons live UNDER the visible set:
    # button_autoresolve / button_attack_container / button_surround_container /
    # button_retreat / button_demand_surrender -- AND the *_container wrappers (button_attack,
    # button_surround, button_continue_siege). MAJOR-siege finding (live, Alith Anar): the assault/
    # surround/continue-siege options are NOT leaves -- each is a `button_*_container` wrapper
    # (state=NewState, meaningless) around an inner `button_*` that carries the REAL active/inactive
    # state. So `descend_re` matches BOTH the `button_set_` sets AND any `_container` wrapper, and
    # the recursion records the inner button leaf with its true state. (button_continue_siege_container
    # is HIDDEN on a minor siege but VISIBLE on a major one -- the "maintain siege across turns to
    # breach" option -- and now reads as active.) `skip_invisible_descend_re` limits the invisible-
    # skip to the `button_set_` twin (whose ghost buttons share ids and would MASK the live set via
    # de-dup); an invisible `_container` is still descended so its unavailable option is recorded
    # visible:False (unique ids -> no masking), matching how hidden direct buttons are recorded.
    "pre_battle": {
        "root": ("popup_pre_battle|mid|battle_deployment|pre_battle_deployment_panel|"
                 "regular_deployment|button_docker|button_parent_when_no_countdown_active"),
        "open_path": "popup_pre_battle",
        "open_by": "visible",
        "option_re": r"^button_.+",
        "descend_re": r"^button_set_|_container$",
        "skip_invisible_descend_re": r"^button_set_",
        "state_at": None,
        # D10 readable NAME at capture: these choice buttons (button_attack, button_autoresolve,
        # button_retreat, button_continue_siege, button_demand_surrender, ...) carry NO GetStateText on
        # the button node itself (verified EMPTY in run 20260728_224846). The on-screen label lives in the
        # button's TOOLTIP, which describe() ALREADY returns as res["tooltip"] (GetTooltipText, fetched
        # WITH the state -- no extra bus call). WH3 formats it "Title||Description" (live-grabbed:
        # button_attack -> "Fight Battle||Manually fight the siege battle at the city walls.";
        # button_retreat -> "Break Siege||..."), so `onscreen` = the title before "||" (_tooltip_title).
        # Deterministic single source -- no text/text_label/name_sub cascade.
        "onscreen": True,
    },
    # POST-BATTLE CAPTIVES choice panel (KEY-EMBEDDED). Root = the button_set_win set (full
    # absolute path -- battle.py's proven _RES_WIN_SET) which is ONLY visible on a FIELD WIN
    # with captives>0; it holds the id-embedded options button_captive_option_kill /
    # _enslave / _release (state on the button). On any other result this enumerates empty.
    "post_battle_captives": {
        "root": ("popup_battle_results|mid|battle_results|post_battle_results_panel|"
                 "button_set_win_holder|button_set_win"),
        "open_path": "popup_battle_results",
        "open_by": "visible",
        "option_re": r"^button_captive_option_.+",
        "state_at": None,
        # NO capture-time NAME: these captive buttons carry EMPTY GetStateText AND an EMPTY tooltip
        # (live-grabbed). The on-screen name ("Execute Captives"/...) lives in a SEPARATE hover-only
        # tooltip root `tooltip_captive_options|txt_title` that is populated ONLY while a human hovers --
        # useless for passive capture. So this panel records just the 3 outcome buttons
        # (button_captive_option_kill/_enslave/_release) + their state; the READABLE name is resolved
        # OFFLINE from the DB (captive record_key -> onscreen_name), a separate reference-ingestion fix
        # (NOT the recorder). Deliberately NOT an `onscreen`/tooltip panel like pre_battle.
    },
    # SETTLEMENT OCCUPATION choice panel. The settlement_captured panel's cards under button_parent
    # are NUMERIC-id (no semantic key in the id), BUT their identity is NOT their x-order: the option
    # SET and COUNT are faction- AND context-specific (2/4/5 cards; Occupy/Loot&Occupy/Sack/Subjugate/
    # Raze/Raise-Herdstone/... -- 4-faction proof in docs), so any positional map MISLABELS (a 2-card
    # offer used to fall back to meaningless option0/option1). DE-POSITIONALIZED: the option's real,
    # faction-correct identity is the ON-SCREEN LABEL -- GetStateText on the card's `dy_option` node
    # ("Occupy"/"Sack"/"Subjugate"/"Raise Herdstone"...) -- read via `label_sub`. The stable KEY is the
    # card id itself: it EQUALS the CharacterPerformsSettlementOccupationDecision event's
    # `occupation_decision`, and joins offline to loc `culture_settlement_occupation_options_tooltip_
    # <id>` (name before "||") -- so the advisor can name it even from an old capture with no text.
    # No _OCC_ORDER / x-order anywhere. The option DESCRIPTION rides along free on the option_button
    # tooltip (state_sub find). (`value_subs` is a generic hook for per-card values, unused here.)
    "occupation": {
        "positional": True,
        "card_container": "settlement_captured|button_parent",
        "state_sub": "option_button",
        # VERIFIED offline (run 20260725_222245): the on-screen NAME is GetStateText on
        # <card>|option_button|dy_option -> "Occupy"/"Loot & Occupy"/"Sack"/"Raze"/... . The
        # option_button's OWN tooltip carries the description ("You occupy this settlement.") at
        # capture time and is picked up free from the state_sub find (no extra bus round-trip).
        "label_sub": "option_button|dy_option",       # GetStateText here = the real on-screen name
        "open_path": "settlement_captured",
        "open_by": "visible",
    },
    # ---- COLLAPSIBLE HUD STACKS (CHILD-COUNT mode -- enumerate_children) ---------------------
    # ARMY STANCES + province EDICTS are HUD "incentive/stance" stacks under hud_campaign|BL_parent.
    # They are the reason the visible-gated scrape missed them (STANCE_EDICT_CAPTURE.md): each stack
    # is COLLAPSIBLE, so when collapsed only the current option is Visible()=True and the REST report
    # Visible()=False EVEN WHILE state=active (VERIFIED live: 8/13 available land stances read
    # visible=False on a collapsed stack). AND these stacks fire NO PanelOpenedCampaign. So (a) they
    # are enumerated by CHILD-COUNT via the bus `find` handler (ChildCount+Find(i) is NOT
    # visibility-gated -> returns every button while collapsed), and (b) clickable is decided from
    # STATE ALONE (_clickable_by_state), never gated on the unreliable collapsed Visible flag.
    # Capture is POLL-DRIVEN (see POLL_PANELS / the watch-loop poll), not PanelOpenedCampaign.
    #
    # ARMY STANCES: two sibling stacks (land + naval) under BL_parent; each `…|clip_parent|
    # stack_background` holds button_MILITARY_FORCE_ACTIVE_STANCE_TYPE_<KEY> option buttons +
    # button_default. Stance key = id with `button_MILITARY_FORCE_ACTIVE_STANCE_TYPE_` stripped
    # (LAND_RAID, SETTLE, MARCH, AMBUSH, TUNNELING, …; naval: DOUBLE_TIME, SEA_RAID, PATROL, …);
    # button_default -> "default" via the generic button_ fallback. Each root's options carry a
    # `source` (land / naval) so the shared button_default is not de-duplicated across the two.
    # State = the button's own state (active=available, inactive=not; VERIFIED live: TUNNELING
    # inactive, the rest active for Alith Anar). open_by:"found" (NOT visible -- the collapsed
    # stack_background is itself visible=False) gates capture to "an army/settlement is selected".
    "army_stances": {
        "children_of": True,
        "roots": [
            {"path": "hud_campaign|BL_parent|land_stance_button_stack|clip_parent|stack_background",
             "source": "land"},
            {"path": "hud_campaign|BL_parent|naval_stance_button_stack|clip_parent|stack_background",
             "source": "naval"},
        ],
        "key_prefixes": ["button_MILITARY_FORCE_ACTIVE_STANCE_TYPE_", "button_"],
        "open_path": "hud_campaign|BL_parent|land_stance_button_stack|clip_parent|stack_background",
        "open_by": "found",
    },
    # EDICTS (province commandments): one stack `stack_incentives|clip_parent|stack_background`
    # holding button_<edict_key> option buttons + button_default. Edict key = id minus leading
    # `button_` (e.g. wh2_main_edict_hef_reaver_patrols, …_tribute_for_the_king, …_rally_citizen_
    # militia; button_default -> "default"). State = button state (active/hover = selectable). The
    # CHOSEN edict is already captured elsewhere (region state dump's active_edict/selected_edict);
    # this records the AVAILABLE option set. open_by:"found" (collapsed stack is visible=False).
    "edicts": {
        "children_of": True,
        "roots": [
            {"path": "hud_campaign|BL_parent|stack_incentives|clip_parent|stack_background",
             "source": None},
        ],
        "key_prefixes": ["button_"],
        "open_path": "hud_campaign|BL_parent|stack_incentives|clip_parent|stack_background",
        "open_by": "found",
    },
    # ---- DIPLOMACY NEGOTIATION offers (CHILD-COUNT mode -- key-embedded `diplomatic_option_`) -----
    # A live negotiation stages its clauses in three offer lists under the diplomacy offers panel;
    # each clause is a KEY-EMBEDDED `diplomatic_option_<clause_key>` row (option_re filters the list
    # container's non-option children out). Reuses enumerate_children (child-count via find) so the
    # clause set is read regardless of virtualization; POLL-DRIVEN (open_by:"visible", in POLL_PANELS)
    # because the offers panel toggles with the negotiation, not with PanelOpenedCampaign. Clause key
    # = id minus `diplomatic_option_`; state active=available / inactive=greyed. VERIFIED live: the
    # three root paths + the deal-summary leaves resolve while a negotiation is open (the option lists
    # are empty until the human stages clauses -- they populate then).
    #
    # diplomacy_options = the AVAILABLE actions list (the expanded offer category's clauses). It also
    # carries a `deal` SUMMARY (targeted single-find scalar reads, not a list): deal success chance,
    # send-button state, current treasury, and the selected COUNTERPARTY faction row. The `deal` is
    # captured whenever the negotiation panel is open (scan_open keeps a `deal` row even at n=0).
    "diplomacy_options": {
        "children_of": True,
        "roots": [
            {"path": ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "offers_list_panel|list_possible_actions"), "source": None},
        ],
        "option_re": r"^diplomatic_option_",
        "key_prefixes": ["diplomatic_option_"],
        "open_path": ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "offers_list_panel|list_possible_actions"),
        "open_by": "visible",
        "scalars": [
            {"name": "deal_success_chance",
             "path": ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "offers_list_panel|button_set_holder|deal_success_holder|"
                      "label_deal_success_chance"), "read": "text"},
            {"name": "send_state",
             "path": ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "offers_list_panel|button_set_holder|button_set1|button_send"),
             "read": "state"},
            {"name": "treasury",
             "path": ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "dy_treasury"), "read": "text"},
        ],
        "selected_row": {
            "name": "counterparty",
            "root": ("diplomacy_dropdown|faction_panel|faction_panel_top|sortable_list_factions|"
                     "list_clip|list_box"),
            "prefix": "faction_row_entry_",
            "selected_state_prefix": "selected",
        },
    },
    # your STAGED GIFTS (what the player offers): the your_offers list holds `diplomatic_option_`
    # clause rows once the human stages them (empty otherwise -> n=0, not emitted).
    "diplomacy_your_offers": {
        "children_of": True,
        "roots": [
            {"path": ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "offers_list_panel|center_panel|your_offers|your_offers_list|list_clip|"
                      "list_box"), "source": None},
        ],
        "option_re": r"^diplomatic_option_",
        "key_prefixes": ["diplomatic_option_"],
        "open_path": ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "offers_list_panel|center_panel|your_offers|your_offers_list|list_clip|"
                      "list_box"),
        "open_by": "visible",
    },
    # their STAGED DEMANDS (what the counterparty offers/demands -- confederation, vassalage, land):
    # the their_offers list, same `diplomatic_option_` clause rows.
    "diplomacy_their_offers": {
        "children_of": True,
        "roots": [
            {"path": ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "offers_list_panel|center_panel|their_offers|their_offers_list|list_clip|"
                      "list_box"), "source": None},
        ],
        "option_re": r"^diplomatic_option_",
        "key_prefixes": ["diplomatic_option_"],
        "open_path": ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "offers_list_panel|center_panel|their_offers|their_offers_list|list_clip|"
                      "list_box"),
        "open_by": "visible",
    },
    # ---- OFFICES (faction office assignment -- P1) -------------------------------------------
    # CONTEXT-CARD MODE. The offices mechanic (e.g. Wood Elves' Court of the Wild Hunt) assigns a
    # character to one of several office SLOTS. `offices` is a TOP-LEVEL root; the click's path-from-
    # root log gave the anchor `offices|main|offices_panel`.
    # FIXED FROM CAPTURED DATA (run 20260728_181345, two `panel:offices` menu_opens, n=3): the old
    # card_container `offices|main|offices_panel` was ONE LEVEL TOO HIGH -- its only DIRECT children are
    # three LAYOUT containers (title_holder / portrait_holder / kick_holder), NOT office slots, so every
    # row resolved key=None (no CcoCampaignCharacter under any of them) and only 3 "slots" showed for a
    # 7-office faction. The office SLOTS live one level deeper, under `portrait_holder` (the wide
    # portrait area: x=412 w=1160 h=399 -- room for ~7 portraits across; title_holder is the header,
    # kick_holder the 55x55 "remove from office" button). So card_container now descends into
    # portrait_holder; each slot's OCCUPANT still sits at `portrait_hold|portrait_card`, bound to
    # CcoCampaignCharacter (numeric cqi) -- read via `context_sub`. An empty office binds no character
    # -> key=None. Capturing that bound Cco (cqi) IS resolving the occupant: the cqi joins offline to
    # the character's name (same as recruit_panel's character_details_<cqi>), no name-node round-trip
    # needed. There is NO office semantic event / state field, so this scrape IS the option-set + the
    # chosen signal: chosen (character->office) = a slot going empty->occupied across snapshots (advisor
    # cross-references the assignment click, and a mod char->office getter if one is added later).
    # `id` = the slot id (the office); `key` = the occupant Cco cqi (or None).
    # ‼ live-verify (E2E, fresh WE recording -- captured data only reached offices_panel's 3 direct
    #   children, nothing BELOW portrait_holder is confirmed):
    #   * confirm the 7 office slots are DIRECT children of portrait_holder. If a listview/scroll
    #     wrapper intervenes (portrait_holder|<list>|<slot>), append that segment to card_container;
    #     until then a wrong depth just yields the wrapper as a keyless card -> graceful, no crash.
    #   * confirm each slot's occupant Cco sits at `portrait_hold|portrait_card` (context_sub). If the
    #     occupant NAME is wanted as a STRING (not just the cqi), grab the slot's `dy_*` name node path
    #     (GetStateText) and wire it -- NOT guessed here (no captured evidence of that sub-path).
    #   * the "Lords" candidate-pool container: if the pool cards are a SIBLING of portrait_holder (or
    #     nested under a "Lords" row) rather than more office slots, add a second context entry for it.
    # Permissive card_re: every non-template child of portrait_holder is treated as an office slot.
    # ‼ FIX #2 (DO NOT REGRESS): card_container is DELIBERATELY at ...|offices_panel|portrait_holder --
    #   the prior depth ...|offices_panel was ONE LEVEL TOO HIGH (its 3 direct children are layout
    #   containers, giving 3 fake slots with occupant=None for a 7-office faction). The 7 office SLOTS
    #   + each occupant's CcoCampaignCharacter (resolved via context_sub portrait_hold|portrait_card)
    #   MUST be confirmed at the live WE E2E; this run only reached offices_panel's 3 direct children.
    "offices": {
        "context": True,
        "card_container": "offices|main|offices_panel|portrait_holder",
        "card_re": r"^(?!template).+",                     # every non-template child = an office slot
        "cco_prefix": "CcoCampaignCharacter",              # occupant / candidate char (numeric cqi)
        "context_sub": "portrait_hold|portrait_card",      # occupant bound 2 levels below the slot
        "open_path": "offices",
        "open_by": "found",
    },
    # ---- RECRUIT LORD / HERO pool (P1) -------------------------------------------------------
    # CONTEXT-CARD MODE. ONE panel serves BOTH recruit-lord and recruit-hero; the MODE is read from the
    # header title `tx_recruit` ("Recruit Lord" / "Recruit Hero") and attached to the row as `title` so
    # the advisor splits lord vs hero. The candidate POOL cards (general_candidate_0.._N) EACH bind
    # context CcoCampaignCharacter:character_details_<cqi> (their sub-node text is virtualized -- the
    # context object is the identity, NOT card child text, per FINDINGS -> enumerate_context_cards reads
    # the card's own context). The type-TAB ids (chs_lord / chs_sorcerer_lord /
    # wh3_main_sla_herald_of_slaanesh) name the available class and are attached as `tabs`. chosen =
    # CharacterRecruited/CharacterCreated char_subtype (already captured). Belongs to the province/army
    # screen-group. Root `character_panel` fires PanelOpenedCampaign (component "character_panel"),
    # DISTINCT from the char-DETAILS panel (skills/equipment).
    # live-verify: the intermediate segment(s) between general_selection_panel and lords_and_agents_holder
    # in `tab_container` are elided in FINDINGS (best-guess wired); the pool `card_container` path IS the
    # confirmed full live structure.
    "recruit_panel": {
        "context": True,
        "card_container": ("character_panel|character_panel_info_holder|general_selection_panel|"
                           "main_holder|character_list_parent|character_list|listview|list_clip|"
                           "list_box"),
        "card_re": r"^general_candidate_\d+",              # general_candidate_0_ ... general_candidate_9_
        "cco_prefix": "CcoCampaignCharacter",              # each card = CcoCampaignCharacter:character_details_<cqi>
        "title_path": "character_panel|character_panel_info_holder|header|title_plaque|tx_recruit",
        "tab_container": ("character_panel|character_panel_info_holder|general_selection_panel|"
                          "lords_and_agents_holder|lord_parent|list_box"),
        "open_path": "character_panel",
        "open_by": "found",
    },
    # ETERNAL DANCE (Masque of Slaanesh) is deliberately NOT wired as a recorder panel: per FINDINGS it
    # is chosen via `btn_dance_<theme>` -> `button_perform` CLICKS (click-driven, resolved advisor-side
    # from the click stream) and has no reliable panel-open event. If a future dance subpanel is found
    # to fire a clean PanelOpenedCampaign, add a COMPONENT_PANELS + PANELS entry then.
    # ---- HIGH ELVES "INTRIGUE AT THE COURT" (best-effort -- structure UNCONFIRMED) -----------
    # CONTEXT-CARD MODE (best-effort). The HE Intrigue panel (HUD button_intrigue -> root
    # `intrigue_panel`) fires PanelOpenedCampaign but was previously UNMAPPED. Its faction selectors
    # are POSITIONAL dropdown rows (option0..optionN -- NO game key in the id, only a row_tx text
    # child), so the KEY-EMBEDDED rule skipped it; but per the top-of-file CONTEXT-OBJECT note each
    # such row still BINDS a CcoCampaignFaction the game reads to draw it, so enumerate_context_cards
    # can name the faction from that bound context (cco_prefix=CcoCampaignFaction) -- exactly the
    # reuse the module docstring anticipated for this panel. The panel's ACTIONS
    # (button_improve/button_decrease) are fixed-id and are a separate CLICK-stream signal, not
    # enumerated here. `id` = the positional row id; `key` = the bound faction key (or None).
    # ‼ live-verify (E2E, fresh HE recording -- NO captured intrigue structure exists yet; this
    #   entry is a best-effort scaffold that fails GRACEFULLY -- wrong path => 0 cards => no capture,
    #   never a crash):
    #   * card_container below is a BEST GUESS at the ROOT only. The option rows sit some unknown
    #     depth BELOW intrigue_panel. Grab the mod find channel's path-from-root to an `option0` row
    #     and set card_container to the full path ABOVE `option<N>` (the row-list container, likely a
    #     `...|list_clip|list_box` dropdown list like diplomacy's faction list). Until then this
    #     captures nothing.
    #   * confirm the rows bind CcoCampaignFaction (cco_prefix). If the faction id instead sits on a
    #     sub-node of the row, add `context_sub`. `row_tx` GetStateText is the display-name fallback
    #     if no context is bound.
    #   * card_re `^option\d+$` matches the positional rows; tighten if other `option*` widgets collide.
    "intrigue": {
        "context": True,
        "card_container": "intrigue_panel",                # BEST GUESS root -- real row-list path UNCONFIRMED (live-verify)
        "card_re": r"^option\d+$",                         # positional faction rows option0..optionN
        "cco_prefix": "CcoCampaignFaction",                # each row binds the counterparty faction
        "open_path": "intrigue_panel",
        "open_by": "found",
    },
    # NOTE (audited live on Alith Anar, not wired): the `sword_of_khaine` HUD button is a camera-jump
    # (no panel) and `button_missions`->`objectives_screen` is an informational objectives/victory
    # list (empty at turn 1), neither an option-choice menu.
}
# compile each panel's option matcher + optional descend matcher once (positional panels
# carry no option_re -- they enumerate by x-order, not a key-embedded recursion).
for _cfg in PANELS.values():
    if "option_re" in _cfg:
        _cfg["_opt"] = re.compile(_cfg["option_re"])
    if "descend_re" in _cfg:
        _cfg["_descend"] = re.compile(_cfg["descend_re"])
    if "skip_invisible_descend_re" in _cfg:
        _cfg["_skip_invis"] = re.compile(_cfg["skip_invisible_descend_re"])
    if "card_re" in _cfg:                             # context-card panels (army, future occ/intrigue)
        _cfg["_card"] = re.compile(_cfg["card_re"])
    if "recruit_re" in _cfg:
        _cfg["_recruit"] = re.compile(_cfg["recruit_re"])

WATCH_POLL = 0.35             # seconds between script-log tail reads (event trigger)
BACKOFF_INTERVAL = 10.0       # slower poll while the bus is unavailable (game down / not armed)
REEMIT_COOLDOWN = 3.0         # don't re-enumerate the same still-open panel within this window

# Panels NOT tied to PanelOpenedCampaign -- driven by a low-rate VISIBILITY POLL in the watch loop
# instead of the event scan (STANCE_EDICT_CAPTURE.md): the collapsible HUD stacks (army_stances /
# edicts) fire no panel-open event; the construction sub-popup (building_construction_popup) is
# opened by clicking a building slot, which likewise fires no PanelOpenedCampaign -> the event scan
# never coincides with it (builds happen but 0 construction captures); and the DIPLOMACY negotiation
# offer lists (diplomacy_options / _your_offers / _their_offers) toggle with the negotiation, not a
# panel-open event. The poll cheaply checks just these few roots' open/populated-ness every
# POLL_INTERVAL and captures on a rising edge, so bus load stays low (never a full-panel scan per
# tick). These names are EXCLUDED from the event scan.
# BLIND POLL ELIMINATED (POLL_PANELS = ()). The recorder is now PURELY EVENT-DRIVEN: an option-set is
# captured only when its panel's PanelOpenedCampaign fires (see COMPONENT_PANELS). The ex-poll decisions
# are now handled thus (POLL ELIMINATION synthesis, FINDINGS):
#   rites / rites_great_game -> EVENT-DRIVEN: both fire PanelOpenedCampaign (rituals_panel /
#                               great_game_rituals) -> moved to COMPONENT_PANELS; chosen already from
#                               RitualStartedEvent.ritual_rkey.
#   equipment (item pool)    -> EVENT-DRIVEN: captured on character_details_panel open (COMPONENT_PANELS);
#                               chosen add = CharacterAncillaryGained, remove = offline UI-diff.
#   edicts                   -> advisor-synthesized: chosen = region state active_edict; option-set = DB
#                               provincial_initiative_records by subculture.
#   army_stances             -> advisor-synthesized: chosen = force state `stance`; option-set = the known
#                               MILITARY_FORCE_ACTIVE_STANCE_TYPE_* constant.
#   construction             -> advisor-synthesized: chosen = build events (BuildingConstructionIssuedBy
#                               Player / BuildingCompleted / slot-diff) + the slot-click; option-set from
#                               DB or a one-shot click-triggered enumeration.
#   diplomacy (offers/L2)    -> EVENT-DRIVEN (re-wired, FIX #4a): diplomacy_options/_your_offers/
#                               _their_offers ride the diplomacy_dropdown open (COMPONENT_PANELS) -> the
#                               staged-deal option-set + deal summary capture while the negotiation panel
#                               is open. chosen still = diplomacy events + clicks + diplo-state diff.
#   equipment_equipped       -> EVENT-DRIVEN (re-wired, FIX #3): rides character_details_panel open with
#                               skills+equipment (COMPONENT_PANELS) -> supplies the equipped snapshot the
#                               offline REMOVE/SWAP UI-diff needs (a remove fires no event). Was DROPPED
#                               when POLL was removed; now capture-on-open (poll path still intact/reversible).
# The poll CODE PATH is intact: empty POLL_PANELS -> poll_names == [] -> the poll block is a no-op, so
# re-populating this tuple restores polling with no other change (reversible).
# CONSTRUCTION re-enabled (2026-07-29): the settlement build browser (`building_construction_popup`) is a
# persistent toggling panel that fires NO PanelOpenedCampaign, so it cannot ride COMPONENT_PANELS -- it was
# ONLY ever captured by the poll (verified: run 20260727_144203 = 9 construction rows via poll; fresh
# event-only runs = 0). Re-added here so BUILD option-sets are captured. Poll cost is bounded: one cheap
# open-check per POLL_INTERVAL with exponential backoff when closed (a MISS backs off to 30s), so a single
# poll target does NOT recreate the old multi-panel poll storm (that was fixed by _FIND_TIMEOUT 2.5->6.0 +
# dropping every OTHER poll panel; the storm needed many faction-absent panels each timing out per sweep).
# army_stances re-added 2026-07-29: the stance stack fires NO PanelOpenedCampaign and exists only
# while an army is SELECTED (rising-edge poll capture). Its capture is the STANCE LEGALITY
# WHITELIST source -- the cco StanceList includes faction-illegal stances and the Activate command
# sets them anyway (verified rule breach), so the loop refuses stance execution without this.
POLL_PANELS = ("construction", "army_stances")

# EVENT-DRIVEN CAPTURE MAP: PanelOpenedCampaign NAMES the opened panel via its `component` field, so we
# capture the mapped panel(s) DIRECTLY instead of scanning every open_path (which does a full-tree search
# + a 2.5s timeout for each absent faction-specific panel -- the old ~0-capture race, 2 menu_opens/run).
# Only EVENT panels belong here; POLL_PANELS (HUD stacks / construction sub-popup / diplomacy offer lists
# / equipment) are captured by the poll and must NOT be listed. An unmapped component is logged once.
COMPONENT_PANELS = {
    "settlement_captured": ("occupation",),
    "popup_battle_results": ("post_battle_captives",),
    "popup_pre_battle": ("pre_battle",),
    "technology_panel": ("technology",),
    "recruitment_options": ("recruitment",),
    "units_recruitment": ("recruitment",),
    # character_details_panel open now captures the skills tree, the equipment item POOL (add/swap-IN),
    # AND the EQUIPPED items (remove/swap-OUT) -- all three ride the panel's PanelOpenedCampaign (they
    # fired no reliable poll-open). equipment_equipped (FIX #3) feeds the advisor's item remove/swap,
    # which is an offline UI-DIFF of the equipped snapshot PER character across opens (a REMOVE fires no
    # event anywhere, so this scrape is the only signal).
    # ‼ live-verify: character_details_panel's PanelOpenedCampaign fires on panel OPEN (not on an in-panel
    #   TAB switch nor on an equip/remove), so equipment/equipment_equipped snapshot the pool + equipped
    #   set AS OF that open; the remove-diff compares consecutive opens per char (sufficient for a
    #   cross-open remove). Confirm the Details tab is populated at open (else the equipped list is empty).
    "character_details_panel": ("skills", "equipment", "equipment_equipped"),
    # diplomacy_dropdown open captures the faction TARGET list (diplomacy) AND the level-2 NEGOTIATION
    # panels -- the WHAT/deal (FIX #4a): the available clauses (diplomacy_options, + its deal summary /
    # counterparty), the player's staged gifts (diplomacy_your_offers), and the counterparty's staged
    # demands (diplomacy_their_offers). Empty offer lists record n=0 (not emitted); diplomacy_options
    # still emits its `deal` summary whenever the panel is reachable.
    # ‼ live-verify the exact TRIGGER: this fires the L2 capture off the diplomacy_dropdown open. If the
    #   negotiation OFFERS panel fires its OWN PanelOpenedCampaign component (e.g. offers_panel /
    #   diplomacy_hud_offers_panel) when a negotiation starts, map the three L2 panels to THAT component
    #   instead -- that avoids capturing an empty/None deal on a bare target-list open. Until confirmed,
    #   the diplomacy_dropdown trigger is the safe default (graceful: closed offer lists -> n=0 / None deal).
    "diplomacy_dropdown": ("diplomacy", "diplomacy_options",
                           "diplomacy_your_offers", "diplomacy_their_offers"),
    "lords_heroes": ("lords_heroes",),
    "provinces": ("provinces",),
    "known_factions": ("known_factions",),
    # RITES are now EVENT-DRIVEN (both variants fire PanelOpenedCampaign -- confirmed): the High Elves
    # rituals_panel and the Slaanesh great_game_rituals map to their existing PANELS entries.
    "rituals_panel": ("rites",),
    "great_game_rituals": ("rites_great_game",),
    # P1 decision panels (event-triggered capture-on-open):
    "offices": ("offices",),                 # office-assignment slots + occupants
    "character_panel": ("recruit_panel",),   # recruit-lord/hero candidate pool (+ mode title / tabs)
    # HE "Intrigue at the Court" -- best-effort; captures only once the intrigue PANELS entry's
    # card_container is live-verified/tuned (unmapped path => 0 cards => no capture, graceful).
    "intrigue_panel": ("intrigue",),
}
_PANEL_OPEN_RE = re.compile(r'"event":"PanelOpenedCampaign"[^}]*?"component":"([^"]*)"')
# TECH-TAB SWITCH re-trigger (GAP 1). The technology panel renders ONE tech tree at a time into
# technology_list|list_clip|list_box|tech_template|tree_parent|slot_parent; WHICH tree is shown is chosen
# by the vertical CATEGORY TABS (technology_panel|tabs|CcoTechnologyUiTabRecord<subculture>_<campaign|
# military|battle> -- e.g. hef_campaign + hef_military; cth_campaign + cth_battle). Switching a tab
# REBUILDS that single tree: the inactive tab's tree is NOT instantiated (confirmed live -- a tab
# record's only children are tx_tab_title / image_tab_category / turns_icon / search_tech_count, NO tech
# leaves; and list_box holds exactly ONE tech_template), so a broadened find root CANNOT reach it. A tab
# switch fires ComponentLClickUp on the tab record, NOT PanelOpenedCampaign, so the default-tab capture
# on panel-open misses the other tab (~half the techs). Fix, fully PASSIVE (the PLAYER clicks the tab):
# when a tab-record component event appears in the log, RE-ENUMERATE technology so the now-active tab's
# tree is captured as its own menu_open; offline UNIONs all panel=technology snapshots for the full set.
# `"component":"..."` only appears on TWSTATE event JSON lines -- the [ui] hover trace uses a different
# `uicomponent`/`path from root:` form, so this never fires on a mere hover.
_TECH_TAB_RE = re.compile(r'"component":"(CcoTechnologyUiTabRecord[^"]*)"')
POLL_INTERVAL = 1.5           # seconds between low-rate poll sweeps of the POLL_PANELS
# SHORT per-find budget. A find replies in <0.5s when the mod is healthy; the old 8s (and the bus
# DEFAULT_TIMEOUT of 30s) meant one dropped/lagged reply -- routine under multi-client bus contention
# (recorder + probes) -- froze the whole poll for seconds-to-minutes, which reads exactly like the
# "recorder stalled" symptom. Capping it fails a bad find fast and keeps the loop live.
_FIND_TIMEOUT = 6.0    # was 2.5 (tight to survive the old poll storm). Poll is now removed, so finds are
                       # only bursty event-triggered enumerations -> a longer budget lets a momentarily
                       # throttled/unfocused-game find complete instead of returning None (which fails the
                       # whole multi-find enumeration). No freeze risk without the poll re-issuing timeouts.

# Where WH3 writes its per-session script_log_*.txt (the game dir and the engine AppData logs).
_APPDATA_LOGS = os.path.expandvars(r"%APPDATA%/The Creative Assembly/Warhammer3/logs")
try:
    import config as _config                     # noqa: E402
    _GAME_DIR = _config.GAME_DIR
except Exception as e:                           # pragma: no cover
    _GAME_DIR = r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III"
    sys.stderr.write("ui-capture: config import failed, using hardcoded game dir -> %s\n" % repr(e)[:80])


def _extract_key(component_id: str) -> str:
    """Best-effort BARE game key from a key-embedded component Id.

    Strips the row/card wrappers so the key is the entity's own id: a trailing cqi for a
    character row, the province/faction key for a list row, the unit key for a recruit card, a
    Cco* record's numeric id -- and a wh*_skill_/wh*_tech_ leaf IS already its own key.

    Args:
        component_id: e.g. "character_row_1042", "row_entry_wh3_main_combi_province_qiang",
            "wh_main_inf_x_recruitable", "CcoCampaignAncillary137".

    Returns:
        The bare key (e.g. "1042", "wh3_main_combi_province_qiang", "wh_main_inf_x", "137"),
        or the Id itself when no wrapper is recognised (skill/tech leaves).
    """
    s = component_id
    if s.endswith("_recruitable"):
        s = s[:-len("_recruitable")]
    for pre in ("faction_row_entry_", "row_entry_", "character_row_"):
        if s.startswith(pre):
            return s[len(pre):]
    # CcoCampaignRitual30<ritual_key> (rites) -> the bare ritual key: strip the Cco* wrapper
    # and its leading count digits, keeping the wh*_ key. Checked BEFORE the numeric-tail rule
    # so CcoCampaignAncillary137 (no wh key) still falls through to that rule.
    m = re.match(r"^Cco[A-Za-z]+\d+(wh\d?_.+)$", s)
    if m:
        return m.group(1)
    m = re.match(r"^Cco[A-Za-z]+(\d+)$", s)
    if m:
        return m.group(1)
    return s


def _find(bus: Bus, path: str) -> dict | None:
    """One find call. Returns the mod's row {result, child_ids} or None if the bus/game is gone.

    Args:
        bus: A connected Bus.
        path: Pipe-separated component path.

    Returns:
        The response dict (keys include "result" and "child_ids"), or None on TWError
        (bus timeout / game gone) -- the caller treats None as "bus unavailable".
    """
    try:
        return bus.send("find", path, timeout=_FIND_TIMEOUT)
    except TWError:
        return None  # intentional: bus/game gone -> None, hot per-find path; caller reports bus loss (no per-find log)


def _child_ids(resp: dict | None) -> list:
    """The child_ids list from a find response, tolerating both wrapper shapes.

    Args:
        resp: A find response dict, or None.

    Returns:
        A list of non-empty child id strings (empty when absent).
    """
    if not resp:
        return []
    kids = resp.get("child_ids")
    if kids is None:
        kids = (resp.get("result") or {}).get("child_ids") or []
    return [k for k in kids if isinstance(k, str) and k]


def _child_contexts(resp: dict | None) -> list:
    """The child_contexts list from a find response (parallel to child_ids), tolerating shapes.

    The mod (find channel, commit 6ab8bf1) returns `child_contexts` -- one entry per direct
    child, each the child's bound context-object id "<CcoType>:<id>" or null (None) when the
    child binds none of the curated Cco* types. Same de-null/wrapper tolerance as _child_ids,
    but None entries are KEPT (they are meaningful placeholders parallel to child_ids).

    Args:
        resp: A find response dict, or None.

    Returns:
        A list of context strings-or-None (empty when the key is absent, e.g. an old mod build
        with no context channel).
    """
    if not resp:
        return []
    ctxs = resp.get("child_contexts")
    if ctxs is None:
        ctxs = (resp.get("result") or {}).get("child_contexts")
    if ctxs is None:
        return []
    return list(ctxs)


def _context_key(context, prefix: str) -> str | None:
    """Extract the entity key from a context-object id when it matches a Cco* prefix.

    The GENERAL context-extraction helper (reusable across panels): a context id is
    "<CcoType>:<id>" (e.g. "CcoMainUnitRecord:wh2_main_hef_inf_spearmen_0"); given the expected
    Cco* type as `prefix`, return the part AFTER the first colon (the verbatim key/id) when the
    type matches, else None. Splitting on the FIRST colon only keeps keys that themselves contain
    a colon intact. Parameterizing by `prefix` is what lets the same enumerate_context_cards path
    serve units (CcoMainUnitRecord -> unit key), and later occupation / intrigue (their cards'
    own Cco* type -> settlement / faction key).

    Args:
        context: A child/own context value from a find response (a "<CcoType>:<id>" string, or
            None when the node binds no context).
        prefix: The Cco* type name to match (e.g. "CcoMainUnitRecord"), WITHOUT the colon.

    Returns:
        The key after the colon when `context` is a string of the form "<prefix>:<key>", else
        None.
    """
    if not isinstance(context, str):
        return None
    head = prefix + ":"
    if context.startswith(head):
        return context[len(head):]
    return None


def _read_state_text(bus: Bus, path: str) -> str | None:
    """Best-effort on-screen NAME at a component path via GetStateText -- or None (graceful).

    Reads one component's GetStateText (its `text`, or `text_label` when the state text is empty).
    Used to stamp a READABLE name onto an option AT CAPTURE (D9): the equipment / equipment_equipped
    item name + category, read from the card's dy_name / dy_label child nodes (see _named_from_nodes).
    A bus miss, an absent node, or an empty text all return None -- this NEVER raises and NEVER aborts
    the enumeration, so a missing name-node degrades to key-only (offline DB resolution still applies).

    Args:
        bus: A connected Bus.
        path: The component path whose GetStateText is the on-screen name.

    Returns:
        The on-screen name string, or None when unread / not found / empty.
    """
    r = _find(bus, path)
    res = (r or {}).get("result") or {}
    if not res.get("found"):
        return None
    t = res.get("text")
    return t if t not in (None, "") else res.get("text_label")


def _tooltip_title(tooltip) -> str | None:
    """The on-screen LABEL of an ICON button = the TITLE half of its tooltip. Deterministic, no bus call.

    The pre-battle / post-battle / magic-item option buttons carry NO GetStateText on the button node
    (verified EMPTY in run 20260728_224846) -- the displayed name lives in the button's TOOLTIP, which
    the mod's describe() ALREADY returns as `res["tooltip"]` (GetTooltipText, fetched with the state --
    NO extra bus round-trip). WH3 formats that tooltip as "Title||Description" (grabbed live:
    button_attack -> "Fight Battle||Manually fight the siege battle at the city walls."; button_retreat
    -> "Break Siege||..."), so the label is the substring BEFORE "||", stripped. A single deterministic
    source -- no text/text_label/name_sub cascade. Returns None for an empty/None tooltip (graceful:
    the offline key->loc join still names the option).

    Args:
        tooltip: the option's GetTooltipText (res["tooltip"]), or None.

    Returns:
        The title before "||" (stripped), or None when the tooltip is empty/None.
    """
    if not tooltip:
        return None
    title = str(tooltip).split("||", 1)[0].strip()
    return title or None


def _named_from_nodes(bus: Bus, base_path: str, cfg: dict):
    """(onscreen, category) for a card via GetStateText on its dy_name / dy_label CHILD nodes.

    The magic-item POOL cards (CcoCampaignAncillary<CQI>) and equipped-item SLOTS have EMPTY tooltips
    (live-grabbed), so the tooltip-title path used for the pre-battle / captive icon buttons does NOT
    apply here. The item NAME is rendered on a dedicated `dy_name` child text node (context-bound to the
    card's CcoCampaignAncillary), and the item CATEGORY on a `dy_label` child -- BOTH read verbatim via
    GetStateText (exactly the deterministic single-source pattern occupation uses for its `dy_option`).
    Live-grabbed: dy_name(CcoCampaignAncillary:6) -> "Stone of Midnight", :282 -> "Khaine's Ring of
    Fury", :283 -> "Engineer's Knapsack", :54 -> "Longshot's Cap". A bus miss / absent node returns None
    (graceful; never raises).

    Args:
        bus: A connected Bus.
        base_path: The card/slot component path (the dy_name / dy_label children hang off this).
        cfg: A PANELS entry carrying `name_node` (the item-name child) and optional `label_node`
            (the category child).

    Returns:
        (onscreen, category): the GetStateText of the name node, and of the category node (either None).
    """
    onscreen = _read_state_text(bus, base_path + "|" + cfg["name_node"]) if cfg.get("name_node") else None
    category = _read_state_text(bus, base_path + "|" + cfg["label_node"]) if cfg.get("label_node") else None
    return onscreen, category


def _clickable(state, visible, allow=None) -> bool | None:
    """Decide clickability from a leaf's CurrentState + Visible.

    ‼ An UNREAD option (state None / visible None -- typically a virtualized list row that is
    off-screen and so resolves found=false) is UNKNOWN, not clickable. Returning True for it
    (the old bug) poisoned the negatives with false positives. Only a leaf we actually read is
    given a hard True/False.

    Two modes: the default DENYLIST (clickable = visible AND state not in _DISABLED_STATES) and,
    when `allow` is given, an ALLOWLIST (clickable = visible AND state in `allow`). The allowlist
    is for panels whose leaf carries many distinct non-clickable states (construction), where a
    denylist would let an unlisted disabled state (e.g. cannot_build_ever) false-positive.

    Args:
        state: The leaf/sub-node CurrentState (any type; compared lower-cased).
        visible: The Visible flag (True/False/None).
        allow: Optional set of lower-cased states that ARE clickable; when provided it REPLACES
            the disabled-set test (only these states read clickable).

    Returns:
        False if explicitly invisible or not-clickable; True if visibly readable and clickable;
        None if the leaf could not be read (off-screen/virtualized -> unknown).
    """
    if visible is False:
        return False
    if state is None:
        return None                                 # unread -> unknown, NEVER assume clickable
    st = str(state).lower()
    if allow is not None:
        return st in allow                          # allowlist panel: only whitelisted = clickable
    if st in _DISABLED_STATES:
        return False
    if visible is True:
        return True
    return None                                     # visible unknown + not-disabled -> unknown


def enumerate_options(bus: Bus, cfg: dict, max_depth: int = MAX_DEPTH,
                      max_finds: int = MAX_FINDS) -> list | None:
    """Bounded key-embedded RECURSION: list a panel's option leaves with clickable state.

    Descends from cfg["root"] into POSITIONAL (non-option) children, one safe find per level
    (never a tree walk), collecting every child that matches cfg["_opt"] as an option and NOT
    recursing into an option leaf's internals. Each option's clickable state is read from its
    cfg["state_at"] sub-node.

    Args:
        bus: A connected Bus.
        cfg: A PANELS entry (root / option matcher / state_at).
        max_depth: Maximum container depth to descend (latency guard).
        max_finds: Maximum find calls before stopping (latency guard; logged if hit).

    Returns:
        A list of {id, key, clickable, state, visible} option dicts (empty when the panel is
        closed / has no key rows), or None if the bus did not answer (bus gone) so the caller
        can distinguish "closed" from "bus unavailable".
    """
    opt = cfg["_opt"]
    descend = cfg.get("_descend")                   # optional: descend INTO these even if opt-matched
    skip_invis = cfg.get("_skip_invis", descend)    # descend-containers to SKIP when invisible
    state_at = cfg.get("state_at")
    allow = cfg.get("clickable_states")             # None => denylist; set => allowlist
    onscreen_flag = cfg.get("onscreen")             # D10: derive on-screen label from the button tooltip title
    out = []
    seen = set()
    finds = 0
    hit_bound = False
    stack = [(cfg["root"], 0)]
    while stack:
        if finds >= max_finds:
            hit_bound = True
            break
        path, depth = stack.pop()
        resp = _find(bus, path)
        finds += 1
        if resp is None:
            return None                                    # bus/game gone mid-enumeration
        # skip descending into an INVISIBLE descend-container FLAGGED for skipping (the non-staged
        # pre-battle button_set_ twin: only the live battle's set is visible; its buttons are the
        # real options -- descending the hidden twin would capture ghost buttons whose ids collide
        # with the live set's and, via de-dup, mask them). `skip_invis` defaults to `descend`
        # (skip every invisible container) but a panel can narrow it (pre_battle: only the sets,
        # so an invisible `_container` is still descended and its option recorded visible:False).
        if skip_invis is not None and skip_invis.search(path.rsplit("|", 1)[-1]) \
                and (resp.get("result") or {}).get("visible") is False:
            continue
        for cid in _child_ids(resp):
            cpath = path + "|" + cid
            if descend is not None and descend.search(cid):
                if depth < max_depth:
                    stack.append((cpath, depth + 1))       # container-button: descend, don't record
            elif opt.search(cid):
                if cid in seen:
                    continue
                seen.add(cid)
                snode = cpath if not state_at else cpath + "|" + state_at
                sr = _find(bus, snode)
                finds += 1
                if sr is None:
                    return None
                res = sr.get("result") or {}
                state, vis = res.get("state"), res.get("visible")
                orow = {"id": cid, "key": _extract_key(cid),
                        "clickable": _clickable(state, vis, allow),
                        "state": state, "visible": vis,
                        # LABEL (GetStateText) -- the structure-agnostic identity + any cost/value
                        # shown on the option. The reliable id when key/context are absent.
                        "text": res.get("text"), "text_label": res.get("text_label"),
                        # screen rect -> lets the extractor resolve a click's coords to THIS card
                        # (geometry join), giving per-click component identity without shots.
                        "x": res.get("x"), "y": res.get("y"),
                        "w": res.get("w"), "h": res.get("h")}
                if onscreen_flag:                           # D10: icon-only button label = tooltip TITLE
                    tt = res.get("tooltip")                 # GetTooltipText, already fetched (no extra bus call)
                    onscreen = _tooltip_title(tt)           # "Fight Battle||..." -> "Fight Battle"
                    orow["tooltip"] = tt
                    orow["onscreen"] = onscreen             # advisor priority: onscreen > text > text_label > name
                    orow["name"] = onscreen
                    orow["label"] = onscreen
                out.append(orow)
            elif depth < max_depth:
                stack.append((cpath, depth + 1))
    if hit_bound:
        print("ui_component_recorder: WARNING max_finds=%d hit enumerating %s (%d options so "
              "far)" % (max_finds, cfg["root"], len(out)), file=sys.stderr, flush=True)
    return out


def _collect_cards(bus: Bus, list_path: str, source: str, opt, state_at, allow,
                   out: list, seen: set, budget: list, max_depth: int = MAX_DEPTH) -> bool:
    """Bounded key-embedded recursion from ONE card-list container, tagging leaves with `source`.

    Same descent contract as enumerate_options (one safe find per positional level, never a tree
    walk; collect cfg["_opt"] matches as options and do NOT recurse into a leaf), but every option
    it appends carries the given `source` label. Positional `template` wrappers are skipped.

    Args:
        bus: A connected Bus.
        list_path: The list container to descend from.
        source: The normalized source label to stamp on every card found here.
        opt: The compiled option matcher (cfg["_opt"]).
        state_at: The sub-node under a leaf carrying its clickable state (None = the leaf itself).
        allow: Optional allowlist of clickable states (None = the disabled-set denylist).
        out: The accumulator list (mutated in place).
        seen: The de-dup set of already-captured leaf ids (mutated in place).
        budget: A one-element [int] find budget, decremented per find (mutated in place).
        max_depth: Maximum container depth to descend.

    Returns:
        True on normal completion (incl. budget exhaustion), False if the bus went away
        mid-descent (so the caller can propagate the bus-gone None).
    """
    stack = [(list_path, 0)]
    while stack:
        if budget[0] <= 0:
            break
        path, depth = stack.pop()
        resp = _find(bus, path)
        budget[0] -= 1
        if resp is None:
            return False
        for cid in _child_ids(resp):
            cpath = path + "|" + cid
            if opt.search(cid):
                # de-dup by (source, id): the SAME unit id appears in BOTH the global and local
                # pools -- keying on id alone dropped the local copy and lost its source tag.
                if (source, cid) in seen:
                    continue
                seen.add((source, cid))
                snode = cpath if not state_at else cpath + "|" + state_at
                sr = _find(bus, snode)
                budget[0] -= 1
                if sr is None:
                    return False
                res = sr.get("result") or {}
                state, vis = res.get("state"), res.get("visible")
                out.append({"id": cid, "key": _extract_key(cid),
                            "clickable": _clickable(state, vis, allow),
                            "state": state, "visible": vis, "source": source,
                            "text": res.get("text"), "text_label": res.get("text_label"),
                            "x": res.get("x"), "y": res.get("y"),
                            "w": res.get("w"), "h": res.get("h")})
            elif depth < max_depth and "template" not in cid.lower():
                stack.append((cpath, depth + 1))
    return True


def enumerate_sourced_options(bus: Bus, cfg: dict, max_finds: int = MAX_FINDS) -> list | None:
    """Recruitment SOURCE-AWARE enumeration: every recruit card WITH its pool `source`.

    Walks each region in cfg["sources"] (paths relative to cfg["base"]) and collects its
    `_recruitable` cards, tagging each with a normalized source. A region with source "group"
    is the standard pool whose DIRECT children are pool-group containers -- each is descended
    separately with its own label from _recruit_source(group_id) (so local1->local,
    global_min->global, and any future group maps by name). A region with a fixed source label
    (allied / renown) is descended directly. Preserves the key-embedded + clickable-state
    capture; only ADDS the `source` field.

    Args:
        bus: A connected Bus.
        cfg: The recruitment PANELS entry (base / sources / option matcher / state_at).
        max_finds: Maximum find calls before stopping (latency guard; logged if hit).

    Returns:
        A list of {id, key, clickable, state, visible, source} option dicts (empty when the
        panel is closed / all pools empty), or None if the bus did not answer (bus gone) so the
        caller can distinguish "closed" from "bus unavailable".
    """
    opt = cfg["_opt"]
    state_at = cfg.get("state_at")
    allow = cfg.get("clickable_states")
    base = cfg["base"]
    out: list = []
    seen: set = set()
    budget = [max_finds]
    for src in cfg["sources"]:
        list_path = base + "|" + src["list"]
        if src["source"] == "group":
            # the standard pool: its direct children are the pool-GROUP containers; descend each
            # with a per-group source label (discovered live, mapped by name -- generalizable).
            gresp = _find(bus, list_path)
            budget[0] -= 1
            if gresp is None:
                return None
            # cards sit at a FIXED depth below each group container (live-verified path); jump
            # straight there instead of blind-recursing every positional wrapper -- that blind
            # descent is what made this time out (dozens of ~1s finds). One find per group's card
            # list + one per card's state.
            suffix = src.get("group_suffix", "unit_list|listview|list_clip|list_box")
            for g in _child_ids(gresp):
                if "template" in g.lower():           # hidden group template, not a real pool
                    continue
                if not _collect_cards(bus, list_path + "|" + g + "|" + suffix, _recruit_source(g),
                                      opt, state_at, allow, out, seen, budget, max_depth=1):
                    return None
        else:
            if not _collect_cards(bus, list_path, src["source"],
                                  opt, state_at, allow, out, seen, budget):
                return None
        if budget[0] <= 0:
            print("ui_component_recorder: WARNING max_finds=%d hit enumerating recruitment "
                  "(%d options so far)" % (max_finds, len(out)), file=sys.stderr, flush=True)
            break
    return out


def enumerate_positional(bus: Bus, cfg: dict) -> list | None:
    """Enumerate cards whose id carries NO game key (a NUMERIC/hash id) -- occupation's
    settlement_captured cards, the HE Intrigue court's faction dropdown.

    Reads the DIRECT children of cfg["card_container"] (one safe find, never a tree walk), reads
    each card's clickable sub-node cfg["state_sub"] (found/visible/state/x), keeps the cards that
    resolve (found + a real x), and orders them left->right by x (an audit coordinate ONLY).

    IDENTITY -- reliable, NON-positional. With cfg["label_sub"] the card's on-screen NAME is read
    from that child's GetStateText (occupation: dy_option -> "Occupy"/"Sack"/"Subjugate"/"Raise
    Herdstone"...); that becomes the label, and the card id stays the stable KEY (== the occupation
    event's occupation_decision, offline-joinable to the DB name). x-ORDER IS NEVER THE LABEL: the
    option SET/COUNT is faction- and context-specific, so a positional map mislabels (a 2-card offer
    once collapsed to option0/option1). cfg["value_subs"] ({name: child}) additionally scrape the
    per-card cost/reward the game shows. ONLY when NO label_sub is configured (a genuinely text-less
    dropdown) does the caller's positional cfg["labels"] (exact-count) / option<i> map apply as the
    honest last resort -- and such options are flagged positional:True.

    Args:
        bus: A connected Bus.
        cfg: A PANELS entry with positional:True (card_container / state_sub / label_sub /
            value_subs / labels).

    Returns:
        A list of {id, key, label, name, desc, clickable, state, visible, x, values, positional}
        dicts left->right (empty when the panel is closed / has no cards), or None if the bus
        did not answer (bus gone) so the caller can distinguish "closed" from "bus gone".
    """
    container = cfg["card_container"]
    state_sub = cfg.get("state_sub")
    label_sub = cfg.get("label_sub")                   # child node whose GetStateText is the REAL name
    value_subs = cfg.get("value_subs") or {}           # {name: child node} cost/reward nodes (additive)
    labels = cfg.get("labels") or []
    allow = cfg.get("clickable_states")
    resp = _find(bus, container)
    if resp is None:
        return None
    cards = []
    for cid in _child_ids(resp):
        snode = container + "|" + cid if not state_sub else container + "|" + cid + "|" + state_sub
        sr = _find(bus, snode)
        if sr is None:
            return None
        res = sr.get("result") or {}
        # a real card resolves its state sub-node with a rect (settle.py's found + x filter);
        # a positional template / non-card child does not, and is skipped.
        if not res.get("found") or res.get("x") is None:
            continue
        card = {"id": cid, "state": res.get("state"), "visible": res.get("visible"),
                "text": res.get("text"), "text_label": res.get("text_label"),
                # the state_sub (occupation: option_button) tooltip is the option DESCRIPTION and is
                # populated at capture time (e.g. "You occupy this settlement.") -- free, already read.
                "desc": res.get("tooltip"),
                "x": res.get("x"), "y": res.get("y"),
                "w": res.get("w"), "h": res.get("h"), "name": None, "values": {}}
        # ON-SCREEN NAME (the reliable, faction-agnostic identity): GetStateText on the card's label
        # node. For occupation that is the EXACT verified path <card>|option_button|dy_option ->
        # "Occupy"/"Loot & Occupy"/"Sack"/"Raze"/"Subjugate"/"Raise Herdstone"... (label_sub carries
        # the sub-path relative to the card).
        if label_sub:
            lr = _find(bus, container + "|" + cid + "|" + label_sub)
            if lr is None:
                return None
            lres = lr.get("result") or {}
            card["name"] = (lres.get("text") or lres.get("text_label")) or None
        for vk, vnode in value_subs.items():           # optional per-card values (generic; unused now)
            vr = _find(bus, container + "|" + cid + "|" + vnode)
            if vr is None:
                return None
            card["values"][vk] = (vr.get("result") or {}).get("text")
        cards.append(card)
    cards.sort(key=lambda d: d["x"])
    # LABELLING. With a label node (label_sub): identity is the real displayed NAME -- NEVER position.
    # x-order is used ONLY as a stable left->right audit coordinate, never as the label. When no label
    # node is configured (a genuinely text-less keyless dropdown) the caller's positional `labels`
    # (exact-count) / option<i> map is the honest last resort.
    if label_sub:
        for c in cards:
            c["label"] = c["name"] or c["id"]          # real name; card id (stable key) if unreadable
            c["positional"] = c["name"] is None        # flag an honest text-miss fallback
    else:
        if labels and len(cards) == len(labels):       # label ONLY on an exact-count match
            for c, lbl in zip(cards, labels):
                c["label"] = lbl
        for i, c in enumerate(cards):
            c.setdefault("label", "option%d" % i)       # unlabelled -> positional option<i>
            c["positional"] = True
    return [{"id": c["id"], "key": c["id"], "label": c["label"], "name": c.get("name"),
             "desc": c.get("desc"),
             "clickable": _clickable(c["state"], c["visible"], allow),
             "state": c["state"], "visible": c["visible"],
             "x": c["x"], "y": c.get("y"), "w": c.get("w"), "h": c.get("h"),
             "values": c.get("values") or {}, "positional": c["positional"]} for c in cards]


def enumerate_context_cards(bus: Bus, cfg: dict) -> list | None:
    """CONTEXT-CARD enumeration: positional cards identified by their bound CONTEXT OBJECT.

    The GENERAL, reusable context path (mod 6ab8bf1). For a container of POSITIONAL cards whose
    id carries no game key, list the cards (one find on cfg["card_container"] -> child_ids), and
    for EACH card do ONE find on the CARD to read its `child_contexts` (its direct children's
    context-object ids). The card's key is pulled from the child whose context matches
    cfg["cco_prefix"] (units: card_image_holder binds CcoMainUnitRecord:<unit_key>). This is one
    find per card for its direct children's contexts -- NEVER a tree walk. Parameterizing by
    cco_prefix means the same path serves units now and occupation / intrigue later.

    Args:
        bus: A connected Bus.
        cfg: A PANELS entry with context:True -- card_container / card_re (which children are
            cards) / cco_prefix (the Cco* type whose id is the card's key); optional recruit_re
            (a card-id pattern that flags recruiting=True, e.g. QueuedLandUnit).

    Returns:
        A list of {id, key, state, visible, recruiting, kind, source} dicts (id = positional card
        id; key = the context-object key, or None when the card binds no matching context; kind /
        source = "recruiting" vs "active"). Empty when the panel is closed / has no cards, or None
        if the bus did not answer (bus gone) so the caller can distinguish closed from bus-gone.
    """
    container = cfg["card_container"]
    card_re = cfg["_card"]
    prefix = cfg["cco_prefix"]
    recruit_re = cfg.get("_recruit")
    cont_prefix = cfg.get("container_context")             # optional: the container's OWN context
    context_sub = cfg.get("context_sub")                   # optional: occupant bound on a NESTED sub-node
    resp = _find(bus, container)
    if resp is None:
        return None
    # the container's own bound context (equipment_equipped: CcoCampaignCharacter:<cqi> = the panel
    # character) -- stamped onto every card so the offline diff can group the equipped set PER CHARACTER.
    cont_key = _context_key((resp.get("result") or {}).get("context"), cont_prefix) if cont_prefix else None
    out = []
    for cid in _child_ids(resp):
        if not card_re.search(cid):
            continue                                       # positional template / non-card child
        cr = _find(bus, container + "|" + cid)             # ONE find: card state + its child contexts
        if cr is None:
            return None
        res = cr.get("result") or {}
        key = None
        # the bound entity can live on a NESTED sub-node, not a direct child (offices: each slot's
        # occupant sits at portrait_hold|portrait_card -- 2 levels below the slot -- bound to
        # CcoCampaignCharacter). When context_sub is set, read the occupant there FIRST (its own
        # context, then its child contexts); an empty slot binds none -> key stays None.
        if context_sub:
            sub = _find(bus, container + "|" + cid + "|" + context_sub)
            if sub is None:
                return None
            subres = sub.get("result") or {}
            key = _context_key(subres.get("context"), prefix)
            if key is None:
                for ctx in _child_contexts(sub):
                    k = _context_key(ctx, prefix)
                    if k is not None:
                        key = k
                        break
        if key is None:
            for ctx in _child_contexts(cr):                # find the child bound to the wanted Cco*
                k = _context_key(ctx, prefix)
                if k is not None:
                    key = k
                    break
        if key is None:                                    # fall back to the card's OWN context
            key = _context_key(res.get("context"), prefix)
        recruiting = bool(recruit_re.search(cid)) if recruit_re is not None else False
        card = {"id": cid, "key": key,
                "state": res.get("state"), "visible": res.get("visible"),
                "recruiting": recruiting,
                "kind": "recruiting" if recruiting else "active",
                "source": "recruiting" if recruiting else "active",
                "text": res.get("text"), "text_label": res.get("text_label"),
                "x": res.get("x"), "y": res.get("y"),
                "w": res.get("w"), "h": res.get("h")}
        if cfg.get("name_node"):                           # D9: equipped item name = dy_name GetStateText
            onscreen, category = _named_from_nodes(bus, container + "|" + cid, cfg)
            card["onscreen"] = onscreen                    # advisor priority: onscreen > text > text_label > name
            card["name"] = onscreen
            card["label"] = onscreen
            if category is not None:                       # dy_label = item category (Talisman/Armour/...)
                card["category"] = category
        if cont_prefix is not None:                        # the panel character (equipped diff key)
            card["char"] = cont_key
        out.append(card)
    return out


def _clickable_by_state(state, allow=None) -> bool | None:
    """Decide clickability from CurrentState ALONE -- for collapsible stacks where Visible is a lie.

    The child-count panels (army_stances / edicts) are COLLAPSIBLE HUD stacks: when collapsed, an
    option that is fully AVAILABLE (state=active) still reports Visible()=False (verified live), so
    the visible-gated _clickable would mark available options not-clickable. Availability there is
    carried by STATE, not Visible (STANCE_EDICT_CAPTURE.md: active=available, inactive=not). So this
    variant ignores Visible for the clickable decision (the raw `visible` is still recorded on the
    option for losslessness). Same denylist/allowlist split as _clickable.

    Args:
        state: The button CurrentState (any type; compared lower-cased).
        allow: Optional set of lower-cased states that ARE clickable; when provided it REPLACES the
            disabled-set test (only these states read clickable).

    Returns:
        None if the state could not be read (unknown); else True/False from the state test
        (denylist: not in _DISABLED_STATES -- which includes 'inactive'; or the allowlist).
    """
    if state is None:
        return None                                     # unread -> unknown, NEVER assume clickable
    st = str(state).lower()
    if allow is not None:
        return st in allow
    return st not in _DISABLED_STATES


def _strip_prefixes(cid: str, prefixes) -> str:
    """The option KEY: `cid` with the FIRST matching prefix (checked in order) stripped.

    Per-panel key derivation for the child-count panels: army_stances strips the long
    `button_MILITARY_FORCE_ACTIVE_STANCE_TYPE_` (falling back to the generic `button_` so
    button_default -> "default"); edicts strip the leading `button_`. First hit wins, so the more
    specific prefix must precede the generic one in the list.

    Args:
        cid: The child button component id (e.g. "button_MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH",
            "button_wh2_main_edict_hef_reaver_patrols", "button_default").
        prefixes: Ordered iterable of prefixes to try.

    Returns:
        The id with the first matching prefix removed (e.g. "MARCH",
        "wh2_main_edict_hef_reaver_patrols", "default"), or the id unchanged when none match.
    """
    for pre in prefixes:
        if cid.startswith(pre):
            return cid[len(pre):]
    return cid


def enumerate_children(bus: Bus, cfg: dict) -> list | None:
    """CHILD-COUNT enumeration: a collapsible stack's option buttons via bus `find` child-count.

    The path for COLLAPSIBLE HUD stacks (army_stances / edicts) that the visible-gated tree walk
    misses (STANCE_EDICT_CAPTURE.md). For each root in cfg["roots"], ONE find on the root returns
    its DIRECT children via the mod's ChildCount+Find(i) -- which is NOT visibility-gated, so it
    returns every option button even while the stack is collapsed (its own visible=False). Then ONE
    find per child reads that button's state (+ label/rect). Clickable is decided from STATE ALONE
    (_clickable_by_state) because a collapsed stack reports Visible=False on available options. Each
    option carries the root's optional `source` (land / naval) so a shared button_default across the
    two stance stacks is not collapsed into one. When the root also holds NON-option children (the
    diplomacy offer lists mix `diplomatic_option_` clauses with other widgets), an optional
    cfg["option_re"] (compiled to `_opt`) filters child ids to the option leaves only.

    Args:
        bus: A connected Bus.
        cfg: A PANELS entry with children_of:True -- roots (list of {path[, source]}), key_prefixes
            (ordered prefixes for the option key), optional option_re (child-id filter), optional
            clickable_states (allowlist).

    Returns:
        A list of {id, key, clickable, state, visible[, source], text, text_label, x, y, w, h}
        option dicts (empty when no stack is present / an army-settlement is not selected), or None
        if the bus did not answer (bus gone) so the caller can distinguish "closed" from "bus gone".
    """
    key_prefixes = cfg.get("key_prefixes", [])
    allow = cfg.get("clickable_states")                 # None => denylist; set => allowlist
    optre = cfg.get("_opt")                             # optional child-id filter (diplomacy clauses)
    state_at = cfg.get("state_at")                      # optional: read clickable state from a sub-node
    out = []
    for spec in cfg["roots"]:
        root = spec["path"]
        source = spec.get("source")
        resp = _find(bus, root)
        if resp is None:
            return None                                        # bus/game gone mid-enumeration
        for cid in _child_ids(resp):
            if optre is not None and not optre.search(cid):    # skip non-option children (filtered)
                continue
            cr = _find(bus, root + "|" + cid)                  # ONE find: the button's own state
            if cr is None:
                return None
            res = cr.get("result") or {}
            state, vis = res.get("state"), res.get("visible")
            if state_at:                                       # clickable state on a sub-node (pool:
                sr = _find(bus, root + "|" + cid + "|" + state_at)   # ancillary_entry active/inactive)
                if sr is None:
                    return None
                sres = sr.get("result") or {}
                if sres.get("found"):                          # fall back to the node's own state if absent
                    state = sres.get("state")
                    if sres.get("visible") is not None:
                        vis = sres.get("visible")
            opt = {"id": cid, "key": _strip_prefixes(cid, key_prefixes),
                   "clickable": _clickable_by_state(state, allow),
                   "state": state, "visible": vis,
                   "context": res.get("context"),             # own Cco id (pool: CcoCampaignAncillary:<id>)
                   "text": res.get("text"), "text_label": res.get("text_label"),
                   "x": res.get("x"), "y": res.get("y"),
                   "w": res.get("w"), "h": res.get("h")}
            if cfg.get("name_node"):                           # D9: magic-item name = dy_name GetStateText
                onscreen, category = _named_from_nodes(bus, root + "|" + cid, cfg)
                opt["onscreen"] = onscreen                     # advisor priority: onscreen > text > text_label > name
                opt["name"] = onscreen
                opt["label"] = onscreen
                if category is not None:                       # dy_label = item category (Talisman/Armour/...)
                    opt["category"] = category
            if source is not None:                             # disambiguate land/naval button_default
                opt["source"] = source
            out.append(opt)
    return out


def _read_scalars(bus: Bus, specs: list) -> dict:
    """Best-effort SCALAR reads: one find per spec, returning {name: GetStateText | state | None}.

    For the diplomacy negotiation SUMMARY (deal success chance, treasury, send-button state) -- a
    single find per value (NOT a list enumeration), reading GetStateText (default) or the raw state.
    A not-found / bus-miss leaf records None (the value is simply unavailable this tick).

    Args:
        bus: A connected Bus.
        specs: A list of {name, path, read} -- read == "state" reads CurrentState, else GetStateText
            (falling back to GetStateTextLabel when the state text is empty).

    Returns:
        {name: value} for every spec (value None when the leaf is absent / unread).
    """
    out = {}
    for sp in specs:
        r = _find(bus, sp["path"])
        res = (r or {}).get("result") or {}
        if not res.get("found"):
            out[sp["name"]] = None
            continue
        if sp.get("read") == "state":
            out[sp["name"]] = res.get("state")
        else:
            t = res.get("text")
            out[sp["name"]] = t if t not in (None, "") else res.get("text_label")
    return out


def _selected_child_key(bus: Bus, spec: dict) -> str | None:
    """The id-suffix key of the first `prefix` child of a list whose state starts with `selected*`.

    The diplomacy COUNTERPARTY read: the negotiation's target faction is the `faction_row_entry_<KEY>`
    row (in the same sortable_list_factions the `diplomacy` panel enumerates) whose state is
    `selected`/`selected_hover`. Only the faction_row_entry_ prefix is considered (the subculture
    HEADER rows -- wh*_sc_* -- also carry selected* states but are not counterparties), and the key
    is the id with that prefix stripped. Early-exits on the first match (few finds).

    Args:
        bus: A connected Bus.
        spec: {root, prefix, selected_state_prefix} -- the list container, the option-row id prefix,
            and the state-prefix that marks the chosen row (default "selected").

    Returns:
        The chosen row's key (id minus prefix), or None when no matching selected row is found.
    """
    root = spec["root"]
    prefix = spec["prefix"]
    sstate = spec.get("selected_state_prefix", "selected")
    resp = _find(bus, root)
    if resp is None:
        return None
    for cid in _child_ids(resp):
        if not cid.startswith(prefix):
            continue                                           # skip subculture headers / non-rows
        cr = _find(bus, root + "|" + cid)
        st = ((cr or {}).get("result") or {}).get("state")
        if isinstance(st, str) and st.startswith(sstate):
            return cid[len(prefix):]
    return None


def panel_open(bus: Bus, cfg: dict) -> bool | None:
    """Cheap check: is this panel currently OPEN? (one find on cfg["open_path"]).

    Args:
        bus: A connected Bus.
        cfg: A PANELS entry (open_path / open_by).

    Returns:
        True/False for open/closed, or None when the bus did not answer.
    """
    resp = _find(bus, cfg["open_path"])
    if resp is None:
        return None
    r = resp.get("result") or {}
    if not r.get("found"):
        return False
    ob = cfg.get("open_by", "found")
    if ob == "height":
        return (r.get("h") or 0) > 20
    if ob == "visible":
        return r.get("visible") is not False
    return True


def capture_panel(bus: Bus, name: str) -> dict | None:
    """Enumerate one panel and build its menu_open row (with the skills no-points reason).

    Args:
        bus: A connected Bus.
        name: A key of PANELS.

    Returns:
        A {"kind":"menu_open","panel","n","options"[,"reason"][,"deal"]} row, or None if the bus
        did not answer. The `deal` sibling (diplomacy_options only) carries the negotiation summary
        (counterparty / success chance / treasury / send-button state).
    """
    cfg = PANELS[name]
    # dispatch by panel kind: positional (x-order cards, no key) -> enumerate_positional;
    # context (positional cards identified by their bound Cco* context object, e.g. army) ->
    # enumerate_context_cards; recruitment (`sources` spec) -> the SOURCE-AWARE enumerator (each
    # card tagged with its recruit pool); every other panel uses the flat key-embedded recursion.
    if cfg.get("positional"):
        opts = enumerate_positional(bus, cfg)
    elif cfg.get("context"):
        opts = enumerate_context_cards(bus, cfg)
    elif cfg.get("children_of"):                       # collapsible HUD stacks (army_stances, edicts)
        opts = enumerate_children(bus, cfg)
    elif cfg.get("sources"):
        opts = enumerate_sourced_options(bus, cfg)
    else:
        opts = enumerate_options(bus, cfg)
    if opts is None:
        return None
    row = {"kind": "menu_open", "panel": name, "n": len(opts), "options": opts}
    rp = cfg.get("reason_path")
    if rp:
        rr = _find(bus, rp)
        if rr is not None:
            row["reason"] = (rr.get("result") or {}).get("state")
    # diplomacy negotiation SUMMARY: scalar reads (deal success chance / treasury / send-button
    # state) + the selected COUNTERPARTY faction, attached as a sibling `deal` object. It is
    # captured whenever the negotiation panel is open -- even with 0 staged clauses -- because
    # scan_open keeps a row when it carries a `deal` (see its n>0-or-deal gate).
    scalars = cfg.get("scalars")
    sel = cfg.get("selected_row")
    if scalars or sel:
        deal = _read_scalars(bus, scalars) if scalars else {}
        if sel:
            deal[sel["name"]] = _selected_child_key(bus, sel)
        row["deal"] = deal
    # RECRUIT panel metadata: the mode TITLE (tx_recruit -> "Recruit Lord" / "Recruit Hero") lets the
    # advisor split lord vs hero, and the type-TAB ids (chs_lord / chs_sorcerer_lord / ...) name the
    # available lord/hero classes. Both are best-effort panel-level reads attached to the row (a
    # bus-miss / absent node simply omits the field).
    tp = cfg.get("title_path")
    if tp:
        tr = _find(bus, tp)
        if tr is not None:
            tres = tr.get("result") or {}
            row["title"] = (tres.get("text") or tres.get("text_label")) or None
    tc = cfg.get("tab_container")
    if tc:
        tcr = _find(bus, tc)
        if tcr is not None:
            row["tabs"] = _child_ids(tcr)
    # GAP 1: which CATEGORY TAB is active (technology) -> stamp `tab` so offline can label the snapshot's
    # screen and UNION campaign+military. Best-effort: the selected tab's state starts with "selected"
    # (else None). Reuses the selected-child reader (same as the diplomacy counterparty row).
    atc = cfg.get("active_tab_container")
    if atc:
        row["tab"] = _selected_child_key(bus, {"root": atc, "prefix": "CcoTechnologyUiTabRecord",
                                               "selected_state_prefix": "selected"})
    return row


def scan_open(bus: Bus, panels: dict | None = None, exclude=None) -> tuple[list, bool]:
    """Enumerate every configured panel that is currently OPEN and populated.

    Args:
        bus: A connected Bus.
        panels: The panel config (default PANELS).
        exclude: Optional iterable of panel names to SKIP -- the event scan passes POLL_PANELS so
            the poll-driven, PanelOpenedCampaign-independent panels (army_stances / edicts /
            construction / diplomacy_options / _your_offers / _their_offers) are not also
            full-scanned on every event fire (keeps bus load low).

    Returns:
        (rows, bus_ok): rows = the menu_open dict of each open+populated panel; bus_ok = did
        the bus answer at all (False => bus unavailable).
    """
    panels = panels or PANELS
    exclude = set(exclude or ())
    rows = []
    bus_ok = False
    for name in panels:
        if name in exclude:
            continue
        try:                                              # one throwing panel must NEVER kill the scan
            op = panel_open(bus, panels[name])            # (which would end the whole ui-watch thread)
        except Exception as e:                            # brief: know it happened + why, not a traceback
            sys.stderr.write("ui-capture: panel_open(%s) skipped -> %s\n" % (name, repr(e)[:80]))
            continue
        if op is None:
            continue                                      # bus miss for this probe
        bus_ok = True
        if not op:
            continue
        try:
            row = capture_panel(bus, name)
        except Exception as e:                            # brief: know it happened + why, not a traceback
            sys.stderr.write("ui-capture: capture_panel(%s) skipped -> %s\n" % (name, repr(e)[:80]))
            continue
        if row is None:
            continue
        # keep populated option-sets AND negotiation-summary rows (diplomacy_options carries a
        # `deal` even when 0 clauses are staged -- the counterparty/success/treasury still matter).
        if row["n"] > 0 or row.get("deal"):
            rows.append(row)
    return rows, bus_ok


# ---- event-trigger tailer (the primary watch mode) --------------------------------------
def _newest_script_log() -> str | None:
    """Path of the newest script_log*.txt WH3 is writing (game dir or AppData logs), or None.

    Returns:
        The most-recently-modified matching file path, or None when none exist.
    """
    best, best_m = None, -1.0
    for base in (_APPDATA_LOGS, _GAME_DIR):
        try:
            for f in os.listdir(base):
                lf = f.lower()
                if lf.startswith("script_log") and lf.endswith(".txt"):
                    p = os.path.join(base, f)
                    try:
                        m = os.path.getmtime(p)
                    except OSError:
                        continue  # intentional: getmtime race on a vanished log -> skip (no per-file log)
                    if m > best_m:
                        best, best_m = p, m
        except OSError:
            continue  # intentional: log dir unreadable -> skip base, re-scanned next loop (no per-loop log)
    return best


def _panel_opened(chunk: str) -> bool:
    """True if a newly-appended log chunk contains a real PanelOpenedCampaign OCCURRENCE.

    Excludes the one-time `[interventions] ... added trigger condition for event
    PanelOpenedCampaign` registration lines (startup noise) -- a real fire is the mod's
    `"event":"PanelOpenedCampaign"` TWSTATE row.

    Args:
        chunk: Freshly-appended bytes of the script log, decoded to text.

    Returns:
        True when a genuine panel-open occurrence is present.
    """
    if "PanelOpenedCampaign" not in chunk:
        return False
    if '"event":"PanelOpenedCampaign"' in chunk:
        return True
    # generic fallback: any PanelOpenedCampaign line that is not a trigger-registration line
    for ln in chunk.splitlines():
        if "PanelOpenedCampaign" in ln and "added trigger condition" not in ln:
            return True
    return False


def _opened_components(chunk: str) -> list:
    """Component ids of every real PanelOpenedCampaign occurrence in a fresh log chunk, in order.

    The PanelOpenedCampaign TWSTATE row NAMES the panel that opened via its `component` field (e.g.
    "settlement_captured", "recruitment_options", "technology_panel"). Driving capture off this is
    event-accurate and needs NO open_path find -- so capture never depends on (or is stalled by) a
    full-tree search for an absent faction-specific panel. Returns [] for a chunk with no genuine fire.
    """
    if "PanelOpenedCampaign" not in chunk:
        return []
    return _PANEL_OPEN_RE.findall(chunk)


def _tech_tab_switched(chunk: str) -> bool:
    """True if a fresh log chunk contains a technology-panel TAB component event (GAP 1 re-trigger).

    A tab switch (the player clicking a CcoTechnologyUiTabRecord* category tab) fires a TWSTATE
    component event, NOT PanelOpenedCampaign -- see _TECH_TAB_RE. On such an event the recorder
    re-enumerates the technology panel to capture the now-active tab's tree.
    """
    return "CcoTechnologyUiTabRecord" in chunk and bool(_TECH_TAB_RE.search(chunk))


def watch(bus: Bus, emit, panels: dict | None = None, is_running=lambda: True) -> None:
    """EVENT-TRIGGERED watch (+ low-rate poll): tail the newest script_log for PanelOpenedCampaign;
    on each fire scan the event panels and emit the open+populated one's options. In parallel, a
    low-rate POLL (every POLL_INTERVAL) captures the POLL_PANELS -- army_stances / edicts /
    construction -- which fire NO PanelOpenedCampaign, capturing each on a rising edge.

    Self-arming and resilient: if the bus stops answering it emits one `bus_unavailable` status
    and backs off, re-arming with `bus_available` when the bus returns. If no script_log exists
    yet (game not up) it waits for one to appear. A panel that is still open from a prior fire is
    not re-emitted until it has closed or REEMIT_COOLDOWN has elapsed. The poll is skipped while
    the bus is known-down so a dead game is not hammered with timing-out finds.

    Args:
        bus: A connected Bus.
        emit: Callback(row_dict) -> None; the recorder passes a JSONL writer here.
        panels: Panels to watch (default PANELS).
        is_running: Predicate; the loop exits when it returns False.

    Returns:
        None.
    """
    panels = panels or PANELS
    bus_ok = None                                          # None = no status emitted yet
    logpath, fh, off = None, None, 0
    last_emit = {}                                         # panel -> (last emit epoch)
    prev_open = {name: False for name in panels}
    last_poll = 0.0                                        # epoch of the last POLL_PANELS sweep
    poll_next = {}                                         # panel -> earliest epoch to poll again (absent-find backoff)
    poll_miss = {}                                         # panel -> consecutive bus-miss count (drives the backoff)
    last_beat = 0.0                                        # epoch of the last liveness heartbeat
    seen_unmapped = set()                                  # PanelOpenedCampaign components with no map (log once)
    poll_names = [n for n in POLL_PANELS if n in panels]   # poll targets present in this panel set
    event_names = [n for n in panels if n not in POLL_PANELS]
    # GAP 1 tech-tab re-trigger state: `keys` = the option-key SET of the last technology emit (dedups a
    # re-clicked tab); `retry_at` = a single scheduled re-capture (0 = none) that catches the tab's tree a
    # beat after the click, in case the first re-capture raced the tree rebuild.
    last_tech = {"keys": None, "retry_at": 0.0}

    def _report_bus(ok):
        nonlocal bus_ok
        if ok and bus_ok is not True:
            emit({"kind": "ui_status", "status": "bus_available"})
            bus_ok = True
        elif not ok and bus_ok is not False:
            emit({"kind": "ui_status", "status": "bus_unavailable"})
            bus_ok = False

    def _emit_scan(rows, checked_names):
        # shared fresh/cooldown gate for BOTH the event scan and the poll: emit a panel's options
        # on a rising edge (was closed) or once REEMIT_COOLDOWN has elapsed, then reset each CHECKED
        # panel's open flag so a close re-arms it. checked_names bounds the reset to the panels this
        # scan actually inspected, so the event scan and the poll never clobber each other's flags.
        open_names = {r["panel"] for r in rows}
        now = time.time()
        for r in rows:
            name = r["panel"]
            fresh = (not prev_open.get(name)) or (now - last_emit.get(name, 0) > REEMIT_COOLDOWN)
            if fresh:
                emit(r)
                last_emit[name] = now
        for name in checked_names:                         # reset transition flags on close
            prev_open[name] = name in open_names

    def _recapture_technology():
        # GAP 1: capture whichever tech TAB is now active and emit it if its option set is NEW (a tab
        # switch -> a rebuilt tree). Bypasses the reemit cooldown (the set genuinely changed), and dedups
        # by option-key set so re-clicking the SAME tab does not spam duplicates. Fully passive: it runs
        # only because the PLAYER clicked a tab (a CcoTechnologyUiTabRecord* event in the log).
        # Returns True iff it emitted a NEW tab's tree (lets the caller skip the retry when unneeded).
        if "technology" not in panels:
            return False
        try:
            row = capture_panel(bus, "technology")
        except Exception as e:                             # one bad capture never kills the loop
            sys.stderr.write("ui-capture: capture_panel(technology) skipped -> %s\n" % repr(e)[:80])
            return False
        if row is None:                                    # bus miss -> report + leave dedup memory intact
            _report_bus(False)
            return False
        _report_bus(True)
        if row["n"] <= 0:
            return False
        keys = frozenset(o.get("key") for o in row["options"])
        if keys and keys != last_tech["keys"]:             # a DIFFERENT tab's tree -> emit it
            emit(row)
            last_emit["technology"] = time.time()
            prev_open["technology"] = True
            last_tech["keys"] = keys
            return True
        return False

    while is_running():
        # (re)attach to the newest script log (handles rotation / game restart / late start)
        newest = _newest_script_log()
        if newest and newest != logpath:
            try:
                if fh:
                    fh.close()
                fh = open(newest, "rb")
                fh.seek(0, os.SEEK_END)                    # only NEW lines from here on
                off = fh.tell()
                logpath = newest
            except OSError as e:
                fh, logpath = None, None
                sys.stderr.write("ui-capture: cannot attach to %s -> %s\n"
                                 % (os.path.basename(newest), repr(e)[:60]))
        if fh is None:
            time.sleep(1.0)
            continue
        try:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size < off:                                 # rotated/truncated
                off = 0
            chunk = b""
            if size > off:
                fh.seek(off)
                chunk = fh.read(size - off)
                off = size
        except OSError as e:
            sys.stderr.write("ui-capture: log read failed, re-attaching -> %s\n" % repr(e)[:60])
            fh, logpath = None, None
            time.sleep(0.5)
            continue

        # EVENT-DRIVEN CAPTURE: PanelOpenedCampaign NAMES the opened panel via `component`. Map it to the
        # configured event panel(s) and capture THOSE directly -- no scan of every open_path (which does a
        # full-tree search + 2.5s timeout for each absent faction-specific panel: the old ~0-capture race).
        # POLL_PANELS are captured by the poll below, so they are not in COMPONENT_PANELS.
        slept = WATCH_POLL
        ok = None
        chunk_text = chunk.decode("utf-8", "replace") if chunk else ""
        if chunk:
            targets = []
            for c in _opened_components(chunk_text):
                mapped = COMPONENT_PANELS.get(c)
                if not mapped:
                    if c not in seen_unmapped:                 # surface a panel we don't yet map, once
                        seen_unmapped.add(c)
                        sys.stderr.write("ui-capture: unmapped PanelOpenedCampaign component %r\n" % c)
                    continue
                for pname in mapped:
                    if pname in panels and pname not in targets:
                        targets.append(pname)
            if targets:
                rows = []
                for pname in targets:
                    try:
                        row = capture_panel(bus, pname)
                    except Exception as e:                     # one bad panel never kills the loop
                        sys.stderr.write("ui-capture: capture_panel(%s) skipped -> %s\n" % (pname, repr(e)[:80]))
                        continue
                    if row is None:                            # bus miss for this panel
                        continue
                    ok = True
                    if row["n"] > 0 or row.get("deal"):
                        rows.append(row)
                _report_bus(bool(ok))
                if not ok:
                    slept = BACKOFF_INTERVAL
                _emit_scan(rows, targets)
                # GAP 1: seed the tech-tab dedup memory from the default-tab capture on panel OPEN, so a
                # subsequent tab switch is recognised as a DIFFERENT set (and re-clicking the opened tab
                # is deduped). Re-seeded each open, so it never goes stale across panel sessions.
                for r in rows:
                    if r["panel"] == "technology":
                        last_tech["keys"] = frozenset(o.get("key") for o in r["options"])

            # GAP 1: a tech-tab SWITCH (CcoTechnologyUiTabRecord* component event -- NOT PanelOpenedCampaign)
            # re-enumerates technology so the now-active tab's tree is captured. Capture immediately and
            # schedule ONE short retry (the tree rebuild can lag the click by a beat); both are deduped by
            # option-key set, so at most one new snapshot per distinct tab.
            if _tech_tab_switched(chunk_text):
                if not _recapture_technology():            # raced the rebuild (or same tab) -> one retry
                    last_tech["retry_at"] = time.time() + 0.8

        # GAP 1: the scheduled tech-tab retry (catches the rebuilt tree if the immediate re-capture raced).
        if last_tech["retry_at"] and time.time() >= last_tech["retry_at"]:
            last_tech["retry_at"] = 0.0
            if bus_ok is not False:
                _recapture_technology()

        # LOW-RATE POLL: capture the PanelOpenedCampaign-independent targets (army_stances / edicts /
        # construction) on a rising edge. Gated by POLL_INTERVAL and skipped while the bus is known
        # DOWN (bus_ok is False) so a dead game is not hit with timing-out finds. Only these few
        # roots' cheap open-check runs each sweep -- bus load stays low.
        now = time.time()
        if poll_names and bus_ok is not False and now - last_poll >= POLL_INTERVAL:
            last_poll = now
            # Per-panel BACKOFF (junk-find guard): a poll target whose open-check returns None (a bus MISS
            # -> the mod did a SLOW full-tree search for a component that does NOT exist on this faction,
            # e.g. rituals_panel/great_game_rituals for Wood Elves) is exponentially backed off, so we stop
            # re-issuing that junk find every POLL_INTERVAL. It was flapping the bus + starving the
            # event-capture finds. A panel that answers (open OR cleanly-closed) re-arms the normal cadence.
            due = [n for n in poll_names if now >= poll_next.get(n, 0.0)]
            prows, pok = [], False
            for n in due:
                try:
                    op = panel_open(bus, panels[n])
                except Exception as e:
                    sys.stderr.write("ui-capture: panel_open(%s) skipped -> %s\n" % (n, repr(e)[:80]))
                    continue
                if op is None:                                 # bus miss = the expensive junk find -> back off
                    poll_miss[n] = poll_miss.get(n, 0) + 1
                    poll_next[n] = now + min(30.0, POLL_INTERVAL * 2 ** min(poll_miss[n], 5))
                    continue
                pok = True
                poll_miss[n] = 0                               # answered -> re-arm normal cadence
                if op:
                    try:
                        row = capture_panel(bus, n)
                    except Exception as e:
                        sys.stderr.write("ui-capture: capture_panel(%s) skipped -> %s\n" % (n, repr(e)[:80]))
                        continue
                    if row is not None and (row["n"] > 0 or row.get("deal")):
                        prows.append(row)
            if due:
                _report_bus(pok)
            if prows:
                _emit_scan(prows, due)

        if now - last_beat >= 30.0:                        # liveness heartbeat: the old freeze stopped
            last_beat = now                                # ALL output, so a stalled loop is now visible
            emit({"kind": "ui_status", "status": "alive"})  # as "no heartbeat", not "nothing to capture"

        time.sleep(slept)


def _main(argv: list[str]) -> None:
    """CLI: --panel <name> one-shot dump | --scan dump every open panel | --watch event loop."""
    import json
    bus = Bus()
    if "--panel" in argv:
        name = argv[argv.index("--panel") + 1]
        if name not in PANELS:
            print("unknown panel %r; known: %s" % (name, sorted(PANELS)))
            return
        row = capture_panel(bus, name)
        print(json.dumps(row, indent=1))
    elif "--scan" in argv:
        rows, ok = scan_open(bus)
        print(json.dumps({"bus_available": ok, "open_panels": rows}, indent=1))
    else:
        watch(bus, lambda row: print(json.dumps(row), flush=True))


if __name__ == "__main__":
    _main(sys.argv[1:])
