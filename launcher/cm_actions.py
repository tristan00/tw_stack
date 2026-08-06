from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

from cco_actions import _ev, register

_LOOKUP = "character_cqi:%s"


def _roots(bus):
    import nav
    try:
        return nav.visible_roots(bus)
    except Exception:
        return None


def _prebattle(bus):
    r = _roots(bus)
    if r is None:
        return "blocked"
    return "popup_pre_battle" in r


def _char_scalar(bus, cqi, expr):
    return _ev(bus, "local c=cm:get_character_by_cqi(%s); if c and not c:is_null_interface() "
                    "then return tostring(%s) end return 'no-char'" % (cqi, expr), timeout=8.0)


def _attack_army_snapshot(bus, ctx, pick):
    cqi = ctx["entity_id"]
    tgt = (pick.get("params") or {}).get("target_cqi")
    if tgt is None:
        return None
    return {"ap": _char_scalar(bus, cqi, "c:action_points_remaining_percent()"),
            "besieging": _char_scalar(bus, cqi, "c:is_besieging()"),
            "acted": _char_scalar(bus, cqi, "c:performed_action_this_turn()"),
            "target_alive": _ev(bus, "local t=cm:get_character_by_cqi(%s) "
                                     "return tostring(t ~= nil and not t:is_null_interface())" % tgt),
            "can_reach": _ev(bus, "local a=cm:get_character_by_cqi(%s); local t=cm:get_character_by_cqi(%s); "
                                  "local ok,v=pcall(function() return cm:character_can_reach_character(a,t) end); "
                                  "return tostring(ok and v)" % (cqi, tgt), timeout=10.0)}


def _attack_army_gate(bus, ctx, pick, before):
    if before.get("can_reach") != "true":
        return False, "cannot_reach_target"
    return True, None


def _attack_army_execute(bus, ctx, pick, before):
    tgt = (pick.get("params") or {})["target_cqi"]
    return _ev(bus, "local ok,e=pcall(function() cm:attack('%s','%s',false,true) end); "
                    "return 'ok='..tostring(ok)" % (_LOOKUP % ctx["entity_id"], _LOOKUP % tgt),
               timeout=20.0) == "ok=true"


def _attack_army_confirm(bus, ctx, pick, before):
    pb = _prebattle(bus)
    acted = _char_scalar(bus, ctx["entity_id"], "c:performed_action_this_turn()")
    landed = (pb is True) or (acted == "true" and before.get("acted") != "true")
    return landed, {"pre_battle": pb, "acted": acted, "acted_before": before.get("acted")}


register("attack_army", {
    "layer": "cm", "signal": "pre_battle_popup",
    "snapshot": _attack_army_snapshot, "gates": [_attack_army_gate],
    "execute": _attack_army_execute, "confirm": _attack_army_confirm,
    "timeout_s": 20.0, "poll_s": 2.0, "retryable": False,
})


def _attack_sett_snapshot(bus, ctx, pick):
    cqi, region = ctx["entity_id"], pick["key"]
    return {"ap": _char_scalar(bus, cqi, "c:action_points_remaining_percent()"),
            "acted": _char_scalar(bus, cqi, "c:performed_action_this_turn()"),
            "besieging": _char_scalar(bus, cqi, "c:is_besieging()"),
            "can_reach": _ev(bus, "local c=cm:get_character_by_cqi(%s); local s=cm:get_region('%s'):settlement(); "
                                  "local ok,v=pcall(function() return cm:character_can_reach_settlement(c,s) end); "
                                  "return tostring(ok and v)" % (cqi, region), timeout=10.0)}


def _attack_sett_gate(bus, ctx, pick, before):
    if before.get("can_reach") != "true":
        return False, "cannot_reach_settlement"
    return True, None


def _attack_sett_execute(bus, ctx, pick, before):
    return _ev(bus, "local ok,e=pcall(function() cm:attack_region('%s','%s') end); "
                    "return 'ok='..tostring(ok)" % (_LOOKUP % ctx["entity_id"], pick["key"]),
               timeout=20.0) == "ok=true"


def _attack_sett_confirm(bus, ctx, pick, before):
    pb = _prebattle(bus)
    bes = _char_scalar(bus, ctx["entity_id"], "c:is_besieging()")
    acted = _char_scalar(bus, ctx["entity_id"], "c:performed_action_this_turn()")
    landed = ((pb is True)
              or (bes == "true" and before.get("besieging") != "true")
              or (acted == "true" and before.get("acted") != "true"))
    return landed, {"pre_battle": pb, "besieging": bes, "besieging_before": before.get("besieging"),
                    "acted": acted, "acted_before": before.get("acted")}


register("attack_settlement", {
    "layer": "cm", "signal": "pre_battle_or_is_besieging",
    "snapshot": _attack_sett_snapshot, "gates": [_attack_sett_gate],
    "execute": _attack_sett_execute, "confirm": _attack_sett_confirm,
    "timeout_s": 20.0, "poll_s": 2.0, "retryable": False,
})


def _garrison_snapshot(bus, ctx, pick):
    return {"in_settlement": _char_scalar(bus, ctx["entity_id"], "c:in_settlement()"),
            "acted": _char_scalar(bus, ctx["entity_id"], "c:performed_action_this_turn()")}


def _garrison_gate(bus, ctx, pick, before):
    if before.get("in_settlement") == "true":
        return False, "already_in_settlement"
    return True, None


def _garrison_execute(bus, ctx, pick, before):
    key = pick["key"] if str(pick["key"]).startswith("settlement:") else "settlement:%s" % pick["key"]
    return _ev(bus, "local ok,e=pcall(function() cm:join_garrison('%s','%s') end); "
                    "return 'ok='..tostring(ok)" % (_LOOKUP % ctx["entity_id"], key), timeout=20.0) == "ok=true"


def _garrison_confirm(bus, ctx, pick, before):
    v = _char_scalar(bus, ctx["entity_id"], "c:in_settlement()")
    return (v == "true"), {"in_settlement": v}


register("garrison", {
    "layer": "cm", "signal": "in_settlement_true",
    "snapshot": _garrison_snapshot, "gates": [_garrison_gate],
    "execute": _garrison_execute, "confirm": _garrison_confirm,
    "timeout_s": 10.0, "poll_s": 1.5,
})


def _leave_execute(bus, ctx, pick, before):
    p = pick.get("params") or {}
    return _ev(bus, "local ok,e=pcall(function() cm:leave_garrison('%s',%s,%s) end); "
                    "return 'ok='..tostring(ok)" % (_LOOKUP % ctx["entity_id"], p["x"], p["y"]),
               timeout=20.0) == "ok=true"


def _leave_confirm(bus, ctx, pick, before):
    v = _char_scalar(bus, ctx["entity_id"], "c:in_settlement()")
    return (v == "false"), {"in_settlement": v}


register("leave_garrison", {
    "layer": "cm", "signal": "in_settlement_false",
    "snapshot": _garrison_snapshot,
    "gates": [lambda bus, ctx, pick, before: (before.get("in_settlement") == "true", "not_in_settlement")],
    "execute": _leave_execute, "confirm": _leave_confirm,
    "timeout_s": 10.0, "poll_s": 1.5,
})


_LUA_MOVE = (
    "local c=cm:get_character_by_cqi(%(cqi)s) "
    "if not c or c:is_null_interface() then return 'no-char' end "
    "if (c:action_points_remaining_percent() or 0)<=0 then return 'no-ap' end "
    "local ok,vx,vy=pcall(function() return "
    "cm:find_valid_spawn_location_for_character_from_position(c:faction():name(),%(x)d,%(y)d,true) end) "
    "if not ok or not vx or vx<0 then return 'invalid-dest' end "
    "if not c:can_reach_position(vx,vy) then return 'unreachable-this-turn' end "
    "local l=cm:char_lookup_str(%(cqi)s) "
    "local ok2,e=pcall(function() cm:enable_movement_for_character(l) cm:move_to(l,vx,vy) end) "
    "if not ok2 then return 'order-failed:'..tostring(e) end "
    "return 'ordered '..vx..','..vy")


def _move_snapshot(bus, ctx, pick):
    cqi = ctx["entity_id"]
    return {"x": _char_scalar(bus, cqi, "c:logical_position_x()"),
            "y": _char_scalar(bus, cqi, "c:logical_position_y()"),
            "ap": _char_scalar(bus, cqi, "c:action_points_remaining_percent()"),
            "besieging": _char_scalar(bus, cqi, "c:is_besieging()"),
            "acted": _char_scalar(bus, cqi, "c:performed_action_this_turn()")}


def _move_gate(bus, ctx, pick, before):
    try:
        if float(before.get("ap") or 0) <= 0.0:
            return False, "no_action_points"
    except (TypeError, ValueError):
        pass
    return True, None


def _move_execute(bus, ctx, pick, before):
    p = pick.get("params") or {}
    x, y = p.get("x"), p.get("y")
    if x is None or y is None:
        sys.stderr.write("cm_actions: move pick carries no destination -- %r\n" % (pick.get("key"),))
        return False
    q = (_LUA_MOVE % {"cqi": ctx["entity_id"], "x": int(x), "y": int(y)})
    r = str(_ev(bus, q, timeout=25.0) or "")
    if not r.startswith("ordered"):
        sys.stderr.write("cm_actions: move not ordered (%s) for cqi %s -> %s\n"
                         % (r, ctx["entity_id"], pick.get("key")))
        return False
    sys.stderr.write("cm_actions: move ordered cqi=%s from=%s,%s asked=%s,%s snapped=%s\n"
                     % (ctx["entity_id"], before.get("x"), before.get("y"), int(x), int(y),
                        r[len("ordered"):].strip()))
    return True


def _move_confirm(bus, ctx, pick, before):
    cqi = ctx["entity_id"]
    x = _char_scalar(bus, cqi, "c:logical_position_x()")
    y = _char_scalar(bus, cqi, "c:logical_position_y()")
    after = {"x": x, "y": y}
    if x is None or y is None or x == "no-char":
        return False, dict(after, unreadable=True)
    moved = (x != before.get("x")) or (y != before.get("y"))
    if moved:
        after["settled"] = _await_standstill(bus, cqi, after)
    return moved, after


_SETTLE_POLLS = 6
_SETTLE_PAUSE = 0.8


def _await_standstill(bus, cqi, after):
    last = (after.get("x"), after.get("y"))
    stable = 0
    for _ in range(_SETTLE_POLLS):
        time.sleep(_SETTLE_PAUSE)
        x = _char_scalar(bus, cqi, "c:logical_position_x()")
        y = _char_scalar(bus, cqi, "c:logical_position_y()")
        if x is None or y is None or x == "no-char":
            return False
        if (x, y) == last:
            stable += 1
            if stable >= 2:
                after["x"], after["y"] = x, y
                return True
        else:
            stable = 0
            last = (x, y)
    after["x"], after["y"] = last
    sys.stderr.write("cm_actions: cqi=%s still moving after %.1fs -- next order may land mid-path\n"
                     % (cqi, _SETTLE_POLLS * _SETTLE_PAUSE))
    return False


register("move", {
    "layer": "cm", "signal": "position_changed",
    "snapshot": _move_snapshot, "gates": [_move_gate],
    "execute": _move_execute, "confirm": _move_confirm,
    "timeout_s": 6.0, "poll_s": 1.0, "retryable": False,
})
