r"""diplomacy_actions.py -- the `diplomacy` action type: propose a deal, confirm on treaty state."""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

import diplomacy                                              # noqa: E402
from cco_actions import _ev, register                         # noqa: E402

_LUA_TREATY = (
    "local me=cm:get_faction(cm:get_local_faction_name(true)) "
    "local o=cm:get_faction('%(key)s') "
    "if not me or me:is_null_interface() or not o or o:is_null_interface() then return 'nil' end "
    "local function b(f) local ok,v=pcall(f) if not ok then return 'nil' end "
    "  return tostring(v) end "
    "return b(function() return me:at_war_with(o) end)..'||'.."
    "b(function() return me:allied_with(o) end)..'||'.."
    "b(function() return me:trade_agreement_with(o) end)..'||'.."
    "b(function() return me:is_vassal_of(o) end)..'||'.."
    "b(function() return o:is_vassal_of(me) end)..'||'.."
    "b(function() return me:diplomatic_standing_with(o) end)")


def _treaty(bus, faction_key):
    """{at_war, allied, trade, our_master, their_vassal, standing} or None if unreadable."""
    raw = _ev(bus, _LUA_TREATY % {"key": faction_key}, timeout=20.0)
    if not raw or str(raw) == "nil":
        return None
    p = str(raw).split("||")
    if len(p) < 6:
        return None

    def _b(v):
        return True if v == "true" else (False if v == "false" else None)

    try:
        standing = float(p[5])
    except (TypeError, ValueError):
        standing = None
    return {"at_war": _b(p[0]), "allied": _b(p[1]), "trade": _b(p[2]),
            "our_master": _b(p[3]), "their_vassal": _b(p[4]), "standing": standing}


def _target(pick):
    """The faction key this action is aimed at."""
    return (pick.get("params") or {}).get("faction")


def _terms(pick):
    return list((pick.get("params") or {}).get("terms") or [])


def _snapshot(bus, ctx, pick):
    key = _target(pick)
    return {"faction": key, "treaty": _treaty(bus, key) if key else None}


def _gate(bus, ctx, pick, before):
    key = _target(pick)
    if not key:
        return False, "no_target_faction"
    terms = _terms(pick)
    if not terms:
        return False, "no_terms"
    if len(terms) > diplomacy.MAX_TERMS:
        return False, "too_many_terms"
    t = before.get("treaty")
    if t is None:
        return False, "treaty_state_unreadable"
    if t.get("allied") and "military_alliance" in terms:
        return False, "already_allied"
    if t.get("trade") and "trade_agreement" in terms:
        return False, "already_trading"
    if t.get("at_war") and diplomacy.DECLARE_WAR in terms:
        return False, "already_at_war"
    if t.get("our_master") or t.get("their_vassal"):
        if "vassal" in terms or "confederation" in terms:
            return False, "already_in_vassalage"
    return True, None


def _execute(bus, ctx, pick, before):
    key, terms = _target(pick), _terms(pick)
    try:
        out = diplomacy.propose(bus, key, terms)
    except diplomacy.DiplomacyError as e:
        panel = getattr(e, "panel", None)
        if panel is not None:
            pick.setdefault("params", {})["panel"] = panel
        sys.stderr.write("diplomacy_actions: %s -> %s (failed_at=%s)\n"
                         % (key, e, (panel or {}).get("failed_at")))
        return False
    pick.setdefault("params", {})["panel"] = out
    sys.stderr.write("diplomacy: %s %s -> staged=%s sent=%s chance=%s failed_at=%s\n"
                     % (key, terms, out.get("staged"), out.get("sent"),
                        out.get("success_chance"), out.get("failed_at")))
    return True


def _confirm(bus, ctx, pick, before):
    """(changed, evidence) from the treaty tuple. `standing` alone does not count as a change."""
    panel = (pick.get("params") or {}).get("panel") or {}
    if panel and not panel.get("sent"):
        return False, {"reason": "never_sent", "failed_at": panel.get("failed_at"),
                       "staged": panel.get("staged"), "sendable": panel.get("sendable"),
                       "success_chance": panel.get("success_chance")}
    key = _target(pick)
    after = _treaty(bus, key)
    if after is None:
        return False, {"reason": "treaty_state_unreadable_after"}
    b = before.get("treaty") or {}
    changed = [f for f in ("at_war", "allied", "trade", "our_master", "their_vassal")
               if b.get(f) != after.get(f)]
    return bool(changed), {"changed": changed, "before": b, "after": after}


register("diplomacy", {
    "layer": "click", "signal": "treaty_changed",
    "snapshot": _snapshot, "gates": [_gate], "execute": _execute, "confirm": _confirm,
    "timeout_s": 20.0, "poll_s": 2.0,
    "retryable": False,
})
