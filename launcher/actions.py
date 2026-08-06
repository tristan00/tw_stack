from __future__ import annotations
import os
import subprocess
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")
import nav

_SETTLE = 1.8
_ACT = 1.4

SCREEN_W, SCREEN_H = 2560, 1440
CX, CY = SCREEN_W // 2, SCREEN_H // 2


def _warn(where, detail):
    sys.stderr.write("actions: %s -> %s\n" % (where, detail))


def _ev(bus, expr):
    r = (bus.send("eval", expr, timeout=6.0) or {}).get("result")
    if r is None:
        _warn("_ev", "eval returned None: %s" % expr[:64])
    return r


def _disp_char(bus, cqi):
    return (_ev(bus, "cm:get_character_by_cqi(%d):display_position_x()" % cqi),
            _ev(bus, "cm:get_character_by_cqi(%d):display_position_y()" % cqi))


def _disp_sett(bus, region):
    return (_ev(bus, "cm:get_region('%s'):settlement():display_position_x()" % region),
            _ev(bus, "cm:get_region('%s'):settlement():display_position_y()" % region))


def _roots(bus):
    r = bus.send("roots", "", timeout=8.0) or {}
    if not r.get("kids"):
        _warn("_roots", "empty/None roots reply (bus miss)")
    return [k.get("id") for k in (r.get("kids") or []) if k.get("visible")]


def focus_char_tight(bus, cqi, d=4.0):
    x, y = _disp_char(bus, cqi)
    if x is None or y is None:
        _warn("focus_char_tight", "unresolved display position for cqi %s (x=%s y=%s)" % (cqi, x, y))
        return False
    bus.send("focus", "xy %s %s %s 0.0 6.0" % (x, y, d), timeout=6.0)
    return True


def focus_sett_tight(bus, region, d=4.0):
    x, y = _disp_sett(bus, region)
    if x is None or y is None:
        _warn("focus_sett_tight", "unresolved display position for region %s (x=%s y=%s)" % (region, x, y))
        return False
    bus.send("focus", "xy %s %s %s 0.0 6.0" % (x, y, d), timeout=6.0)
    return True


def selected_army(bus) -> bool:
    return "units_panel" in _roots(bus)


def select_char(bus, cqi, tries=2) -> bool:
    for _ in range(tries):
        if not focus_char_tight(bus, cqi):
            return False
        time.sleep(_SETTLE)
        time.sleep(1.0)
        if selected_army(bus):
            return True
    return selected_army(bus)


def _ap(bus, cqi):
    r = bus.send("chars", "", timeout=6.0) or {}
    for c in (r.get("chars") or []):
        if c.get("cqi") == cqi:
            return c.get("ap_pct")
    return None


def order_attack_at_center(bus) -> None:
    time.sleep(_ACT)


def pre_battle_open(bus) -> bool:
    return "popup_pre_battle" in _roots(bus)


def attack_char(bus, enemy_cqi, lord_cqi=56) -> dict:
    sel = select_char(bus, lord_cqi)
    focus_char_tight(bus, enemy_cqi)
    time.sleep(_SETTLE)
    order_attack_at_center(bus)
    return {"selected": sel, "pre_battle": pre_battle_open(bus)}


def attack_settlement(bus, region, lord_cqi=56) -> dict:
    sel = select_char(bus, lord_cqi)
    focus_sett_tight(bus, region)
    time.sleep(_SETTLE)
    order_attack_at_center(bus)
    pb = False
    for _ in range(6):
        if pre_battle_open(bus):
            pb = True
            break
        time.sleep(0.7)
    return {"selected": sel, "pre_battle": pb}


def autoresolve(bus) -> list:
    ar = bus.send("autoresolve", "", timeout=10.0) or {}
    if not ar or ar.get("error"):
        _warn("autoresolve", "autoresolve reply missing/errored: %s" % ar)
    time.sleep(3.0)
    return _roots(bus)


_BR = "popup_battle_results|mid|battle_results|post_battle_results_panel"


def confirm_results_checkmark(bus) -> bool:
    p = "%s|button_set_settlement_captured|button_accept" % _BR
    r = bus.send("click", p, timeout=8.0) or {}
    time.sleep(2.5)
    if not r.get("clicked"):
        _warn("confirm_results_checkmark", "click did not register: %s (%s)" % (p, r))
    return bool(r.get("clicked"))


def handle_captives(bus, choice="release") -> bool:
    p = "%s|button_set_win_holder|button_set_win|button_captive_option_%s" % (_BR, choice)
    r = bus.send("click", p, timeout=8.0) or {}
    time.sleep(1.5)
    if not r.get("clicked"):
        _warn("handle_captives", "click did not register: %s (%s)" % (p, r))
    return bool(r.get("clicked"))


def occupy_settlement(bus, choice="Occupy") -> bool:
    tr = bus.send("tree", "settlement_captured 22 3000", timeout=15.0) or {}
    for n in (tr.get("nodes") or []):
        if n.get("id") == "dy_option" and str(n.get("text")).strip().lower() == choice.strip().lower():
            opt = str(n.get("path")).rsplit("|dy_option", 1)[0]
            r = bus.send("click", opt, timeout=8.0) or {}
            time.sleep(2.5)
            return bool(r.get("clicked"))
    seen = [str(n.get("text")) for n in (tr.get("nodes") or []) if n.get("id") == "dy_option"]
    _warn("occupy_settlement", "choice %r not found among dy_option texts %s" % (choice, seen))
    return False


def clear_popups(bus) -> int:
    return len(nav.close_popups(bus))


_CCO_G = ("local function g(c,p) local ok,v=pcall(function() return c:Call(p) end);"
          "if ok and v~=nil then return v end return nil end ")


def _cco_ev(bus, lua, timeout=15.0):
    r = bus.send("eval", lua, timeout=timeout) or {}
    if r.get("error"):
        _warn("cco_ev", str(r["error"])[:160])
        return None
    return r.get("result")


def cco_activate_stance(bus, char_cqi, stance_key, legal_keys) -> bool:
    if not legal_keys or stance_key not in legal_keys:
        _warn("cco_activate_stance", "REFUSED %r: not in legality whitelist %s"
              % (stance_key, sorted(legal_keys or [])))
        return False
    pre = _cco_ev(bus, _CCO_G +
        "local mf=cco('CcoCampaignCharacter','%s'):Call('MilitaryForceContext');"
        "if not mf then return 'NO-FORCE' end local l=mf:Call('StanceList');"
        "for i,v in ipairs(l) do if tostring(g(v,'Key'))=='%s' then "
        "return tostring(g(v,'CanBeActivated'))..'/'..tostring(g(v,'CanAfford')) end end "
        "return 'NOT-IN-LIST'" % (char_cqi, stance_key))
    if pre != "true/true":
        _warn("cco_activate_stance", "pre-check failed for %r: %s" % (stance_key, pre))
        return False
    _cco_ev(bus, _CCO_G +
        "local mf=cco('CcoCampaignCharacter','%s'):Call('MilitaryForceContext');"
        "local l=mf:Call('StanceList');"
        "for i,v in ipairs(l) do if tostring(g(v,'Key'))=='%s' then "
        "pcall(function() v:Call('Activate') end); return 'called' end end return 'NOT-IN-LIST'"
        % (char_cqi, stance_key))
    time.sleep(1.2)
    now = _cco_ev(bus, _CCO_G +
        "local mf=cco('CcoCampaignCharacter','%s'):Call('MilitaryForceContext');"
        "local l=mf:Call('StanceList');"
        "for i,v in ipairs(l) do if g(v,'IsActive')==true then return tostring(g(v,'Key')) end end"
        % char_cqi)
    ok = now == stance_key
    if not ok:
        _warn("cco_activate_stance", "post-assert failed: active=%s wanted=%s" % (now, stance_key))
    return ok


def cco_construct(bus, region, slot_index, building_key, expected_cost=None) -> bool:
    t0 = _ev(bus, "cm:get_faction(cm:get_local_faction_name(true)):treasury()")
    if expected_cost is not None and t0 is not None and t0 < expected_cost:
        _warn("cco_construct", "pre-check: treasury %s < cost %s" % (t0, expected_cost))
        return False
    res = _cco_ev(bus, _CCO_G +
        "local s=cco('CcoCampaignSettlement','settlement:%s');"
        "if not s then return 'NO-CTX' end local slots=s:Call('BuildingSlotList');"
        "for i,x in ipairs(slots) do if g(x,'Index')==%d then "
        "  local p=g(x,'PossibleUpgradeWithoutConversionsList');"
        "  if type(p)~='table' then return 'NO-POSSIBLES' end "
        "  for j=0,#p-1 do "
        "    if tostring(g(x,'PossibleUpgradeWithoutConversionsList['..j..'].Key'))=='%s' then "
        "      if g(x,'BuildingRequirementsMet(PossibleUpgradeWithoutConversionsList['..j..'])')~=true then "
        "        return 'REQ-NOT-MET' end "
        "      pcall(function() x:Call('Construct(PossibleUpgradeWithoutConversionsList['..j..'])') end);"
        "      return 'called' end end return 'KEY-NOT-IN-SLOT' end end return 'NO-SLOT'"
        % (region, int(slot_index), building_key), timeout=25.0)
    if res != "called":
        _warn("cco_construct", "%s slot %s %r -> %s" % (region, slot_index, building_key, res))
        return False
    time.sleep(2.0)
    t1 = _ev(bus, "cm:get_faction(cm:get_local_faction_name(true)):treasury()")
    queued = _cco_ev(bus, _CCO_G +
        "local s=cco('CcoCampaignSettlement','settlement:%s');"
        "local slots=s:Call('BuildingSlotList');"
        "for i,x in ipairs(slots) do if g(x,'Index')==%d then "
        "return tostring(g(x,'IsBuildingNew')) end end" % (region, int(slot_index)))
    ok = (queued == "true") and (t0 is not None and t1 is not None and t1 < t0)
    if not ok:
        _warn("cco_construct", "post-assert failed: queued=%s treasury %s->%s" % (queued, t0, t1))
    return ok
