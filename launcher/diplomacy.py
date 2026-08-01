from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

import interrupts                                             # noqa: E402
import nav                                                    # noqa: E402

ROOT = "diplomacy_dropdown"
BTN_DIPLOMACY = "hud_campaign|faction_buttons_docker|button_group_management|button_diplomacy"
OFFERS_PATH = ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
               "offers_list_panel|list_possible_actions")

# the deal terms the advisor may propose
TERMS = ("nonaggression_pact", "trade_agreement", "defensive_alliance", "soft_access",
         "military_alliance", "vassal", "confederation")
DECLARE_WAR = "declare_war"
MAX_TERMS = 2

_CLICKABLE = ("active", "hover", "selected")

POLL_STEP = 0.15                              # seconds


def _wait(pred, limit, step=POLL_STEP):
    """Poll a predicate. True if it came true inside `limit`."""
    t0 = time.time()
    while time.time() - t0 < limit:
        if pred():
            return True
        time.sleep(step)
    return False


def _rows(bus):
    return [n for n in _tree(bus)
            if str(n.get("id") or "").startswith("faction_row_entry_") and n.get("visible")]


def _path(n):
    return (n or {}).get("path") or (n or {}).get("full_path")


class DiplomacyError(RuntimeError):
    """The diplomacy panel did not behave as expected."""


def _tree(bus):
    return interrupts._tree(bus, ROOT, 30, 80000)


def _node(bus, node_id):
    """Resolve a node by id. Re-read before every click; do not cache the result."""
    for n in _tree(bus):
        if str(n.get("id") or "") == node_id:
            return n
    return None


def _hardware_click(node, double=False):
    nav.mouse("dclick" if double else "click",
              *nav.ui_to_screen(node["x"] + node["w"] / 2, node["y"] + node["h"] / 2))


def _text_of(bus, node_id):
    n = _node(bus, node_id)
    return str(n.get("text") or "").strip() if n else None


def success_chance(bus):
    """label_deal_success_chance as a signed float, or None. Use chance_after_change to record it."""
    raw = _text_of(bus, "label_deal_success_chance")
    if raw is None:
        return None
    try:
        return float(raw.replace("%", "").replace("+", "").strip())
    except ValueError:
        return None


def chance_after_change(bus, before, limit=4.0):
    """success_chance once it has moved off `before`; None if it never moves."""
    out = {}

    def moved():
        cur = success_chance(bus)
        out["v"] = cur
        return cur is not None and cur != before

    return out.get("v") if _wait(moved, limit) else None


def dealable_factions(bus):
    """Faction keys we can open a negotiation with, read from the panel's own row list."""
    opened = open_panel(bus)
    try:
        keys, seen = [], set()
        for n in _tree(bus):
            nid = str(n.get("id") or "")
            if nid.startswith("faction_row_entry_") and n.get("visible"):
                key = nid[len("faction_row_entry_"):]
                if key and key not in seen:
                    seen.add(key)
                    keys.append(key)
        sys.stderr.write("diplomacy: %d dealable faction(s) read from the panel\n" % len(keys))
        return keys
    finally:
        if opened:
            close_panel(bus)


def open_panel(bus, limit=10.0):
    """Open the diplomacy panel and wait for a faction row to render. True if this call opened it."""
    already = ROOT in interrupts.roots(bus)
    if not already:
        nav.bus_click(bus, BTN_DIPLOMACY)
        if not _wait(lambda: ROOT in interrupts.roots(bus), limit):
            raise DiplomacyError("diplomacy panel never opened (roots=%s)" % interrupts.roots(bus))
    if not _wait(lambda: bool(_rows(bus)), limit):
        raise DiplomacyError("panel open but no faction row ever rendered")
    return not already


def close_panel(bus, tries=3, limit=2.5):
    """Close the panel, asserting it is gone. Raises if it will not close."""
    for _ in range(tries):
        if ROOT not in interrupts.roots(bus):
            return True
        n = _node(bus, "button_cancel") or _node(bus, "button_close")
        if n and n.get("visible") and str(n.get("state")) in _CLICKABLE:
            _hardware_click(n)
        else:
            nav.close_popups(bus)
        if _wait(lambda: ROOT not in interrupts.roots(bus), limit):
            return True
    raise DiplomacyError("diplomacy panel would not close (roots=%s)" % interrupts.roots(bus))


def select_faction(bus, faction_key, limit=4.0):
    """Open the negotiation with one faction. Hardware double-click -- a bus click will not do it."""
    row_id = "faction_row_entry_%s" % faction_key
    n = _node(bus, row_id)
    if n is None:
        raise DiplomacyError("no %s in the panel -- that faction is not deal-able right now" % row_id)
    if not n.get("visible"):
        raise DiplomacyError("%s is present but not visible (scrolled out of view)" % row_id)
    _hardware_click(n, double=True)
    if _wait(lambda: bool(offered_terms(bus)), limit):
        return True
    raise DiplomacyError("double-clicked %s but no negotiation opened (no diplomatic_option_* "
                         "appeared)" % row_id)


def offered_terms(bus):
    """{term: state} for every diplomatic_option_* on screen. Re-read between clicks."""
    out = {}
    for n in _tree(bus):
        nid = str(n.get("id") or "")
        if nid.startswith("diplomatic_option_") and n.get("visible"):
            out[nid[len("diplomatic_option_"):]] = str(n.get("state") or "")
    return out


def add_term(bus, term, limit=2.5):
    """Stage one term on the open offer. True if its state changed, False if `inactive`/absent."""
    node_id = "diplomatic_option_%s" % term
    before = offered_terms(bus).get(term)
    if before is None or before == "inactive":
        return False
    n = _node(bus, node_id)                       # resolved immediately before the click
    if n is None or not n.get("visible") or not _path(n):
        return False
    nav.bus_click(bus, _path(n))
    return _wait(lambda: offered_terms(bus).get(term) != before, limit)


def prepare(bus, faction_key, terms):
    """Open a negotiation and stage `terms`. Sends nothing. Returns what reached the table."""
    terms = list(terms)[:MAX_TERMS]
    open_panel(bus)
    carried = success_chance(bus)                 # read before selecting: may be stale
    select_faction(bus, faction_key)
    staged, rejected = [], []
    for t in terms:
        (staged if add_term(bus, t) else rejected).append(t)
    return {"faction": faction_key, "requested": terms, "staged": staged,
            "unavailable": rejected,
            "success_chance": chance_after_change(bus, carried, limit=1.0) if staged else None,
            "chance_carried_in": carried,
            "sendable": _sendable(bus)}


def _sendable(bus):
    n = _node(bus, "button_send")
    return bool(n and n.get("visible") and str(n.get("state")) in _CLICKABLE)


def send(bus):
    """Press Send. True if the button was live and pressed. Never retry an inactive Send."""
    n = _node(bus, "button_send")
    if not (n and n.get("visible") and str(n.get("state")) in _CLICKABLE):
        return False
    _hardware_click(n)
    time.sleep(2.5)
    return True


def propose(bus, faction_key, terms):
    """Stage terms, send if the game allows, close, report what the panel did."""
    out = {"faction": faction_key, "requested": list(terms)[:MAX_TERMS],
           "stage": "open", "ok": False, "failed_at": None}
    try:
        # prepare() must stay inside the try so the finally always closes the panel
        out["stage"] = "prepare"
        out.update(prepare(bus, faction_key, terms))
        if not out["staged"]:
            out["failed_at"] = "deal_selection"
            return out
        out["stage"] = "send"
        out["sent"] = send(bus)
        out["refused_by_ai"] = not out["sent"]
        out["ok"] = bool(out["sent"])
        if not out["sent"]:
            out["failed_at"] = "send_refused"
        return out
    except DiplomacyError as e:
        out["failed_at"] = "faction_selection" if out["stage"] == "prepare" else out["stage"]
        out["error"] = str(e)[:200]
        e.panel = dict(out)                       # the caller reads `sent` off the exception
        raise
    finally:
        out["stage"] = "closed"
        try:
            close_panel(bus)
        except DiplomacyError as ce:
            ce.panel = dict(out)
            raise
