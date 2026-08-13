from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)

from cco_actions import _G, _ev, register

RECRUIT_BTN = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
               "button_subpanel|button_group_army|button_recruitment")
CREATE_ARMY_BTN = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
                   "button_subpanel|button_group_settlement|button_create_army")
AGENTS_BTN = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
              "button_subpanel|button_group_settlement|button_agents")
CHAR_PANEL = "character_panel|character_panel_info_holder"
PANEL_TITLE = CHAR_PANEL + "|header|title_plaque|tx_recruit"
LORD_MODE = "recruit_general"
AGENT_MODE = "recruit_agent"
LORD_TYPE_LIST = CHAR_PANEL + "|lords_and_agents_holder|lord_parent|list_box"
AGENT_TYPE_LIST = CHAR_PANEL + "|lords_and_agents_holder|agent_parent|list_box"
CANDIDATE_LIST = (CHAR_PANEL + "|general_selection_panel|main_holder|character_list_parent|"
                  "character_list|listview|list_clip|list_box")
BUTTON_RAISE = CHAR_PANEL + "|footer|button_raise"
LORE_LIST = (CHAR_PANEL + "|general_selection_panel|main_holder|lore_select_parent|"
             "lord_magic_lore_type|lore_type_list")


def _click(bus, path, timeout=10.0):
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


def engine_click(bus, component_id):
    lua = ('common.call_context_command([[RootComponent.ChildContext("%s").SimulateLClick]]) '
           'return "sent"' % component_id)
    try:
        return _ev(bus, lua, timeout=20.0) == "sent"
    except Exception as e:
        sys.stderr.write("click_actions: engine_click %s -> %s" % (component_id, repr(e)[:80]) + chr(10))
        return False


def _until(pred, cap, step=0.2):
    deadline = time.time() + cap
    while time.time() < deadline:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


_CLEAR_TRACE = common.CLEAR_SCREEN_TRACE
_BATTLE_ROOTS = ("popup_pre_battle", "popup_battle_results", "settlement_captured")
LAST_GUARD = [None]


def _clear_trace(row):
    import json
    try:
        with open(_CLEAR_TRACE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + chr(10))
    except OSError:
        pass


def clear_screen(bus, where=None):
    import nav
    import interrupts
    inflight = sys.exc_info()[1]
    pending_before = nav.engine_pending(bus)
    roots_before = nav.visible_roots(bus) or []
    guard = {}
    try:
        guard = interrupts.claim_screen(bus, where or "clear_screen",
                                        pending=pending_before, open_roots=roots_before)
    except BaseException as e:
        if inflight is not None:
            sys.stderr.write("click_actions: clear_screen(%s) guard raised over in-flight %r\n"
                             % (where, inflight))
        _clear_trace({"ts": time.time(), "where": where, "pending_before": pending_before,
                      "roots_before": roots_before[:14], "guard_raised": repr(e)[:120]})
        raise
    if guard.get("fired"):
        LAST_GUARD[0] = guard
    if guard.get("left"):
        sys.stderr.write("click_actions: clear_screen(%s) leaving %s on screen -- the interrupt "
                         "model has not answered it\n" % (where, guard["left"]))
        _clear_trace({"ts": time.time(), "where": where, "pending_before": pending_before,
                      "pending_after": guard.get("pending_after"), "closed_n": 0,
                      "roots_before": roots_before[:14], "guard": guard})
        return 0
    try:
        _ev(bus, 'common.call_context_command([[CloseAllPanels]]) return "sent"', timeout=15.0)
        _until(lambda: not [r for r in (nav.visible_roots(bus) or [])
                            if r not in nav.BASE_ROOTS], 1.0)
    except Exception as e:
        sys.stderr.write("click_actions: CloseAllPanels -> %s" % repr(e)[:80] + chr(10))
    n = 0
    closed = []
    try:
        closed = nav.close_popups(bus) or []
        n = len(closed)
    except Exception as e:
        sys.stderr.write("click_actions: close_popups -> %s" % repr(e)[:80] + chr(10))
    hit = [p for p in closed if any(b in str(p) for b in _BATTLE_ROOTS)]
    battle_root_before = [r for r in roots_before if r in _BATTLE_ROOTS]
    pending_after = nav.engine_pending(bus)
    if hit or battle_root_before or str(pending_before or "").startswith("true") \
            or pending_before != pending_after or guard.get("fired"):
        _clear_trace({"ts": time.time(), "where": where, "pending_before": pending_before,
                      "pending_after": pending_after, "battle_root_before": battle_root_before,
                      "closed_battle_paths": hit, "closed_n": n,
                      "roots_before": roots_before[:14],
                      "guard": (guard if guard.get("fired") else None)})
    try:
        if any(r not in nav.BASE_ROOTS for r in (nav.visible_roots(bus) or [])):
            nav.deselect(bus)
            _until(lambda: not [r for r in (nav.visible_roots(bus) or [])
                                if r not in nav.BASE_ROOTS], 1.2)
    except Exception as e:
        sys.stderr.write("click_actions: deselect -> %s" % repr(e)[:80] + chr(10))
    return n


def select_settlement(bus, region):
    r = _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s') if not s then return 'NO-SETT' end "
                      "local ok,e=pcall(function() s:Call('Select') end) return 'ok='..tostring(ok)" % region,
            timeout=20.0)
    _until(lambda: bool(_roots(bus)), 1.5)
    return r == "ok=true"


def select_character(bus, cqi):
    r = _ev(bus, _G + "local c=cco('CcoCampaignCharacter','%s') if not c then return 'NO-CHAR' end "
                      "local ok,e=pcall(function() c:Call('Select') end) return 'ok='..tostring(ok)" % cqi,
            timeout=20.0)
    _until(lambda: bool(_roots(bus)), 1.2)
    return r == "ok=true"


def focus_settlement(bus, region):
    r = _ev(bus, "local rg=cm:get_region('%s') "
                 "if not rg or rg:is_null_interface() then return 'NO-REGION' end "
                 "local s=rg:settlement() "
                 "if not s or s:is_null_interface() then return 'NO-SETT' end "
                 "local ok,x,y,d,b,h=pcall(function() return cm:get_camera_position() end) "
                 "if not ok then return 'NO-CAMERA-API' end "
                 "local ok2,px,py=pcall(function() "
                 "return s:display_position_x(),s:display_position_y() end) "
                 "if not ok2 then return 'NO-POSITION' end "
                 "local ok3=pcall(function() cm:set_camera_position(px,py,d,b,h) end) "
                 "if ok3 then return 'ok' end return 'SET-FAILED'" % region, timeout=12.0)
    if r != "ok":
        sys.stderr.write("click_actions: camera focus on settlement %s -> %s\n" % (region, r))
    return r == "ok"


def prepare(bus, kind, entity_id, expect_root=None, timeout=6.0):
    import nav
    if kind == "settlement":
        focus_settlement(bus, entity_id)
    elif kind == "lord":
        focus_character(bus, entity_id)
    if (expect_root and is_selected(bus, kind, entity_id) is True
            and expect_root in (nav.visible_roots(bus) or [])):
        return True, "already_ready"
    clear_screen(bus, "prepare")
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


def is_selected(bus, kind, entity_id):
    ctor = ("cco('CcoCampaignCharacter','%s')" % entity_id if kind == "lord"
            else "cco('CcoCampaignSettlement','settlement:%s')" % entity_id)
    try:
        v = _ev(bus, _G + "local c=%s if not c then return 'nil' end "
                          "return ts(g(c,'IsSelected'))" % ctor, timeout=8.0)
    except Exception:
        return None
    v = str(v or "").lower()
    return True if v == "true" else (False if v == "false" else None)


def _treasury(bus):
    return _ev(bus, "return cm:get_faction(cm:get_local_faction_name(true)):treasury()", timeout=8.0)


def _roots(bus):
    import nav
    try:
        return nav.visible_roots(bus)
    except Exception:
        return None






def _selected_edict(bus, region):
    return _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s');"
                         "local m=g(s,'FactionProvinceManagerContext'); if not m then return 'NO-MGR' end "
                         "local i=g(m,'SelectedInitiative'); if i then return ts(g(i,'Key')) end "
                         "return 'none'" % region, timeout=12.0)


def edict_options(bus, region):
    raw = _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s');"
                        "local m=g(s,'FactionProvinceManagerContext'); if not m then return '' end "
                        "local l=g(m,'InitiativeList'); if type(l)~='table' then return '' end local o={} "
                        "for i=1,#l do o[#o+1]=ts(g(l[i],'Key')) end return table.concat(o,',')"
             % region, timeout=20.0)
    return [k for k in str(raw or "").split(",") if k and k != "nil"]


def _edict_snapshot(bus, ctx, pick):
    region = ctx["entity_id"]
    prepare(bus, "settlement", region)
    return {"selected": _selected_edict(bus, region), "options": edict_options(bus, region)}




_LUA_EDICT_CLICK = (
    "local E=getfenv(cm.get_local_faction) local core=rawget(E,'core') "
    "local fuic=rawget(E,'find_uicomponent') "
    "local root=core:get_ui_root() "
    "local btn=fuic(root,'hud_campaign','BL_parent','stack_incentives','clip_parent',"
    "'stack_background','%(btn)s') "
    "if not btn then return 'NO-BTN' end "
    "btn:SimulateLClick() return 'clicked'")


def _edict_execute(bus, ctx, pick, before):
    ok, why = prepare(bus, "settlement", ctx["entity_id"])
    if not ok:
        sys.stderr.write("click_actions: edict refused, not a known state -> %s" % why + chr(10))
        return False
    r = _ev(bus, _LUA_EDICT_CLICK % {"btn": "button_%s" % pick["key"]}, timeout=15.0)
    if r != "clicked":
        sys.stderr.write("click_actions: edict button button_%s not clickable in the stack -> %s\n"
                         % (pick["key"], r))
        return False
    return True


def _edict_confirm(bus, ctx, pick, before):
    sel = _selected_edict(bus, ctx["entity_id"])
    return (sel == pick["key"]), {"selected": sel}


register("edict", {
    "layer": "click", "signal": "selected_initiative_key",
    "snapshot": _edict_snapshot, "prechecks": [],
    "execute": _edict_execute, "confirm": _edict_confirm,
    "timeout_s": 6.0, "poll_s": 1.2,
})


def _pending_recruits(bus, cqi):
    return _ev(bus, "local c=cm:get_character_by_cqi(%s) "
                    "if not c or not c:has_military_force() then return '' end "
                    "local ok,it=pcall(function() return c:military_force():recruitment_items() end) "
                    "if not ok or not it then return '' end "
                    "local n=0 pcall(function() n=#it end) "
                    "local ks={} for i=1,n do ks[#ks+1]=tostring(it[i]) end "
                    "table.sort(ks) return table.concat(ks,',')" % cqi, timeout=12.0)


POOL_ANCHOR = "unit_list"
QUEUE_SEP = "@"


def split_key(pick):
    raw = str(pick.get("key") or "")
    unit, _, suffix = raw.partition(QUEUE_SEP)
    queue = str((pick.get("params") or {}).get("queue") or suffix or "").lower() or None
    return unit, queue


def card_queue(path):
    segs = str(path or "").split("|")
    try:
        i = segs.index(POOL_ANCHOR)
    except ValueError:
        return None
    return segs[i - 1] if i > 0 else None


POOL_LIST = ("units_panel|main_units_panel|recruitment_docker|recruitment_options|"
             "recruitment_listbox|recruitment_pool_list|list_clip|list_box")


def recruitable_units(bus):
    try:
        tr = bus.send("tree", "units_panel 30 9000", timeout=20) or {}
    except Exception:
        return []
    nodes = tr.get("nodes") or []
    by_path = {}
    for n in nodes:
        by_path.setdefault(str(n.get("path") or ""), []).append(n)

    def _child_text(card_path, *tail):
        want = card_path + "|" + "|".join(tail)
        for n in by_path.get(want, []):
            t = str(n.get("text") or "").strip()
            if t:
                return t
        return None

    def _num(v):
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    out = []
    for n in nodes:
        i = str(n.get("id") or "")
        if not (i.endswith("_recruitable") and n.get("visible")):
            continue
        path = str(n.get("path") or "")
        out.append({"key": i[:-len("_recruitable")], "state": n.get("state"), "path": path,
                    "queue": card_queue(path),
                    "cost": _num(_child_text(path, "external_holder", "RecruitmentCost", "Cost")),
                    "upkeep": _num(_child_text(path, "external_holder", "UpkeepCost", "Upkeep")),
                    "turns": _num(_child_text(path, "unit_icon", "Turns"))})
    return out


def recruitment_capacity(bus):
    out = {}
    _res, pools = _find(bus, POOL_LIST, timeout=12.0)
    for pool in pools or []:
        _r, slots = _find(bus, "%s|%s|recruitment_cap|capacity_listview" % (POOL_LIST, pool),
                          timeout=10.0)
        if not slots:
            continue
        used = sum(1 for s in slots if str(s) == "used_slot")
        out[pool] = {"total": len(slots), "used": used, "free": len(slots) - used}
    return out


def focus_character(bus, cqi):
    r = _ev(bus, "local c=cm:get_character_by_cqi(%s) "
                 "if not c or c:is_null_interface() then return 'NO-CHAR' end "
                 "local ok,x,y,d,b,h=pcall(function() return cm:get_camera_position() end) "
                 "if not ok then return 'NO-CAMERA-API' end "
                 "local ok2,px,py=pcall(function() "
                 "return c:display_position_x(),c:display_position_y() end) "
                 "if not ok2 then return 'NO-POSITION' end "
                 "local ok3=pcall(function() cm:set_camera_position(px,py,d,b,h) end) "
                 "if ok3 then return 'ok' end return 'SET-FAILED'" % cqi, timeout=12.0)
    if r != "ok":
        sys.stderr.write("click_actions: camera focus on %s -> %s\n" % (cqi, r))
    return r == "ok"


def _recruit_snapshot(bus, ctx, pick):
    prepare(bus, "lord", ctx["entity_id"], expect_root="units_panel")
    r = _roots(bus)
    return {"treasury": _treasury(bus), "pending": _pending_recruits(bus, ctx["entity_id"]),
            "units_panel_open": (r is not None and "units_panel" in r),
            "capacity": recruitment_capacity(bus)}


def _recruit_precheck(bus, ctx, pick, before):
    """Crash guard only -- NOT a judgement about whether the recruit is legal."""
    if not before.get("units_panel_open"):
        return False, "units_panel_not_open_CTD_guard"
    return True, None


def _recruit_execute(bus, ctx, pick, before):
    try:
        return _recruit_execute_inner(bus, ctx, pick, before)
    finally:
        clear_screen(bus, "recruit_finally")


def _recruit_execute_inner(bus, ctx, pick, before):
    ok, why = prepare(bus, "lord", ctx["entity_id"], expect_root="units_panel")
    if not ok:
        sys.stderr.write("click_actions: recruit_unit refused, not a known state -> %s" % why + chr(10))
        return False
    cards = recruitable_units(bus)
    if not cards:
        if not _click(bus, RECRUIT_BTN):
            return False
        _until(lambda: bool(recruitable_units(bus)), 1.4)
        cards = recruitable_units(bus)
    unit, want_q = split_key(pick)
    same_key = [c for c in cards if c["key"] == unit]
    if want_q:
        pools = [c for c in same_key if str(c.get("queue") or "").lower() == want_q]
        if not pools:
            pools = [c for c in same_key
                     if str(c.get("queue") or "").lower().rstrip("0123456789") == want_q]
        if len(pools) > 1:
            sys.stderr.write("click_actions: %r matches %d pools %s -- ambiguous, refusing\n"
                             % (want_q, len(pools), [c.get("queue") for c in pools]))
            return False
        card = pools[0] if pools else None
        if card is None and same_key:
            sys.stderr.write("click_actions: %s offered in %s but %r was asked for\n"
                             % (unit, [c.get("queue") for c in same_key], want_q))
            return False
    else:
        card = _cheapest_pool(same_key)
    if card is None:
        sys.stderr.write("click_actions: unit %r not among recruitable cards\n" % pick["key"])
        return False
    pick.setdefault("params", {})["queue_used"] = card.get("queue")
    return _click(bus, card["path"])


def _cheapest_pool(cards):
    """Among the pools offering one unit, the one that costs the fewest turns, then the"""
    if not cards:
        return None

    def _rank(c):
        turns, cost = c.get("turns"), c.get("cost")
        return (turns if turns is not None else 1e9,
                cost if cost is not None else 1e9)

    return sorted(cards, key=_rank)[0]


def _recruit_confirm(bus, ctx, pick, before):
    t = _treasury(bus)
    after = str(_pending_recruits(bus, ctx["entity_id"]) or "")
    prior = str(before.get("pending") or "")
    want = split_key(pick)[0]
    queued = after.count(want) > prior.count(want)
    dropped = (t is not None and before.get("treasury") is not None and t < before["treasury"])
    return queued, {"treasury": t, "pending": after, "pending_before": prior,
                    "treasury_dropped": dropped,
                    # which pool the click actually landed in -- observed, not asserted
                    "queue_used": (pick.get("params") or {}).get("queue_used")}


register("recruit_unit", {
    "layer": "click", "signal": "unit_key_in_recruitment_items",
    "snapshot": _recruit_snapshot, "prechecks": [_recruit_precheck],
    "execute": _recruit_execute, "confirm": _recruit_confirm,
    "timeout_s": 2.0, "poll_s": 1.0, "spends_gold": True,
})


MERC_BTN_CONTAINER = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
                      "button_subpanel|button_group_army|mercenary_recruitment_button_container")
MERC_DISPLAY = ("units_panel|main_units_panel|recruitment_docker|recruitment_options|"
                "mercenary_display")
MERC_SUFFIX = "_mercenary"

MERC_POOL_BUTTONS = {
    "raise_dead": "button_mercenary_recruit_raise_dead",
    "recruit_ror": "button_mercenary_recruit_renown",
    "recruit_blessed": "button_mercenary_recruit_blessed_spawning",
    "recruit_imperial": "button_mercenary_recruit_imperial_supply",
}

MAX_FORCE_UNITS = 20

_LUA_FORCE_UNITS = (
    "local c=cm:get_character_by_cqi(%s) "
    "if not c or not c:has_military_force() then return '' end "
    "local ul=c:military_force():unit_list() "
    "local ks={} for i=0,ul:num_items()-1 do ks[#ks+1]=tostring(ul:item_at(i):unit_key()) end "
    "table.sort(ks) return table.concat(ks,',')")


def _force_units(bus, cqi):
    raw = _ev(bus, _LUA_FORCE_UNITS % cqi, timeout=12.0)
    return [k for k in str(raw or "").split(",") if k and k != "nil"]


def mercenary_units(bus):
    try:
        tr = bus.send("tree", "units_panel 30 9000", timeout=20) or {}
    except Exception:
        return []
    nodes = tr.get("nodes") or []
    by_path = {}
    for n in nodes:
        by_path.setdefault(str(n.get("path") or ""), []).append(n)

    def _child_text(card_path, *tail):
        want = card_path + "|" + "|".join(tail)
        for n in by_path.get(want, []):
            t = str(n.get("text") or "").strip()
            if t:
                return t
        return None

    def _num(v):
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    out = []
    for n in nodes:
        i = str(n.get("id") or "")
        if not (i.endswith(MERC_SUFFIX) and n.get("visible")):
            continue
        path = str(n.get("path") or "")
        if "|list_clip|list_box|" not in path:
            continue
        out.append({"key": i[:-len(MERC_SUFFIX)], "state": n.get("state"), "path": path,
                    "cost": _num(_child_text(path, "external_holder", "RecruitmentCost", "Cost"))})
    return out


def _merc_snapshot(bus, ctx, pick):
    prepare(bus, "lord", ctx["entity_id"], expect_root="units_panel")
    r = _roots(bus)
    units = _force_units(bus, ctx["entity_id"])
    return {"treasury": _treasury(bus), "force_units": units, "unit_count": len(units),
            "units_panel_open": (r is not None and "units_panel" in r)}


def _merc_precheck(bus, ctx, pick, before):
    """Crash guard only. `army_full` is decided by the advisor from state.units."""
    if not before.get("units_panel_open"):
        return False, "units_panel_not_open_CTD_guard"
    return True, None


def _merc_execute_for(pool_btn):
    def _exec(bus, ctx, pick, before):
        try:
            return _merc_execute_inner(bus, ctx, pick, before, pool_btn)
        finally:
            clear_screen(bus, "merc_finally")
    return _exec


def _merc_execute_inner(bus, ctx, pick, before, pool_btn):
    ok, why = prepare(bus, "lord", ctx["entity_id"], expect_root="units_panel")
    if not ok:
        sys.stderr.write("click_actions: %s refused, not a known state -> %s\n"
                         % (pick.get("action_type"), why))
        return False
    unit = str(pick.get("key") or "")

    def _card():
        return next((c for c in mercenary_units(bus) if c["key"] == unit), None)

    card = _card()
    if card is None:
        if not _click(bus, "%s|%s" % (MERC_BTN_CONTAINER, pool_btn)):
            return False
        _until(lambda: bool(mercenary_units(bus)), 2.0)
        card = _card()
    if card is None:
        sys.stderr.write("click_actions: unit %r not among mercenary cards for %s\n"
                         % (unit, pool_btn))
        return False
    if card.get("state") == "locked":
        sys.stderr.write("click_actions: mercenary card %s is locked\n" % unit)
        return False
    if not _click(bus, card["path"]):
        return False
    hire = {}

    def _armed():
        _res, kids = _find(bus, MERC_DISPLAY + "|buttons_holder")
        for k in kids or []:
            if not k.startswith("button_"):
                continue
            res, _kids = _find(bus, "%s|buttons_holder|%s" % (MERC_DISPLAY, k))
            if res.get("visible") and res.get("state") == "active":
                hire["path"] = "%s|buttons_holder|%s" % (MERC_DISPLAY, k)
                return True
        return False

    if not _until(_armed, 2.0):
        sys.stderr.write("click_actions: no hire button armed after selecting %s\n" % unit)
        return False
    return _click(bus, hire["path"])


def _merc_confirm(bus, ctx, pick, before):
    unit = str(pick.get("key") or "")
    t = _treasury(bus)
    after = _force_units(bus, ctx["entity_id"])
    prior = before.get("force_units") or []
    hired = after.count(unit) > prior.count(unit)
    return hired, {"treasury": t, "unit_count": len(after),
                   "unit_count_before": len(prior),
                   "treasury_dropped": (t is not None and before.get("treasury") is not None
                                        and t < before["treasury"])}


for _merc_atype, _merc_btn in MERC_POOL_BUTTONS.items():
    register(_merc_atype, {
        "layer": "click", "signal": "force_unit_key_count_increased",
        "snapshot": _merc_snapshot, "prechecks": [_merc_precheck],
        "execute": _merc_execute_for(_merc_btn), "confirm": _merc_confirm,
        "timeout_s": 4.0, "poll_s": 1.0, "spends_gold": True,
    })


def _character_count(bus):
    for attempt in range(1):
        v = _ev(bus, "local f=cm:get_local_faction(true) return f:character_list():num_items()",
                timeout=20.0)
        try:
            return int(float(v))
        except (TypeError, ValueError):
            if attempt < 2:
                time.sleep(1.0)
    sys.stderr.write("click_actions: character count UNREADABLE after 3 tries -- "
                     "reporting unknown, NOT zero-growth" + chr(10))
    return None






def lore_tabs(bus):
    try:
        t = bus.send("tree", "%s 6 4000" % LORE_LIST, timeout=20) or {}
    except Exception as e:
        sys.stderr.write("click_actions: lore tree -> %s\n" % repr(e)[:80])
        return []
    out = []
    for n in (t.get("nodes") or []):
        i = str(n.get("id") or "")
        if "lore" in i and n.get("visible") and str(n.get("path") or "").endswith(i):
            if i not in ("lore_type_list",):
                out.append((i, str(n.get("path"))))
    return out


def split_lord_key(bus, key, types):
    want, _, explicit = str(key).partition("@")
    hits = [t for t in types if want == t or want.endswith("_" + t) or ("_" + t + "_") in want]
    if not hits:
        return None, (explicit or None)
    t = max(hits, key=len)
    tail = want.split(t, 1)[1].strip("_") if t in want else ""
    return t, (explicit or tail or None)


def lord_candidates(bus):
    try:
        t = bus.send("tree", "%s 6 4000" % CANDIDATE_LIST, timeout=20) or {}
    except Exception as e:
        sys.stderr.write("click_actions: candidate tree -> %s\n" % repr(e)[:80])
        return []
    rows = [n for n in (t.get("nodes") or [])
            if str(n.get("id") or "").startswith("general_candidate") and n.get("visible")
            and str(n.get("state")) in ("active", "selected")]
    rows.sort(key=lambda n: (n.get("y") or 0, n.get("x") or 0))
    return [str(n.get("id")) for n in rows]


def _lord_snapshot(bus, ctx, pick):
    n = _character_count(bus)
    if n is None:
        return None
    return {"treasury": _treasury(bus), "n_chars": n, "t0": time.time()}


def _panel_mode(bus):
    r, _ = _find(bus, PANEL_TITLE)
    if not r.get("found") or not r.get("visible"):
        return None
    return str(r.get("state") or "")


def _open_panel_mode(bus, mode):
    if _panel_mode(bus) == mode:
        return True
    btn = AGENTS_BTN if mode == AGENT_MODE else CREATE_ARMY_BTN
    if not _click(bus, btn):
        return False
    if _until(lambda: _panel_mode(bus) == mode, 3.0):
        return True
    sys.stderr.write("click_actions: %s left the panel in mode %r, wanted %r -- refusing to click "
                     "a list the panel is not showing\n"
                     % (btn.rsplit("|", 1)[-1], _panel_mode(bus), mode))
    return False


def _lord_execute(bus, ctx, pick, before):
    try:
        return _lord_execute_inner(bus, ctx, pick, before)
    finally:
        clear_screen(bus, "lord_finally")


def _lord_execute_inner(bus, ctx, pick, before):
    ok, why = prepare(bus, "settlement", ctx["entity_id"], expect_root="settlement_panel")
    if not ok:
        sys.stderr.write("click_actions: %s refused, not a known state -> %s"
                         % (pick.get("action_type") or "recruit", why) + chr(10))
        return False
    # recruit keys are "<subtype>@<candidate_index>" -- the pool offers one row per
    # candidate now, and the subtype is what names the UI button. Same "@" convention
    # recruit_unit and the building slot ops already use. Bare subtypes still parse.
    want = str(pick["key"]).split("@", 1)[0]
    params = pick.get("params") or {}
    is_hero = pick.get("action_type") == "recruit_hero"
    if not _open_panel_mode(bus, AGENT_MODE if is_hero else LORD_MODE):
        return False

    if is_hero:
        agent_list = [k for k in _find(bus, AGENT_TYPE_LIST)[1]
                      if not k.startswith("button_template")]
        agent_type = params.get("agent_type")
        if agent_type and agent_type in agent_list:
            btn, lore_hint = agent_type, None
        else:
            btn, lore_hint = split_lord_key(bus, want, agent_list)
        type_path, commit_id, offered = AGENT_TYPE_LIST, "button_confirm", agent_list
    else:
        lord_list = [k for k in _find(bus, LORD_TYPE_LIST)[1]
                     if not k.startswith("button_template")]
        btn, lore_hint = split_lord_key(bus, want, lord_list)
        type_path, commit_id, offered = LORD_TYPE_LIST, "button_raise", lord_list
        if btn is None:
            if not _open_panel_mode(bus, AGENT_MODE):
                return False
            agent_list = [k for k in _find(bus, AGENT_TYPE_LIST)[1]
                          if not k.startswith("button_template")]
            btn, lore_hint = split_lord_key(bus, want, agent_list)
            type_path, commit_id, offered = AGENT_TYPE_LIST, "button_confirm", agent_list
    if btn is None:
        sys.stderr.write("click_actions: %r is not in the panel's %s list %s"
                         % (want, "hero" if type_path == AGENT_TYPE_LIST else "lord", offered)
                         + chr(10))
        return False
    if not _click(bus, "%s|%s" % (type_path, btn)):
        return False
    _until(lambda: bool(lord_candidates(bus)) or bool(lore_tabs(bus)), 1.5)
    lores = lore_tabs(bus)
    if lores:
        if not lore_hint:
            sys.stderr.write("click_actions: %s offers %d lores %s and the pick names none -- "
                             "refusing to accept whichever tab happens to be open\n"
                             % (btn, len(lores), [i for i, _p in lores]))
            return False
        hit = [(i, pth) for i, pth in lores if lore_hint.lower() in i.lower()]
        if len(hit) != 1:
            sys.stderr.write("click_actions: lore %r matches %d of %s -- refusing\n"
                             % (lore_hint, len(hit), [i for i, _p in lores]))
            return False
        if not _click(bus, hit[0][1]):
            return False
        _until(lambda: bool(lord_candidates(bus)), 2.0)
    cands = lord_candidates(bus)
    if not cands:
        sys.stderr.write("click_actions: no general_candidate rows for %r\n" % pick["key"])
        return False
    idx = int((pick.get("params") or {}).get("candidate_index", 0))
    if not engine_click(bus, cands[min(idx, len(cands) - 1)]):
        return False
    res, _ = _find(bus, BUTTON_RAISE)
    if res.get("state") != "active":
        sys.stderr.write("click_actions: button_raise not active after candidate select (state=%s)\n"
                         % res.get("state"))
        return False
    return engine_click(bus, commit_id)


def _lord_confirm(bus, ctx, pick, before):
    n = _character_count(bus)
    t = _treasury(bus)
    before_n = before.get("n_chars")
    if n is None:
        return False, {"n_chars": None, "n_chars_before": before_n, "unreadable": True}
    grew = (before_n is not None and n > before_n)
    g = LAST_GUARD[0] or {}
    if (grew and g.get("ts", 0) >= (before.get("t0") or 0)
            and any(str(s).startswith(("dilemma:", "event_ack:")) for s in g.get("steps") or [])):
        sys.stderr.write("click_actions: lord confirm not attributable -- interrupt %s answered "
                         "during the clear\n" % (g.get("steps"),))
        return False, {"n_chars": n, "n_chars_before": before_n, "treasury": t,
                       "confirm_tainted_by_interrupt": g.get("steps")}
    return grew, {"n_chars": n, "n_chars_before": before_n, "treasury": t,
                  "treasury_dropped": (t is not None and before.get("treasury") is not None
                                       and t < before["treasury"])}


register("recruit_lord", {
    "layer": "click", "signal": "new_character_cqi_and_treasury_drop",
    "snapshot": _lord_snapshot,
    "execute": _lord_execute, "confirm": _lord_confirm,
    "timeout_s": 6.0, "poll_s": 2.0, "spends_gold": True,
})

register("recruit_hero", {
    "layer": "click", "signal": "new_character_cqi_and_treasury_drop",
    "snapshot": _lord_snapshot,
    "execute": _lord_execute, "confirm": _lord_confirm,
    "timeout_s": 6.0, "poll_s": 2.0, "spends_gold": True,
})
