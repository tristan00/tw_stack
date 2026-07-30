r"""cm_actions.py -- v7 executors on the cm (campaign SCRIPT API) layer.

These are the actions the CCO/UI-command layer genuinely does NOT expose (verified by full
enumeration of the shipped CCO reference: 515 contexts / 6347 functions). CA documents the
commands used here as "equivalent to the player or AI issuing the same order", so they are
player-equivalent, NOT god-mode. God-mode families (cm:force_*, create_force*, grant_*,
spawn_*, teleport_*, Dev*) are deliberately NOT used.

LIVE-VERIFIED 2026-07-30 (WH3 v8.1.1), each with a post-assert:
  attack army       cm:attack(attacker, target, lay_siege, ignore_shroud) -> pre-battle opened,
                    target dead after autoresolve.
  attack settlement cm:attack_region(attacker, region_key)  <-- EXACTLY 2 ARGS.
                    ⚠ THE BUG THAT COST US HOURS: the 3-arg form (attacker, region, bool) is the
                    WH2 signature; in WH3 it silently no-ops -- returns ok, does nothing at all.
                    With 2 args: lord moved, AP 100->25.9, is_besieging=true, pre-battle opened,
                    autoresolve -> Occupy -> region captured.
  garrison          cm:join_garrison(attacker, 'settlement:<region_key>') -> in_settlement true.
                    ⚠ the settlement key is the settlement's OWN key() ("settlement:<region_key>"),
                    NOT the bare region key (bare region key silently no-ops).
  leave garrison    cm:leave_garrison(attacker, x, y) [LOGICAL coords] -> in_settlement false.

REACHABILITY GATE (important): use the campaign_manager WRAPPER
`cm:character_can_reach_settlement(char, settlement)` -- CA wrote it because the raw model
predicate returns FALSE POSITIVES when the character has no action points.

Also proven dead here: `bus move` / cm:move_to CANNOT enter a settlement (CA docs say a different
order type is required) -- that is why garrison uses join_garrison.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

from cco_actions import _ev, register           # noqa: E402  (shared engine + eval helper)

_LOOKUP = "character_cqi:%s"


def _roots(bus):
    import nav
    try:
        return nav.visible_roots(bus)
    except Exception:
        return None                              # bus blocked (often a modal popup) -- caller decides


def _prebattle(bus):
    r = _roots(bus)
    if r is None:
        return "blocked"
    return "popup_pre_battle" in r


def _char_scalar(bus, cqi, expr):
    return _ev(bus, "local c=cm:get_character_by_cqi(%s); if c and not c:is_null_interface() "
                    "then return tostring(%s) end return 'no-char'" % (cqi, expr), timeout=8.0)


# ------------------------------------------------------------------ attack nearest enemy ARMY
def nearest_enemy_army(bus):
    """{cqi, faction, dist, x, y} of the closest at-war army, or None (bus `hostiles`)."""
    try:
        ho = (bus.send("hostiles", "", timeout=10) or {}).get("hostiles") or []
    except Exception as e:
        sys.stderr.write("cm_actions: hostiles -> %s\n" % repr(e)[:80])
        return None
    arms = [h for h in ho if h.get("kind") == "army" and h.get("cqi")]
    arms.sort(key=lambda h: h.get("dist") if h.get("dist") is not None else 9999)
    return arms[0] if arms else None


def _attack_army_snapshot(bus, ctx, pick):
    cqi = ctx["entity_id"]
    tgt = (pick.get("params") or {}).get("target_cqi")
    if tgt is None:
        return None
    return {"ap": _char_scalar(bus, cqi, "c:action_points_remaining_percent()"),
            "acted": _char_scalar(bus, cqi, "c:performed_action_this_turn()"),
            "target_alive": _ev(bus, "return tostring(cm:get_character_by_cqi(%s) ~= nil)" % tgt),
            "can_reach": _ev(bus, "local a=cm:get_character_by_cqi(%s); local t=cm:get_character_by_cqi(%s); "
                                  "local ok,v=pcall(function() return cm:character_can_reach_character(a,t) end); "
                                  "return tostring(ok and v)" % (cqi, tgt), timeout=10.0)}


def _attack_army_gate(bus, ctx, pick, before):
    if before.get("acted") == "true":
        return False, "already_acted_this_turn"
    if before.get("can_reach") != "true":
        return False, "cannot_reach_target"       # CA wrapper; also covers 0-AP false positives
    return True, None


def _attack_army_execute(bus, ctx, pick, before):
    tgt = (pick.get("params") or {})["target_cqi"]
    return _ev(bus, "local ok,e=pcall(function() cm:attack('%s','%s',false,true) end); "
                    "return 'ok='..tostring(ok)" % (_LOOKUP % ctx["entity_id"], _LOOKUP % tgt),
               timeout=20.0) == "ok=true"


def _attack_army_confirm(bus, ctx, pick, before):
    pb = _prebattle(bus)
    # a blocked bus here means a modal battle popup == the order landed
    return (pb is True or pb == "blocked"), {"pre_battle": pb}


register("attack_army", {
    "layer": "cm", "signal": "pre_battle_popup",
    "snapshot": _attack_army_snapshot, "gates": [_attack_army_gate],
    "execute": _attack_army_execute, "confirm": _attack_army_confirm,
    "timeout_s": 20.0, "poll_s": 2.0, "retryable": False, "max_per_entity_turn": 1,
})


# ------------------------------------------------------------- attack nearest enemy SETTLEMENT
def nearest_enemy_settlement(bus):
    """{region, faction, dist, x, y} of the closest at-war settlement, or None."""
    try:
        ho = (bus.send("hostiles", "", timeout=10) or {}).get("hostiles") or []
    except Exception as e:
        sys.stderr.write("cm_actions: hostiles -> %s\n" % repr(e)[:80])
        return None
    ss = [h for h in ho if h.get("kind") == "settlement" and h.get("region")]
    ss.sort(key=lambda h: h.get("dist") if h.get("dist") is not None else 9999)
    return ss[0] if ss else None


def _attack_sett_snapshot(bus, ctx, pick):
    cqi, region = ctx["entity_id"], pick["key"]
    return {"ap": _char_scalar(bus, cqi, "c:action_points_remaining_percent()"),
            "acted": _char_scalar(bus, cqi, "c:performed_action_this_turn()"),
            "besieging": _char_scalar(bus, cqi, "c:is_besieging()"),
            # CA WRAPPER, not the raw model call (raw false-positives at 0 AP)
            "can_reach": _ev(bus, "local c=cm:get_character_by_cqi(%s); local s=cm:get_region('%s'):settlement(); "
                                  "local ok,v=pcall(function() return cm:character_can_reach_settlement(c,s) end); "
                                  "return tostring(ok and v)" % (cqi, region), timeout=10.0)}


def _attack_sett_gate(bus, ctx, pick, before):
    if before.get("acted") == "true":
        return False, "already_acted_this_turn"
    if before.get("can_reach") != "true":
        return False, "cannot_reach_settlement"
    return True, None


def _attack_sett_execute(bus, ctx, pick, before):
    # EXACTLY TWO ARGUMENTS -- a third (WH2-era) argument makes this silently do nothing.
    return _ev(bus, "local ok,e=pcall(function() cm:attack_region('%s','%s') end); "
                    "return 'ok='..tostring(ok)" % (_LOOKUP % ctx["entity_id"], pick["key"]),
               timeout=20.0) == "ok=true"


def _attack_sett_confirm(bus, ctx, pick, before):
    pb = _prebattle(bus)
    if pb is True or pb == "blocked":
        return True, {"pre_battle": pb}
    bes = _char_scalar(bus, ctx["entity_id"], "c:is_besieging()")
    return (bes == "true" and before.get("besieging") != "true"), {"pre_battle": pb, "besieging": bes}


register("attack_settlement", {
    "layer": "cm", "signal": "pre_battle_or_is_besieging",
    "snapshot": _attack_sett_snapshot, "gates": [_attack_sett_gate],
    "execute": _attack_sett_execute, "confirm": _attack_sett_confirm,
    "timeout_s": 20.0, "poll_s": 2.0, "retryable": False, "max_per_entity_turn": 1,
})


# ------------------------------------------------------------------------------- GARRISON
def nearest_own_settlement(bus, cqi):
    """{region, x, y, dist} of the player's closest own settlement to the character (logical coords)."""
    try:
        setts = (bus.send("setts", "", timeout=10) or {}).get("setts") or []
        chars = (bus.send("chars", "", timeout=10) or {}).get("chars") or []
    except Exception as e:
        sys.stderr.write("cm_actions: setts/chars -> %s\n" % repr(e)[:80])
        return None
    me = next((c for c in chars if str(c.get("cqi")) == str(cqi)), None)
    if not me or not setts:
        return None
    best = min(setts, key=lambda s: ((s.get("x", 0) - me.get("x", 0)) ** 2 +
                                     (s.get("y", 0) - me.get("y", 0)) ** 2))
    return {"region": best.get("region"), "x": best.get("x"), "y": best.get("y")}


def _garrison_snapshot(bus, ctx, pick):
    return {"in_settlement": _char_scalar(bus, ctx["entity_id"], "c:in_settlement()"),
            "acted": _char_scalar(bus, ctx["entity_id"], "c:performed_action_this_turn()")}


def _garrison_gate(bus, ctx, pick, before):
    if before.get("in_settlement") == "true":
        return False, "already_in_settlement"
    return True, None


def _garrison_execute(bus, ctx, pick, before):
    # settlement key form is the settlement's own key(): "settlement:<region_key>"
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
    "timeout_s": 10.0, "poll_s": 1.5, "max_per_entity_turn": 1,
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
    "timeout_s": 10.0, "poll_s": 1.5, "max_per_entity_turn": 1,
})
