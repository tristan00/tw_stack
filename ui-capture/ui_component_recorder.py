r"""ui_component_recorder.py -- at menu-open, capture a panel's options and whether each is clickable.

    python ui_component_recorder.py --panel <name> | --scan | --watch
"""
from __future__ import annotations

import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bus"))
from bus import Bus
from errors import TWError

_DISABLED_STATES = {"inactive", "locked", "disabled", "greyed", "grayed", "hidden",
                    "unavailable", "dummy"}

MAX_DEPTH = 10
MAX_FINDS = 600

_RECRUIT_SOURCE_PREFIX = (
    ("local",     "local"),
    ("global",    "global"),
    ("renown",    "renown"),
    ("mercenary", "renown"),
    ("merc",      "renown"),
    ("allied",    "allied"),
    ("outpost",   "allied"),
)


def _recruit_source(container: str) -> str:
    """Normalized recruit-source label for a pool-group container id, or the raw id when unrecognised."""
    g = (container or "").lower()
    for pre, label in _RECRUIT_SOURCE_PREFIX:
        if g.startswith(pre) or pre in g:
            return label
    return container or "unknown"

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
        "option_re": r"^row_entry_.+",
        "state_at": None,
    },
    "known_factions": {
        "root": ("hud_campaign|radar_things|dropdown_parent|dropdown_factions|panel|panel_clip|"
                 "sortable_list_factions|list_clip|list_box"),
        "open_path": "hud_campaign|radar_things|dropdown_parent|dropdown_factions",
        "open_by": "height",
        "option_re": r"^faction_row_entry_(?!.*_sc_).+",
        "state_at": None,
    },
    "recruitment": {
        "base": "units_panel|main_units_panel|recruitment_docker|recruitment_options",
        "open_path": "units_panel|main_units_panel|recruitment_docker|recruitment_options",
        "open_by": "visible",
        "option_re": r".+_recruitable$",
        "state_at": None,
        "sources": [
            {"list": "recruitment_listbox|recruitment_pool_list|list_clip|list_box",
             "source": "group"},
            {"list": ("allied_recuitment_display|recruitment_holder|unit_list|listview|"
                      "list_clip|allied_unit_list"), "source": "allied"},
            {"list": "mercenary_display|frame|listview|list_clip|list_box", "source": "renown"},
        ],
    },
    "army": {
        "context": True,
        "card_container": "units_panel|main_units_panel|units",
        "card_re": r"^(?:AgentUnit|LandUnit|QueuedLandUnit) \d+$",
        "cco_prefix": "CcoMainUnitRecord",
        "recruit_re": r"^QueuedLandUnit ",
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
        "option_re": r"^CcoCampaignAncillary\d+$",
        "key_prefixes": ["CcoCampaignAncillary"],
        "state_at": "ancillary_entry",
        "name_node": "dy_name",
        "label_node": "dy_label",
        "open_path": "character_details_panel",
        "open_by": "found",
    },
    "equipment_equipped": {
        "context": True,
        "card_container": ("character_details_panel|character_context_parent|tab_panels|"
                           "character_details_subpanel|character_details_panel_details|"
                           "equipment_holder|ancillary_parent|magic_items_equiped|"
                           "equiped_items_listview|list_clip|equiped_items_list"),
        "card_re": r"^CcoAncillariesCategoryRecord\d+$",
        "cco_prefix": "CcoCampaignAncillary",
        "container_context": "CcoCampaignCharacter",
        "name_node": "dy_name",
        "label_node": "dy_label",
        "open_path": "character_details_panel",
        "open_by": "found",
    },
    "construction": {
        "root": ("building_construction_popup|popup_panel|building_tree_holder|"
                 "construction_building_tree|chain_list"),
        "open_path": "building_construction_popup",
        "open_by": "visible",
        "option_re": r"^wh\d?_",
        "state_at": "square_building_button",
        "clickable_states": {"normal"},
    },
    "rites": {
        "root": "rituals_panel|panel_frame|context_rituals_list",
        "open_path": "rituals_panel",
        "open_by": "found",
        "option_re": r"^CcoCampaignRitual",
        "state_at": "action|button_perform",
    },
    "rites_great_game": {
        "root": "great_game_rituals|rituals_panel|rituals_list",
        "open_path": "great_game_rituals",
        "open_by": "found",
        "option_re": r"^CcoCampaignRitual",
        "state_at": None,
    },
    "pre_battle": {
        "root": ("popup_pre_battle|mid|battle_deployment|pre_battle_deployment_panel|"
                 "regular_deployment|button_docker|button_parent_when_no_countdown_active"),
        "open_path": "popup_pre_battle",
        "open_by": "visible",
        "option_re": r"^button_.+",
        "descend_re": r"^button_set_|_container$",
        "skip_invisible_descend_re": r"^button_set_",
        "state_at": None,
        "onscreen": True,
    },
    "post_battle_captives": {
        "root": ("popup_battle_results|mid|battle_results|post_battle_results_panel|"
                 "button_set_win_holder|button_set_win"),
        "open_path": "popup_battle_results",
        "open_by": "visible",
        "option_re": r"^button_captive_option_.+",
        "state_at": None,
    },
    "occupation": {
        "positional": True,
        "card_container": "settlement_captured|button_parent",
        "state_sub": "option_button",
        "label_sub": "option_button|dy_option",
        "open_path": "settlement_captured",
        "open_by": "visible",
    },
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
    "offices": {
        "context": True,
        "card_container": "offices|main|offices_panel|portrait_holder",
        "card_re": r"^(?!template).+",
        "cco_prefix": "CcoCampaignCharacter",
        "context_sub": "portrait_hold|portrait_card",
        "open_path": "offices",
        "open_by": "found",
    },
    "recruit_panel": {
        "context": True,
        "card_container": ("character_panel|character_panel_info_holder|general_selection_panel|"
                           "main_holder|character_list_parent|character_list|listview|list_clip|"
                           "list_box"),
        "card_re": r"^general_candidate_\d+",
        "cco_prefix": "CcoCampaignCharacter",
        "title_path": "character_panel|character_panel_info_holder|header|title_plaque|tx_recruit",
        "tab_container": ("character_panel|character_panel_info_holder|general_selection_panel|"
                          "lords_and_agents_holder|lord_parent|list_box"),
        "open_path": "character_panel",
        "open_by": "found",
    },
    "intrigue": {
        "context": True,
        "card_container": "intrigue_panel",
        "card_re": r"^option\d+$",
        "cco_prefix": "CcoCampaignFaction",
        "open_path": "intrigue_panel",
        "open_by": "found",
    },
}
for _cfg in PANELS.values():
    if "option_re" in _cfg:
        _cfg["_opt"] = re.compile(_cfg["option_re"])
    if "descend_re" in _cfg:
        _cfg["_descend"] = re.compile(_cfg["descend_re"])
    if "skip_invisible_descend_re" in _cfg:
        _cfg["_skip_invis"] = re.compile(_cfg["skip_invisible_descend_re"])
    if "card_re" in _cfg:
        _cfg["_card"] = re.compile(_cfg["card_re"])
    if "recruit_re" in _cfg:
        _cfg["_recruit"] = re.compile(_cfg["recruit_re"])

WATCH_POLL = 0.35
BACKOFF_INTERVAL = 10.0
REEMIT_COOLDOWN = 3.0

POLL_PANELS = ("construction", "army_stances")

COMPONENT_PANELS = {
    "settlement_captured": ("occupation",),
    "popup_battle_results": ("post_battle_captives",),
    "popup_pre_battle": ("pre_battle",),
    "technology_panel": ("technology",),
    "recruitment_options": ("recruitment",),
    "units_recruitment": ("recruitment",),
    "character_details_panel": ("skills", "equipment", "equipment_equipped"),
    "diplomacy_dropdown": ("diplomacy", "diplomacy_options",
                           "diplomacy_your_offers", "diplomacy_their_offers"),
    "lords_heroes": ("lords_heroes",),
    "provinces": ("provinces",),
    "known_factions": ("known_factions",),
    "rituals_panel": ("rites",),
    "great_game_rituals": ("rites_great_game",),
    "offices": ("offices",),
    "character_panel": ("recruit_panel",),
    "intrigue_panel": ("intrigue",),
}
_PANEL_OPEN_RE = re.compile(r'"event":"PanelOpenedCampaign"[^}]*?"component":"([^"]*)"')
_TECH_TAB_RE = re.compile(r'"component":"(CcoTechnologyUiTabRecord[^"]*)"')
POLL_INTERVAL = 1.5
_FIND_TIMEOUT = 6.0

_APPDATA_LOGS = os.path.expandvars(r"%APPDATA%/The Creative Assembly/Warhammer3/logs")
try:
    import config as _config
    _GAME_DIR = _config.GAME_DIR
except Exception as e:
    _GAME_DIR = r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III"
    sys.stderr.write("ui-capture: config import failed, using hardcoded game dir -> %s\n" % repr(e)[:80])


def _extract_key(component_id: str) -> str:
    """Best-effort BARE game key from a key-embedded component Id, or the Id itself when no wrapper matches."""
    s = component_id
    if s.endswith("_recruitable"):
        s = s[:-len("_recruitable")]
    for pre in ("faction_row_entry_", "row_entry_", "character_row_"):
        if s.startswith(pre):
            return s[len(pre):]
    m = re.match(r"^Cco[A-Za-z]+\d+(wh\d?_.+)$", s)
    if m:
        return m.group(1)
    m = re.match(r"^Cco[A-Za-z]+(\d+)$", s)
    if m:
        return m.group(1)
    return s


def _find(bus: Bus, path: str) -> dict | None:
    """One find call. Returns the mod's row {result, child_ids} or None if the bus/game is gone."""
    try:
        return bus.send("find", path, timeout=_FIND_TIMEOUT)
    except TWError:
        return None


def _child_ids(resp: dict | None) -> list:
    """The child_ids list from a find response, tolerating both wrapper shapes."""
    if not resp:
        return []
    kids = resp.get("child_ids")
    if kids is None:
        kids = (resp.get("result") or {}).get("child_ids") or []
    return [k for k in kids if isinstance(k, str) and k]


def _child_contexts(resp: dict | None) -> list:
    """The child_contexts list from a find response, parallel to child_ids (None entries are kept)."""
    if not resp:
        return []
    ctxs = resp.get("child_contexts")
    if ctxs is None:
        ctxs = (resp.get("result") or {}).get("child_contexts")
    if ctxs is None:
        return []
    return list(ctxs)


def _context_key(context, prefix: str) -> str | None:
    """The part of a "<CcoType>:<id>" context id after the first colon when the type matches `prefix`, else None."""
    if not isinstance(context, str):
        return None
    head = prefix + ":"
    if context.startswith(head):
        return context[len(head):]
    return None


def _read_state_text(bus: Bus, path: str) -> str | None:
    """Best-effort on-screen name at a component path via GetStateText (text, else text_label), or None."""
    r = _find(bus, path)
    res = (r or {}).get("result") or {}
    if not res.get("found"):
        return None
    t = res.get("text")
    return t if t not in (None, "") else res.get("text_label")


def _tooltip_title(tooltip) -> str | None:
    """The on-screen label of an icon button: the "Title||Description" tooltip's title, or None."""
    if not tooltip:
        return None
    title = str(tooltip).split("||", 1)[0].strip()
    return title or None


def _named_from_nodes(bus: Bus, base_path: str, cfg: dict):
    """(onscreen, category) for a card via GetStateText on its cfg["name_node"] / cfg["label_node"] children."""
    onscreen = _read_state_text(bus, base_path + "|" + cfg["name_node"]) if cfg.get("name_node") else None
    category = _read_state_text(bus, base_path + "|" + cfg["label_node"]) if cfg.get("label_node") else None
    return onscreen, category


def _clickable(state, visible, allow=None) -> bool | None:
    """Clickability from a leaf's CurrentState + Visible: True/False when read, None when UNREAD (never assume clickable)."""
    if visible is False:
        return False
    if state is None:
        return None
    st = str(state).lower()
    if allow is not None:
        return st in allow
    if st in _DISABLED_STATES:
        return False
    if visible is True:
        return True
    return None


def enumerate_options(bus: Bus, cfg: dict, max_depth: int = MAX_DEPTH,
                      max_finds: int = MAX_FINDS) -> list | None:
    """Bounded key-embedded recursion from cfg["root"]: a list of option dicts, or None when the bus did not answer."""
    opt = cfg["_opt"]
    descend = cfg.get("_descend")
    skip_invis = cfg.get("_skip_invis", descend)
    state_at = cfg.get("state_at")
    allow = cfg.get("clickable_states")
    onscreen_flag = cfg.get("onscreen")
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
            return None
        if skip_invis is not None and skip_invis.search(path.rsplit("|", 1)[-1]) \
                and (resp.get("result") or {}).get("visible") is False:
            continue
        for cid in _child_ids(resp):
            cpath = path + "|" + cid
            if descend is not None and descend.search(cid):
                if depth < max_depth:
                    stack.append((cpath, depth + 1))
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
                        "text": res.get("text"), "text_label": res.get("text_label"),
                        "x": res.get("x"), "y": res.get("y"),
                        "w": res.get("w"), "h": res.get("h")}
                if onscreen_flag:
                    tt = res.get("tooltip")
                    onscreen = _tooltip_title(tt)
                    orow["tooltip"] = tt
                    orow["onscreen"] = onscreen
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
    """Bounded key-embedded recursion from ONE card-list container, tagging leaves with `source`; False if the bus went away."""
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
    """Recruitment SOURCE-AWARE enumeration: every recruit card with its pool `source`, or None when the bus did not answer."""
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
            gresp = _find(bus, list_path)
            budget[0] -= 1
            if gresp is None:
                return None
            suffix = src.get("group_suffix", "unit_list|listview|list_clip|list_box")
            for g in _child_ids(gresp):
                if "template" in g.lower():
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
    """Enumerate cards whose id carries no game key, left->right by x; None when the bus did not answer."""
    container = cfg["card_container"]
    state_sub = cfg.get("state_sub")
    label_sub = cfg.get("label_sub")
    value_subs = cfg.get("value_subs") or {}
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
        if not res.get("found") or res.get("x") is None:
            continue
        card = {"id": cid, "state": res.get("state"), "visible": res.get("visible"),
                "text": res.get("text"), "text_label": res.get("text_label"),
                "desc": res.get("tooltip"),
                "x": res.get("x"), "y": res.get("y"),
                "w": res.get("w"), "h": res.get("h"), "name": None, "values": {}}
        if label_sub:
            lr = _find(bus, container + "|" + cid + "|" + label_sub)
            if lr is None:
                return None
            lres = lr.get("result") or {}
            card["name"] = (lres.get("text") or lres.get("text_label")) or None
        for vk, vnode in value_subs.items():
            vr = _find(bus, container + "|" + cid + "|" + vnode)
            if vr is None:
                return None
            card["values"][vk] = (vr.get("result") or {}).get("text")
        cards.append(card)
    cards.sort(key=lambda d: d["x"])
    if label_sub:
        for c in cards:
            c["label"] = c["name"] or c["id"]
            c["positional"] = c["name"] is None
    else:
        if labels and len(cards) == len(labels):
            for c, lbl in zip(cards, labels):
                c["label"] = lbl
        for i, c in enumerate(cards):
            c.setdefault("label", "option%d" % i)
            c["positional"] = True
    return [{"id": c["id"], "key": c["id"], "label": c["label"], "name": c.get("name"),
             "desc": c.get("desc"),
             "clickable": _clickable(c["state"], c["visible"], allow),
             "state": c["state"], "visible": c["visible"],
             "x": c["x"], "y": c.get("y"), "w": c.get("w"), "h": c.get("h"),
             "values": c.get("values") or {}, "positional": c["positional"]} for c in cards]


def enumerate_context_cards(bus: Bus, cfg: dict) -> list | None:
    """CONTEXT-CARD enumeration: keyless cards identified by their bound cfg["cco_prefix"] context object; None when the bus did not answer."""
    container = cfg["card_container"]
    card_re = cfg["_card"]
    prefix = cfg["cco_prefix"]
    recruit_re = cfg.get("_recruit")
    cont_prefix = cfg.get("container_context")
    context_sub = cfg.get("context_sub")
    resp = _find(bus, container)
    if resp is None:
        return None
    cont_key = _context_key((resp.get("result") or {}).get("context"), cont_prefix) if cont_prefix else None
    out = []
    for cid in _child_ids(resp):
        if not card_re.search(cid):
            continue
        cr = _find(bus, container + "|" + cid)
        if cr is None:
            return None
        res = cr.get("result") or {}
        key = None
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
            for ctx in _child_contexts(cr):
                k = _context_key(ctx, prefix)
                if k is not None:
                    key = k
                    break
        if key is None:
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
        if cfg.get("name_node"):
            onscreen, category = _named_from_nodes(bus, container + "|" + cid, cfg)
            card["onscreen"] = onscreen
            card["name"] = onscreen
            card["label"] = onscreen
            if category is not None:
                card["category"] = category
        if cont_prefix is not None:
            card["char"] = cont_key
        out.append(card)
    return out


def _clickable_by_state(state, allow=None) -> bool | None:
    """Clickability from CurrentState ALONE -- for collapsible stacks, whose collapsed options report Visible=False while available."""
    if state is None:
        return None
    st = str(state).lower()
    if allow is not None:
        return st in allow
    return st not in _DISABLED_STATES


def _strip_prefixes(cid: str, prefixes) -> str:
    """The option KEY: `cid` with the first matching prefix stripped (list them most-specific first)."""
    for pre in prefixes:
        if cid.startswith(pre):
            return cid[len(pre):]
    return cid


def enumerate_children(bus: Bus, cfg: dict) -> list | None:
    """CHILD-COUNT enumeration of each cfg["roots"] entry's direct children (not visibility-gated); None when the bus did not answer."""
    key_prefixes = cfg.get("key_prefixes", [])
    allow = cfg.get("clickable_states")
    optre = cfg.get("_opt")
    state_at = cfg.get("state_at")
    out = []
    for spec in cfg["roots"]:
        root = spec["path"]
        source = spec.get("source")
        resp = _find(bus, root)
        if resp is None:
            return None
        for cid in _child_ids(resp):
            if optre is not None and not optre.search(cid):
                continue
            cr = _find(bus, root + "|" + cid)
            if cr is None:
                return None
            res = cr.get("result") or {}
            state, vis = res.get("state"), res.get("visible")
            if state_at:
                sr = _find(bus, root + "|" + cid + "|" + state_at)
                if sr is None:
                    return None
                sres = sr.get("result") or {}
                if sres.get("found"):
                    state = sres.get("state")
                    if sres.get("visible") is not None:
                        vis = sres.get("visible")
            opt = {"id": cid, "key": _strip_prefixes(cid, key_prefixes),
                   "clickable": _clickable_by_state(state, allow),
                   "state": state, "visible": vis,
                   "context": res.get("context"),
                   "text": res.get("text"), "text_label": res.get("text_label"),
                   "x": res.get("x"), "y": res.get("y"),
                   "w": res.get("w"), "h": res.get("h")}
            if cfg.get("name_node"):
                onscreen, category = _named_from_nodes(bus, root + "|" + cid, cfg)
                opt["onscreen"] = onscreen
                opt["name"] = onscreen
                opt["label"] = onscreen
                if category is not None:
                    opt["category"] = category
            if source is not None:
                opt["source"] = source
            out.append(opt)
    return out


def _read_scalars(bus: Bus, specs: list) -> dict:
    """Best-effort SCALAR reads: one find per spec, returning {name: GetStateText | state | None}."""
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
    """The id-suffix key of the first `prefix` child of a list whose state starts with `selected*`, else None."""
    root = spec["root"]
    prefix = spec["prefix"]
    sstate = spec.get("selected_state_prefix", "selected")
    resp = _find(bus, root)
    if resp is None:
        return None
    for cid in _child_ids(resp):
        if not cid.startswith(prefix):
            continue
        cr = _find(bus, root + "|" + cid)
        st = ((cr or {}).get("result") or {}).get("state")
        if isinstance(st, str) and st.startswith(sstate):
            return cid[len(prefix):]
    return None


def panel_open(bus: Bus, cfg: dict) -> bool | None:
    """Cheap check: is this panel currently OPEN? True/False, or None when the bus did not answer."""
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
    """Enumerate one panel into a {"kind":"menu_open","panel","n","options"[,"reason"][,"deal"]} row, or None on a bus miss."""
    cfg = PANELS[name]
    if cfg.get("positional"):
        opts = enumerate_positional(bus, cfg)
    elif cfg.get("context"):
        opts = enumerate_context_cards(bus, cfg)
    elif cfg.get("children_of"):
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
    scalars = cfg.get("scalars")
    sel = cfg.get("selected_row")
    if scalars or sel:
        deal = _read_scalars(bus, scalars) if scalars else {}
        if sel:
            deal[sel["name"]] = _selected_child_key(bus, sel)
        row["deal"] = deal
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
    atc = cfg.get("active_tab_container")
    if atc:
        row["tab"] = _selected_child_key(bus, {"root": atc, "prefix": "CcoTechnologyUiTabRecord",
                                               "selected_state_prefix": "selected"})
    return row


def scan_open(bus: Bus, panels: dict | None = None, exclude=None) -> tuple[list, bool]:
    """Enumerate every configured panel that is currently OPEN and populated; returns (rows, bus_ok)."""
    panels = panels or PANELS
    exclude = set(exclude or ())
    rows = []
    bus_ok = False
    for name in panels:
        if name in exclude:
            continue
        try:
            op = panel_open(bus, panels[name])
        except Exception as e:
            sys.stderr.write("ui-capture: panel_open(%s) skipped -> %s\n" % (name, repr(e)[:80]))
            continue
        if op is None:
            continue
        bus_ok = True
        if not op:
            continue
        try:
            row = capture_panel(bus, name)
        except Exception as e:
            sys.stderr.write("ui-capture: capture_panel(%s) skipped -> %s\n" % (name, repr(e)[:80]))
            continue
        if row is None:
            continue
        if row["n"] > 0 or row.get("deal"):
            rows.append(row)
    return rows, bus_ok


def _newest_script_log() -> str | None:
    """Path of the newest script_log*.txt WH3 is writing (game dir or AppData logs), or None."""
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
                        continue
                    if m > best_m:
                        best, best_m = p, m
        except OSError:
            continue
    return best


def _panel_opened(chunk: str) -> bool:
    """True if a log chunk contains a real PanelOpenedCampaign occurrence, excluding trigger-registration lines."""
    if "PanelOpenedCampaign" not in chunk:
        return False
    if '"event":"PanelOpenedCampaign"' in chunk:
        return True
    for ln in chunk.splitlines():
        if "PanelOpenedCampaign" in ln and "added trigger condition" not in ln:
            return True
    return False


def _opened_components(chunk: str) -> list:
    """Component ids of every real PanelOpenedCampaign occurrence in a fresh log chunk, in order."""
    if "PanelOpenedCampaign" not in chunk:
        return []
    return _PANEL_OPEN_RE.findall(chunk)


def _tech_tab_switched(chunk: str) -> bool:
    """True if a fresh log chunk contains a technology-panel TAB component event."""
    return "CcoTechnologyUiTabRecord" in chunk and bool(_TECH_TAB_RE.search(chunk))


def watch(bus: Bus, emit, panels: dict | None = None, is_running=lambda: True) -> None:
    """Tail the newest script_log for PanelOpenedCampaign and emit each opened panel's options, plus a low-rate poll of POLL_PANELS."""
    panels = panels or PANELS
    bus_ok = None
    logpath, fh, off = None, None, 0
    last_emit = {}
    prev_open = {name: False for name in panels}
    last_poll = 0.0
    poll_next = {}
    poll_miss = {}
    last_beat = 0.0
    seen_unmapped = set()
    poll_names = [n for n in POLL_PANELS if n in panels]
    event_names = [n for n in panels if n not in POLL_PANELS]
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
        open_names = {r["panel"] for r in rows}
        now = time.time()
        for r in rows:
            name = r["panel"]
            fresh = (not prev_open.get(name)) or (now - last_emit.get(name, 0) > REEMIT_COOLDOWN)
            if fresh:
                emit(r)
                last_emit[name] = now
        for name in checked_names:
            prev_open[name] = name in open_names

    def _recapture_technology():
        if "technology" not in panels:
            return False
        try:
            row = capture_panel(bus, "technology")
        except Exception as e:
            sys.stderr.write("ui-capture: capture_panel(technology) skipped -> %s\n" % repr(e)[:80])
            return False
        if row is None:
            _report_bus(False)
            return False
        _report_bus(True)
        if row["n"] <= 0:
            return False
        keys = frozenset(o.get("key") for o in row["options"])
        if keys and keys != last_tech["keys"]:
            emit(row)
            last_emit["technology"] = time.time()
            prev_open["technology"] = True
            last_tech["keys"] = keys
            return True
        return False

    while is_running():
        newest = _newest_script_log()
        if newest and newest != logpath:
            try:
                if fh:
                    fh.close()
                fh = open(newest, "rb")
                fh.seek(0, os.SEEK_END)
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
            if size < off:
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

        slept = WATCH_POLL
        ok = None
        chunk_text = chunk.decode("utf-8", "replace") if chunk else ""
        if chunk:
            targets = []
            for c in _opened_components(chunk_text):
                mapped = COMPONENT_PANELS.get(c)
                if not mapped:
                    if c not in seen_unmapped:
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
                    except Exception as e:
                        sys.stderr.write("ui-capture: capture_panel(%s) skipped -> %s\n" % (pname, repr(e)[:80]))
                        continue
                    if row is None:
                        continue
                    ok = True
                    if row["n"] > 0 or row.get("deal"):
                        rows.append(row)
                _report_bus(bool(ok))
                if not ok:
                    slept = BACKOFF_INTERVAL
                _emit_scan(rows, targets)
                for r in rows:
                    if r["panel"] == "technology":
                        last_tech["keys"] = frozenset(o.get("key") for o in r["options"])

            if _tech_tab_switched(chunk_text):
                if not _recapture_technology():
                    last_tech["retry_at"] = time.time() + 0.8

        if last_tech["retry_at"] and time.time() >= last_tech["retry_at"]:
            last_tech["retry_at"] = 0.0
            if bus_ok is not False:
                _recapture_technology()

        now = time.time()
        if poll_names and bus_ok is not False and now - last_poll >= POLL_INTERVAL:
            last_poll = now
            due = [n for n in poll_names if now >= poll_next.get(n, 0.0)]
            prows, pok = [], False
            for n in due:
                try:
                    op = panel_open(bus, panels[n])
                except Exception as e:
                    sys.stderr.write("ui-capture: panel_open(%s) skipped -> %s\n" % (n, repr(e)[:80]))
                    continue
                if op is None:
                    poll_miss[n] = poll_miss.get(n, 0) + 1
                    poll_next[n] = now + min(30.0, POLL_INTERVAL * 2 ** min(poll_miss[n], 5))
                    continue
                pok = True
                poll_miss[n] = 0
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

        if now - last_beat >= 30.0:
            last_beat = now
            emit({"kind": "ui_status", "status": "alive"})

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
