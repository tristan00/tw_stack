r"""click_actions.py -- v7 executors driven through UI COMPONENTS.

Used ONLY where neither the CCO command layer nor the cm script layer exposes the action
(verified by full CCO enumeration + CA script-API research). Everything here is driven by the
bus `click` channel (SimulateLClick BY COMPONENT PATH) -- no screen coordinates, no synthetic
mouse, so it cannot steal the user's cursor and does not depend on camera/resolution.

⚠ SimulateLClick LIES IN TWO WAYS, both learned the hard way:
  1. `clicked: True` is returned even when nothing happens -> every action post-asserts.
  2. A FEW components ignore SimulateLClick entirely (verified no-ops: the diplomacy
     faction_row_entry_* rows, and the end-turn button). Those need a hardware click -- which is
     why nothing here depends on them (end turn is done with the CCO EndTurn command instead).

LIVE-VERIFIED 2026-07-30:
  edict  -- click hud_campaign|BL_parent|stack_incentives|button_<edict_key>
            -> FactionProvinceManagerContext.SelectedInitiative.Key becomes that key.
            Works while the stack is COLLAPSED (the button resolves by path even when only the
            currently-selected button + stack_arrow are enumerable). No hover/expand needed.
            NOTE: CanSetInitiative means "the SELECTED initiative can be set" -- it is NOT a
            province-completeness gate (it reads false simply when nothing is selected).
            NOTE: selection != activation. get_active_edict_key() stays empty until the turn ends;
            get_selected_edict_key() mirrors the selection immediately.

  recruit_unit -- path proven in the v6 loop (treasury drop confirmed live).
            ⚠ CTD GUARD: clicking button_recruitment with NO army selected can crash the engine
            (0xc0000409). Only ever click it with units_panel confirmed open.

  recruit_lord -- full path captured from a live human demo (game's own [ui] click trace):
            button_create_army -> lord_parent|list_box|<lord_type> -> general_candidate_<n>_
            -> footer|button_raise. Confirmation cascade observed:
            UnitCreated -> CharacterCreated(char_cqi) -> MilitaryForceCreated -> CharacterRecruited,
            plus treasury 1764 -> 914 and a new cqi in the faction character list.
            (Execution not yet self-run: awaiting a fresh campaign; the paths are recorded here so
            the knowledge is not lost.)
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

from cco_actions import _G, _ev, register       # noqa: E402

# ---- component paths (all captured live) -------------------------------------------------
EDICT_STACK = "hud_campaign|BL_parent|stack_incentives"
RECRUIT_BTN = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
               "button_subpanel|button_group_army|button_recruitment")
CREATE_ARMY_BTN = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
                   "button_subpanel|button_group_settlement|button_create_army")
CHAR_PANEL = "character_panel|character_panel_info_holder"
LORD_TYPE_LIST = CHAR_PANEL + "|lords_and_agents_holder|lord_parent|list_box"
CANDIDATE_LIST = (CHAR_PANEL + "|general_selection_panel|main_holder|character_list_parent|"
                  "character_list|listview|list_clip|list_box")
BUTTON_RAISE = CHAR_PANEL + "|footer|button_raise"


def _click(bus, path, timeout=10.0):
    """SimulateLClick by path. Returns True only if the mod found AND clicked the component --
    which still proves nothing about the game state (see module docstring)."""
    try:
        r = bus.send("click", path, timeout=timeout) or {}
    except Exception as e:
        sys.stderr.write("click_actions: click %s -> %s\n" % (path.rsplit("|", 1)[-1], repr(e)[:70]))
        return False
    if not r.get("clicked"):
        sys.stderr.write("click_actions: click NOT registered: %s (found=%s)\n"
                         % (path.rsplit("|", 1)[-1], r.get("found")))
    return bool(r.get("clicked"))


def _find(bus, path, timeout=8.0):
    try:
        r = bus.send("find", path, timeout=timeout) or {}
        return (r.get("result") or {}), (r.get("child_ids") or [])
    except Exception:
        return {}, []


def _hw_click(bus, path, settle=1.2):
    """HARDWARE click on a component's rect. Needed for components the bus refuses to
    SimulateLClick -- notably ones reporting visible:False (verified: the raise-army
    `general_candidate_<n>_` rows, which are found+active but invisible, so the mod's click
    is a silent no-op while still returning clicked:True)."""
    import nav
    res, _ = _find(bus, path)
    if res.get("x") is None:
        sys.stderr.write("click_actions: no rect for %s\n" % path.rsplit("|", 1)[-1])
        return False
    sx, sy = nav.ui_to_screen(res["x"] + (res.get("w") or 0) / 2.0,
                              res["y"] + (res.get("h") or 0) / 2.0)
    nav.mouse("move", sx, sy)
    time.sleep(0.3)
    nav.mouse("click", sx, sy)
    time.sleep(settle)
    return True


def _click_or_hw(bus, path):
    """Try the (cursor-safe) bus click; fall back to a hardware click on the rect."""
    res, _ = _find(bus, path)
    if res.get("visible") is True and _click(bus, path):
        return True
    return _hw_click(bus, path)


def _treasury(bus):
    return _ev(bus, "return cm:get_faction(cm:get_local_faction_name(true)):treasury()", timeout=8.0)


def _roots(bus):
    import nav
    try:
        return nav.visible_roots(bus)
    except Exception:
        return None


# ------------------------------------------------------------------------------- EDICT
def _selected_edict(bus, region):
    return _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s');"
                         "local m=g(s,'FactionProvinceManagerContext'); if not m then return 'NO-MGR' end "
                         "local i=g(m,'SelectedInitiative'); if i then return ts(g(i,'Key')) end "
                         "return 'none'" % region, timeout=12.0)


def edict_options(bus, region):
    """The province's edict keys (records) -- the option set for this settlement's province."""
    raw = _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s');"
                        "local m=g(s,'FactionProvinceManagerContext'); if not m then return '' end "
                        "local l=g(m,'InitiativeList'); if type(l)~='table' then return '' end local o={} "
                        "for i=1,#l do o[#o+1]=ts(g(l[i],'Key')) end return table.concat(o,',')"
             % region, timeout=20.0)
    return [k for k in str(raw or "").split(",") if k and k != "nil"]


def _edict_snapshot(bus, ctx, pick):
    region = ctx["entity_id"]
    return {"selected": _selected_edict(bus, region), "options": edict_options(bus, region)}


def _edict_gate(bus, ctx, pick, before):
    if pick["key"] not in (before.get("options") or []):
        return False, "edict_not_available_in_province"
    if before.get("selected") == pick["key"]:
        return False, "already_selected"
    return True, None


def _edict_execute(bus, ctx, pick, before):
    """Click the edict button EXACTLY ONCE.

    ⚠ DO NOT re-click the button and DO NOT ESC/close the stack afterwards: either CANCELS the
    queued commandment (this is what made an earlier attempt appear to select and then revert).
    The button lives either promoted as a direct child of the stack, or inside
    clip_parent|stack_background; the promoted id rotates as a preview, so resolve before clicking.
    """
    key = "button_%s" % pick["key"]
    for path in ("%s|%s" % (EDICT_STACK, key),
                 "%s|clip_parent|stack_background|%s" % (EDICT_STACK, key)):
        res, _ = _find(bus, path)
        if res.get("found"):
            return _click(bus, path)
    sys.stderr.write("click_actions: edict button %s not resolvable in the stack\n" % key)
    return False


def _edict_confirm(bus, ctx, pick, before):
    sel = _selected_edict(bus, ctx["entity_id"])
    return (sel == pick["key"]), {"selected": sel}


register("edict", {
    "layer": "click", "signal": "selected_initiative_key",
    "snapshot": _edict_snapshot, "gates": [_edict_gate],
    "execute": _edict_execute, "confirm": _edict_confirm,
    "timeout_s": 6.0, "poll_s": 1.2, "max_per_entity_turn": 1,
})


# ------------------------------------------------------------------------- RECRUIT UNIT
def _pending_recruits(bus, cqi):
    return _ev(bus, _G + "local c=cco('CcoCampaignCharacter','%s'); local mf=g(c,'MilitaryForceContext');"
                         "if not mf then return -1 end local p=g(mf,'PendingRecruitmentUnitList');"
                         "if type(p)=='table' then return #p end return -1" % cqi, timeout=12.0)


def recruitable_units(bus):
    """`<unit_key>_recruitable` cards currently in units_panel (requires the panel open)."""
    try:
        tr = bus.send("tree", "units_panel 30 9000", timeout=20) or {}
    except Exception:
        return []
    out = []
    for n in tr.get("nodes") or []:
        i = str(n.get("id") or "")
        if i.endswith("_recruitable") and n.get("visible"):
            out.append({"key": i[:-len("_recruitable")], "state": n.get("state"), "path": n.get("path")})
    return out


def _recruit_snapshot(bus, ctx, pick):
    r = _roots(bus)
    return {"treasury": _treasury(bus), "pending": _pending_recruits(bus, ctx["entity_id"]),
            "units_panel_open": (r is not None and "units_panel" in r)}


def _recruit_gate(bus, ctx, pick, before):
    # CTD GUARD -- never toggle recruitment without units_panel open (engine crash 0xc0000409)
    if not before.get("units_panel_open"):
        return False, "units_panel_not_open_CTD_guard"
    return True, None


def _recruit_execute(bus, ctx, pick, before):
    cards = recruitable_units(bus)
    if not cards:                                # idempotent: the button TOGGLES the sub-panel,
        if not _click(bus, RECRUIT_BTN):         # so only open it when no cards are showing
            return False
        time.sleep(1.4)
        cards = recruitable_units(bus)
    card = next((c for c in cards if c["key"] == pick["key"]), None)
    if card is None:
        sys.stderr.write("click_actions: unit %r not among recruitable cards\n" % pick["key"])
        return False
    return _click(bus, card["path"])


def _recruit_confirm(bus, ctx, pick, before):
    t, p = _treasury(bus), _pending_recruits(bus, ctx["entity_id"])
    dropped = (t is not None and before.get("treasury") is not None and t < before["treasury"])
    queued = (isinstance(p, (int, float)) and isinstance(before.get("pending"), (int, float))
              and p > before["pending"])
    return (dropped or queued), {"treasury": t, "pending": p}


register("recruit_unit", {
    "layer": "click", "signal": "treasury_drop_or_pending_increase",
    "snapshot": _recruit_snapshot, "gates": [_recruit_gate],
    "execute": _recruit_execute, "confirm": _recruit_confirm,
    "timeout_s": 8.0, "poll_s": 1.5, "spends_gold": True, "max_per_entity_turn": 4,
})


# ------------------------------------------------------------------------- RECRUIT LORD
def _character_cqis(bus):
    raw = _ev(bus, "local f=cm:get_local_faction(true); local cl=f:character_list(); local o={} "
                   "for i=0,cl:num_items()-1 do o[#o+1]=cl:item_at(i):command_queue_index() end "
                   "return table.concat(o,',')", timeout=12.0)
    return set(str(raw or "").split(","))


def lord_types(bus):
    """Lord-type buttons in the raise-army panel (e.g. hef_prince / hef_princess / hef_sea_helm)."""
    _res, kids = _find(bus, LORD_TYPE_LIST)
    return [k for k in kids if not k.startswith("button_template")]


def lord_candidates(bus):
    """`general_candidate_<n>_` rows for the selected lord type."""
    _res, kids = _find(bus, CANDIDATE_LIST)
    return [k for k in kids if k.startswith("general_candidate")]


def _lord_snapshot(bus, ctx, pick):
    return {"treasury": _treasury(bus), "chars": sorted(_character_cqis(bus))}


def _lord_execute(bus, ctx, pick, before):
    r = _roots(bus)
    if not (r and "character_panel" in r):       # idempotent: the button TOGGLES the panel
        if not _click(bus, CREATE_ARMY_BTN):     # opens character_panel (raise-army)
            return False
        time.sleep(1.8)
    if not _click(bus, "%s|%s" % (LORD_TYPE_LIST, pick["key"])):   # lord TYPE, e.g. hef_prince
        return False
    time.sleep(1.5)
    cands = lord_candidates(bus)
    if not cands:
        sys.stderr.write("click_actions: no general_candidate rows for %r\n" % pick["key"])
        return False
    idx = int((pick.get("params") or {}).get("candidate_index", 0))
    # candidate rows report visible:False -> the bus click is a silent no-op; hardware-click the rect
    if not _hw_click(bus, "%s|%s" % (CANDIDATE_LIST, cands[min(idx, len(cands) - 1)])):
        return False
    # button_raise is 'down_off' (disabled) until a candidate is selected -- that flip is the
    # in-flight proof the selection landed, checked before committing.
    res, _ = _find(bus, BUTTON_RAISE)
    if res.get("state") != "active":
        sys.stderr.write("click_actions: button_raise not active after candidate select (state=%s)\n"
                         % res.get("state"))
        return False
    return _click_or_hw(bus, BUTTON_RAISE)       # commits


def _lord_confirm(bus, ctx, pick, before):
    now = _character_cqis(bus)
    new = sorted(now - set(before.get("chars") or []))
    t = _treasury(bus)
    dropped = (t is not None and before.get("treasury") is not None and t < before["treasury"])
    return (bool(new) and dropped), {"new_cqis": new, "treasury": t}


register("recruit_lord", {
    "layer": "click", "signal": "new_character_cqi_and_treasury_drop",
    "snapshot": _lord_snapshot,
    "execute": _lord_execute, "confirm": _lord_confirm,
    "timeout_s": 12.0, "poll_s": 2.0, "spends_gold": True, "max_per_entity_turn": 1,
})
