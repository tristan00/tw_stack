from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)

import diplomacy
from cco_actions import _ev, register

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
    "b(function() return me:diplomatic_standing_with('%(key)s') end)")


def _treaty(bus, faction_key):
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
    return (pick.get("params") or {}).get("faction")


def _terms(pick):
    return list((pick.get("params") or {}).get("terms") or [])


def _gift(pick):
    g = (pick.get("params") or {}).get("gift")
    if not g:
        return None
    tier = g.get("tier") if isinstance(g, dict) else g
    return str(tier) if tier in diplomacy.GIFT_TIERS else None


def _treasury(bus):
    raw = _ev(bus, "return cm:get_local_faction(true):treasury()", timeout=20.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _snapshot(bus, ctx, pick):
    key = _target(pick)
    return {"faction": key, "treaty": _treaty(bus, key) if key else None,
            "treasury": _treasury(bus) if _gift(pick) else None}


def _validate_pick(bus, ctx, pick, before):
    """Validation of the PICK, not of the game."""
    if not _target(pick):
        return False, "no_target_faction"
    terms = _terms(pick)
    if not terms and not _gift(pick):
        return False, "no_terms"
    if len(terms) > diplomacy.MAX_TERMS:
        return False, "too_many_terms"
    return True, None


def _emit_stream(key, terms, panel, before, ok, gift=None):
    try:
        import diplo_stream as DS
        DS.track(key)
        DS.emit("deal", channel="outgoing", faction=key, terms=terms, ok=ok, gift=gift,
                panel=panel, treaty_before=(before or {}).get("treaty"))
    except Exception as e:
        sys.stderr.write("diplomacy_actions: diplo_stream emit -> %s\n" % repr(e)[:80])


def _execute(bus, ctx, pick, before):
    key, terms, gift = _target(pick), _terms(pick), _gift(pick)
    try:
        out = diplomacy.propose(bus, key, terms, gift=gift)
    except diplomacy.DiplomacyError as e:
        panel = getattr(e, "panel", None)
        if panel is not None:
            pick.setdefault("params", {})["panel"] = panel
        sys.stderr.write("diplomacy_actions: %s -> %s (failed_at=%s)\n"
                         % (key, e, (panel or {}).get("failed_at")))
        _emit_stream(key, terms, panel, before, ok=False, gift=gift)
        return False
    except Exception:
        _emit_stream(key, terms, None, before, ok=False, gift=gift)
        raise
    pick.setdefault("params", {})["panel"] = out
    _emit_stream(key, terms, out, before, ok=True, gift=gift)
    g = out.get("gift") or {}
    sys.stderr.write("diplomacy: %s terms=%s gift=%s -> staged=%s sent=%s accepted=%s chance=%s "
                     "failed_at=%s gift_failed_at=%s gift_steps=%s\n"
                     % (key, terms, gift, out.get("staged"), out.get("sent"),
                        out.get("accepted"), out.get("success_chance"), out.get("failed_at"),
                        g.get("failed_at"),
                        {k: g.get(k) for k in ("opened", "offer_selected", "chosen",
                                               "committed", "backed_out")} if g else None))
    return True


def _confirm(bus, ctx, pick, before):
    panel = (pick.get("params") or {}).get("panel") or {}
    if panel and not panel.get("sent"):
        return False, {"reason": "never_sent", "failed_at": panel.get("failed_at"),
                       "staged": panel.get("staged"), "sendable": panel.get("sendable"),
                       "success_chance": panel.get("success_chance")}
    key = _target(pick)
    tier = _gift(pick)
    if tier:
        t0, t1 = before.get("treasury"), _treasury(bus)
        walk = dict(panel.get("gift") or {})
        if t0 is None or t1 is None:
            return False, {"reason": "treasury_unreadable", "signal": "treasury_dropped",
                           "before": t0, "after": t1, "walk": walk}
        return t1 < t0, {"signal": "treasury_dropped", "tier": tier,
                         "before": t0, "after": t1, "spent": t0 - t1, "walk": walk}
    after = _treaty(bus, key)
    if after is None:
        return False, {"reason": "treaty_state_unreadable_after"}
    b = before.get("treaty") or {}
    changed = [f for f in ("at_war", "allied", "trade", "our_master", "their_vassal")
               if b.get(f) != after.get(f)]
    return bool(changed), {"changed": changed, "before": b, "after": after}


def _doomed_unsent(bus, ctx, pick, before, after):
    panel = (pick.get("params") or {}).get("panel") or {}
    if not panel.get("sent"):
        return "deal_never_sent(failed_at=%s)" % panel.get("failed_at")
    return None


register("diplomacy", {
    "layer": "click", "signal": "treaty_changed",
    "snapshot": _snapshot, "prechecks": [_validate_pick], "execute": _execute, "confirm": _confirm,
    "doomed": _doomed_unsent,
    "timeout_s": 0.0, "poll_s": 0.0, "confirm_settle_s": 0.6,
    "retryable": False,
})
