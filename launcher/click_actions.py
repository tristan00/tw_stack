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


def engine_click(bus, component_id):
    """Click a component ENGINE-SIDE by its id -- no OS cursor, so it can never steal the mouse.

    `CcoComponent.SimulateLClick` is the only Simulate* in the whole CCO system and CA drive their
    own UI with it (verbatim, dlc24_matters_of_state.twui.xml:8127:
    context_function_id="Component(&quot;FC4F69A8-...&quot;).SimulateLClick"). `RootComponent` needs
    no ExpressionState and `ChildContext` searches recursively, so it resolves runtime-spawned rows
    like general_candidate_<n>_ that appear in no XML.

    This replaces the hardware clicks that used to drive lord recruitment. It still proves nothing
    on its own -- the caller must post-assert, exactly as with the bus click.
    """
    lua = ('common.call_context_command([[RootComponent.ChildContext("%s").SimulateLClick]]) '
           'return "sent"' % component_id)
    try:
        return _ev(bus, lua, timeout=20.0) == "sent"
    except Exception as e:
        sys.stderr.write("click_actions: engine_click %s -> %s" % (component_id, repr(e)[:80]) + chr(10))
        return False


def _click_or_hw(bus, path):
    """Try the (cursor-safe) bus click; fall back to a hardware click on the rect."""
    res, _ = _find(bus, path)
    if res.get("visible") is True and _click(bus, path):
        return True
    return _hw_click(bus, path)


# ==== KNOWN STATE BEFORE EVERY CLICK =======================================================
# THE RULE, applied by EVERY click executor without exception: never click into an unknown screen.
# Put the game where we want it first -- clear stray popups, point the CAMERA at the subject, SELECT
# the subject, and verify the panel we expect actually opened. Only then click.
#
# Why this is not optional. HUD buttons act on the CURRENT SELECTION and panels are per-selection,
# so clicking with the wrong thing selected is a silent no-op at best and the WRONG ACTION at worst
# -- and SimulateLClick returns clicked:True either way, so nothing downstream notices.
#
# Live proof of the cost: recruit_lord clicked `button_create_army` with NOTHING selected. It
# reported found=True, clicked=True and opened nothing, so the lord-type list and candidate rows
# never existed and the action failed 10 times out of 10 in real runs. We misdiagnosed that as
# "invisible components" and bolted on hardware clicks that stole the user's cursor. With the
# settlement selected first: settlement_panel -> character_panel -> 4 lord types -> 70 candidate
# rows, several reporting visible=True. The panel was simply never open.
#
# Focus + selection are cco Void commands and the popup drain is a bus click, so establishing the
# known state costs no OS cursor movement.


def clear_screen(bus):
    """Close whatever the game was left looking at: open PANELS first, then leftover popups.

    ⚠ Popup draining alone is not enough. A panel (character_panel, settlement_panel, units_panel)
    has no dismiss button, so nav.close_popups returns 0 and the panel stays up -- and a stale panel
    BLOCKS the next selection from opening its own panel. Live: character_panel left open from a
    previous attempt made select_settlement succeed while settlement_panel never appeared, so the
    whole recruit chain refused.
    `CloseAllPanels` is a verified CA global context command (used in campaign_tours.lua:3097),
    click-free and cursor-free.
    """
    import nav
    try:
        _ev(bus, 'common.call_context_command([[CloseAllPanels]]) return "sent"', timeout=15.0)
        time.sleep(1.0)
    except Exception as e:
        sys.stderr.write("click_actions: CloseAllPanels -> %s" % repr(e)[:80] + chr(10))
    try:
        return len(nav.close_popups(bus))
    except Exception as e:
        sys.stderr.write("click_actions: close_popups -> %s" % repr(e)[:80] + chr(10))
        return 0


def select_settlement(bus, region):
    r = _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s') if not s then return 'NO-SETT' end "
                      "local ok,e=pcall(function() s:Call('Select') end) return 'ok='..tostring(ok)" % region,
            timeout=20.0)
    time.sleep(1.5)
    return r == "ok=true"


def select_character(bus, cqi):
    r = _ev(bus, _G + "local c=cco('CcoCampaignCharacter','%s') if not c then return 'NO-CHAR' end "
                      "local ok,e=pcall(function() c:Call('Select') end) return 'ok='..tostring(ok)" % cqi,
            timeout=20.0)
    time.sleep(1.2)
    return r == "ok=true"


def focus(bus, kind, entity_id):
    """Point the CAMERA at the subject. Not cosmetic: it discards whatever view the game was left
    on, so the click lands against a screen we put there rather than whatever preceded it."""
    import nav
    try:
        r = (nav.focus_char(bus, int(entity_id)) if kind == "lord"
             else nav.focus_settlement(bus, entity_id))
        time.sleep(1.0)
        return bool(r)
    except Exception as e:
        sys.stderr.write("click_actions: focus %s %s -> %s" % (kind, entity_id, repr(e)[:70]) + chr(10))
        return False


def prepare(bus, kind, entity_id, expect_root=None, timeout=6.0):
    """clear popups -> camera on subject -> select subject -> verify expected panel.

    kind: "settlement" | "lord". Returns (ok, reason). LOUD by design: a click issued from an
    unverified screen is the exact failure this exists to prevent, so callers must refuse to click
    when this returns False rather than trying anyway.
    """
    import nav
    clear_screen(bus)
    focus(bus, kind, entity_id)
    if kind == "settlement":
        ok = select_settlement(bus, entity_id)
    elif kind == "lord":
        ok = select_character(bus, entity_id)
    else:
        return False, "unknown_subject_kind_%s" % kind
    if not ok:
        return False, "could_not_select_%s_%s" % (kind, entity_id)
    if expect_root:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if expect_root in (nav.visible_roots(bus) or []):
                    return True, "ready"
            except Exception:
                pass
            time.sleep(0.5)
        return False, "expected_panel_%s_never_opened" % expect_root
    return True, "ready"


def _treasury(bus):
    return _ev(bus, "return cm:get_faction(cm:get_local_faction_name(true)):treasury()", timeout=8.0)


def _roots(bus):
    import nav
    try:
        return nav.visible_roots(bus)
    except Exception:
        return None


# ----------------------------------------------------------------- STANCE LEGALITY WHITELIST
# READ-ONLY enumerator (no clicking) -- the game's own answer to "which stances may this faction
# use", which the cco StanceList does NOT give: StanceList returns EVERY stance in the game and
# Activate will happily set a faction-ILLEGAL one (verified rule breach: a High Elf army entered
# TUNNELING). The HUD stance stack only ever contains the legal ones, so it is the whitelist.
#
# ⚠ the stack is COLLAPSED most of the time, so only the current button reports visible:True. The
# bus `find` handler enumerates direct children via ChildCount+Find(i), which is NOT visibility
# gated, so it returns all of them anyway (verified: 13 buttons off a collapsed, invisible stack).
STANCE_STACK = "hud_campaign|BL_parent|land_stance_button_stack|clip_parent|stack_background"
_STANCE_PREFIX = "button_"


def stance_options(bus):
    """{stance_key: state} for every stance this faction may use. Empty dict = the stack could not
    be read (LOUD: callers must treat that as "no stance offers", never as "no legal stances")."""
    _res, kids = _find(bus, STANCE_STACK, timeout=12.0)
    out = {}
    for k in kids:
        if not k.startswith(_STANCE_PREFIX) or k == "button_default":
            continue
        key = k[len(_STANCE_PREFIX):]
        res, _ = _find(bus, "%s|%s" % (STANCE_STACK, k), timeout=8.0)
        out[key] = res.get("state")
    if not out:
        sys.stderr.write("click_actions: stance stack %s enumerated 0 buttons\n" % STANCE_STACK)
    return out


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
    prepare(bus, "settlement", region)      # the HUD stack belongs to the SELECTED province
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
    ok, why = prepare(bus, "settlement", ctx["entity_id"])
    if not ok:
        sys.stderr.write("click_actions: edict refused, not a known state -> %s" % why + chr(10))
        return False
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
    prepare(bus, "lord", ctx["entity_id"], expect_root="units_panel")
    r = _roots(bus)
    return {"treasury": _treasury(bus), "pending": _pending_recruits(bus, ctx["entity_id"]),
            "units_panel_open": (r is not None and "units_panel" in r)}


def _recruit_gate(bus, ctx, pick, before):
    # CTD GUARD -- never toggle recruitment without units_panel open (engine crash 0xc0000409)
    if not before.get("units_panel_open"):
        return False, "units_panel_not_open_CTD_guard"
    return True, None


def _recruit_execute(bus, ctx, pick, before):
    ok, why = prepare(bus, "lord", ctx["entity_id"], expect_root="units_panel")
    if not ok:
        sys.stderr.write("click_actions: recruit_unit refused, not a known state -> %s" % why + chr(10))
        return False
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
    ok, why = prepare(bus, "settlement", ctx["entity_id"], expect_root="settlement_panel")
    if not ok:
        sys.stderr.write("click_actions: recruit_lord refused, not a known state -> %s" % why + chr(10))
        return False
    r = _roots(bus)
    if not (r and "character_panel" in r):       # idempotent: the button TOGGLES the panel
        if not _click(bus, CREATE_ARMY_BTN):     # opens character_panel (raise-army)
            return False
        time.sleep(1.8)
    # ⚠ ID MISMATCH: the offer key is the DB subtype (wh2_main_hef_prince) but the UI type button
    # is the short form (hef_prince). Resolve against what the panel actually lists rather than
    # assuming either shape -- prefixes differ per pack (wh2_main_, wh2_dlc10_, wh3_dlc27_...).
    want = str(pick["key"])
    types = lord_types(bus)
    btn = next((t for t in types if want.endswith(t) or t.endswith(want) or t in want), None)
    if btn is None:
        sys.stderr.write("click_actions: lord type for %r not among %s" % (want, types) + chr(10))
        return False
    if not _click(bus, "%s|%s" % (LORD_TYPE_LIST, btn)):
        return False
    time.sleep(1.5)
    cands = lord_candidates(bus)
    if not cands:
        sys.stderr.write("click_actions: no general_candidate rows for %r\n" % pick["key"])
        return False
    idx = int((pick.get("params") or {}).get("candidate_index", 0))
    # ENGINE-SIDE click by component id. These rows are spawned at runtime (they exist in no XML)
    # and often report visible:False, which is why the path-based bus click was a silent no-op and
    # we used to fall back to a hardware click that stole the cursor.
    if not engine_click(bus, cands[min(idx, len(cands) - 1)]):
        return False
    # button_raise is 'down_off' (disabled) until a candidate is selected -- that flip is the
    # in-flight proof the selection landed, checked before committing.
    res, _ = _find(bus, BUTTON_RAISE)
    if res.get("state") != "active":
        sys.stderr.write("click_actions: button_raise not active after candidate select (state=%s)\n"
                         % res.get("state"))
        return False
    return engine_click(bus, "button_raise")     # commits, engine-side


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
