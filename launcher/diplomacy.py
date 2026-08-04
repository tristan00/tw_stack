from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

import interrupts
import nav

ROOT = "diplomacy_dropdown"
BTN_DIPLOMACY = "hud_campaign|faction_buttons_docker|button_group_management|button_diplomacy"
OFFERS_PATH = ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
               "offers_list_panel|list_possible_actions")

TERMS = ("nonaggression_pact", "trade_agreement", "defensive_alliance", "soft_access",
         "military_alliance", "vassal", "confederation")
DECLARE_WAR = "declare_war"
MAX_TERMS = 1

PAYMENT_OPTION = "payment"
GIFT_TIERS = ("small", "medium", "large")
PAY_OFFER = "pay_offer"

_CLICKABLE = ("active", "hover", "selected")

POLL_STEP = 0.15


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
    """Resolve a node by id; the list moves under the cursor, so the result must not be cached."""
    for n in _tree(bus):
        if str(n.get("id") or "") == node_id:
            return n
    return None


FACTION_LIST = "sortable_list_factions"
SCROLL_ARROW = ("diplomacy_dropdown|faction_panel|faction_panel_top|"
                "sortable_list_factions|vslider|%s")


def _find(bus, node_id):
    try:
        r = bus.send("find", node_id, timeout=8.0) or {}
    except Exception:
        return {}
    return r.get("result") or {}


def _list_band(bus):
    """(y0, y1) of the faction list's clipping viewport."""
    for n in _tree(bus):
        p = str(_path(n) or "")
        if str(n.get("id") or "") == "list_clip" and FACTION_LIST in p:
            return int(n.get("y") or 0), int(n.get("y") or 0) + int(n.get("h") or 0)
    raise DiplomacyError("no %s list_clip in the panel -- is diplomacy open?" % FACTION_LIST)


def _row_pitch(bus):
    """Pixels between adjacent rows: one arrow click moves the list by exactly this."""
    ys = sorted(int(n.get("y") or 0) for n in _rows(bus))
    gaps = sorted({b - a for a, b in zip(ys, ys[1:]) if b > a})
    if not gaps:
        raise DiplomacyError("cannot measure the faction list's row pitch (%d rows)" % len(ys))
    return gaps[0]


def _settled_y(bus, row_id, limit=3.0, agree=3):
    """The row's y once `agree` consecutive reads return it unchanged (the list tweens)."""
    prev, n, t0 = None, 0, time.time()
    while time.time() - t0 < limit:
        cur = int(_find(bus, row_id).get("y") or 0)
        n = n + 1 if cur == prev else 0
        prev = cur
        if n >= agree - 1:
            return cur
    return prev if prev is not None else 0


def scroll_to_row(bus, row_id, margin=6):
    """Bring `row_id`'s centre into the list viewport in one exact burst of arrow clicks."""
    n = _find(bus, row_id)
    if not n.get("found"):
        raise DiplomacyError("no %s in the panel -- that faction is not deal-able right now"
                             % row_id)
    y0, y1 = _list_band(bus)
    h = int(n.get("h") or 44)
    centre = _settled_y(bus, row_id) + h // 2
    lo, hi = y0 + margin, y1 - margin
    if lo <= centre <= hi:
        return 0
    pitch = _row_pitch(bus)
    if centre > hi:
        clicks, arrow = -(-(centre - hi) // pitch), SCROLL_ARROW % "bottom"
    else:
        clicks, arrow = -(-(lo - centre) // pitch), SCROLL_ARROW % "top"
    for _ in range(clicks):
        bus.send("click", arrow, timeout=8.0)
    final = _settled_y(bus, row_id) + h // 2
    if not lo <= final <= hi:
        raise DiplomacyError("%d clicks of %spx left %s at centre %s, outside %s..%s -- the click "
                             "count is wrong, not the direction"
                             % (clicks, pitch, row_id, final, lo, hi))
    return clicks


def _hardware_click(node, double=False):
    nav.mouse("dclick" if double else "click",
              *nav.ui_to_screen(node["x"] + node["w"] / 2, node["y"] + node["h"] / 2))


def _text_of(bus, node_id):
    n = _node(bus, node_id)
    return str(n.get("text") or "").strip() if n else None


def success_chance(bus):
    """label_deal_success_chance as a signed float, or None."""
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
        if _node(bus, "cancel_payments"):
            sys.stderr.write("diplomacy: payments subpanel was covering the faction list -- "
                             "backing out of it (a previous walk died mid-payment)\n")
            cancel_payments(bus)
        if not _wait(lambda: bool(_rows(bus)), limit):
            raise DiplomacyError("panel open but no faction row ever rendered")
    return not already


def close_panel(bus, tries=3, limit=2.5):
    """Close the panel, asserting it is gone. Raises if it will not close.

    The payments subpanel has to be backed out first: while it is up the panel offers neither
    button_cancel nor button_close, so every dismissal path below is a no-op until it is gone."""
    for _ in range(tries):
        if ROOT not in interrupts.roots(bus):
            return True
        cancel_payments(bus)
        n = _node(bus, "button_cancel") or _node(bus, "button_close")
        if n and n.get("visible") and str(n.get("state")) in _CLICKABLE:
            _hardware_click(n)
        else:
            nav.close_popups(bus)
        if _wait(lambda: ROOT not in interrupts.roots(bus), limit):
            return True
    live = sorted({"%s(%s)" % (c.get("id"), c.get("state")) for c in _tree(bus)
                   if c.get("visible") and str(c.get("state") or "") in _CLICKABLE
                   and str(c.get("id") or "").startswith("button")})
    raise DiplomacyError(
        "diplomacy panel would not close after %d tries -- a leftover blocking screen kills the "
        "whole campaign, so this is fatal here rather than later. roots=%s clickable_buttons=%s"
        % (tries, interrupts.roots(bus), live[:20]))


def select_faction(bus, faction_key, limit=4.0):
    """Open the negotiation with one faction."""
    row_id = "faction_row_entry_%s" % faction_key
    scroll_to_row(bus, row_id)
    for attempt in range(3):
        n = _node(bus, row_id)
        if n is None or not _path(n):
            raise DiplomacyError("no %s in the panel -- that faction is not deal-able right now"
                                 % row_id)
        nav.bus_click(bus, _path(n))
        if _wait(lambda: str(_find(bus, row_id).get("state") or "") == "selected", 1.5, step=0.2):
            break
        sys.stderr.write("diplomacy: %s did not take the click (attempt %d)\n"
                         % (row_id, attempt + 1))
    else:
        raise DiplomacyError("%s never became selected after 3 bus clicks" % row_id)
    _hardware_click(_find(bus, row_id), double=True)
    if _wait(lambda: bool(offered_terms(bus)), limit):
        return True
    raise DiplomacyError("selected %s but no negotiation opened (no diplomatic_option_* appeared)"
                         % row_id)


def offered_terms(bus):
    """{term: state} for every diplomatic_option_* on screen."""
    out = {}
    for n in _tree(bus):
        nid = str(n.get("id") or "")
        if nid.startswith("diplomatic_option_") and n.get("visible"):
            out[nid[len("diplomatic_option_"):]] = str(n.get("state") or "")
    return out


def add_term(bus, term, limit=2.5):
    """Stage one term on the open offer. True if its state changed, False if `inactive`/absent.

    Every refusal names the term's state AND the full set the panel is actually offering -- the
    offered set is already in hand here, and discarding it is why a `deal_selection` failure used
    to say nothing about which terms were available for that faction."""
    node_id = "diplomatic_option_%s" % term
    offered = offered_terms(bus)
    before = offered.get(term)
    if before is None or before == "inactive":
        _trace("add_term %r refused: state=%r -- panel offers %s", term, before,
               {k: v for k, v in sorted(offered.items())})
        return False
    n = _node(bus, node_id)
    if n is None or not n.get("visible") or not _path(n):
        _trace("add_term %r: state=%r but the node is absent/invisible/pathless", term, before)
        return False
    nav.bus_click(bus, _path(n))
    ok = _wait(lambda: str(_find(bus, node_id).get("state") or "") not in ("", before), limit)
    if not ok:
        _trace("add_term %r clicked but state stayed %r -- panel offers %s", term, before,
               {k: v for k, v in sorted(offered.items())})
    return ok


def open_payments(bus, limit=4.0):
    """Click diplomatic_option_payment to raise the payments subpanel. False if it is not offered.

    The subpanel is what `payment_state` reads and what a gift is staged through; nothing about
    it is visible until this lands, so it must run before the tier buttons can be enumerated."""
    state = offered_terms(bus).get(PAYMENT_OPTION)
    if state is None or state == "inactive":
        return False
    n = _node(bus, "diplomatic_option_%s" % PAYMENT_OPTION)
    if n is None or not n.get("visible") or not _path(n):
        return False
    nav.bus_click(bus, _path(n))
    return _wait(lambda: bool(_node(bus, PAY_OFFER)), limit)


def _index(bus):
    """{id: node} from ONE tree scrape. Every _node call re-scrapes 80k nodes, so a reader that
    wants a dozen fields must not call it a dozen times."""
    idx = {}
    for n in _tree(bus):
        nid = str(n.get("id") or "")
        if nid and nid not in idx:
            idx[nid] = n
    return idx


def _num(node):
    raw = str((node or {}).get("text") or "").strip()
    if not raw:
        return None
    try:
        return float(raw.replace("%", "").replace("+", "").replace(",", "").strip())
    except ValueError:
        return None


def payment_state(bus):
    """Everything the OPEN payments subpanel exposes, read-only, in one scrape.

    A tier is available exactly when its own button is clickable; the game gates that on the
    treasury covering that tier's cost, so availability is read off the panel, never computed.
    `value` is the cost of the SELECTED tier only -- the panel shows one at a time, and the
    price is per-target, not a fixed per-tier constant."""
    idx = _index(bus)
    tiers = {}
    for t in GIFT_TIERS:
        n = idx.get("button_%s_gift" % t)
        tiers[t] = {"state": (str(n.get("state") or "") if n else None),
                    "available": bool(n and n.get("visible")
                                      and str(n.get("state") or "") in _CLICKABLE)}
    ok = idx.get("ok_payments")
    offer = idx.get(PAY_OFFER)
    label = idx.get("label_gift_type")
    return {"tiers": tiers,
            "offer_selected": bool(offer and str(offer.get("state") or "") == "selected"),
            "selected_tier": (str(label.get("text") or "").strip() if label else None),
            "selected_value": _num(idx.get("label_gift_value")),
            "treasury": _num(idx.get("dy_payments_treasury")),
            "success_chance": _num(idx.get("label_payment_success_chance")),
            "committable": bool(ok and str(ok.get("state") or "") in _CLICKABLE)}


TIER_LABELS = {"small": "small gift", "medium": "medium gift", "large": "generous gift"}


def _trace(fmt, *a):
    sys.stderr.write("diplomacy: " + (fmt % a if a else fmt) + "\n")


def selected_tier(bus):
    """Which gift tier the panel currently has selected, read from its own label_gift_type.

    The label does NOT track the button id: button_large_gift displays 'Generous gift', so the
    mapping is explicit. TIER_LABELS was read off ONE campaign, so any wording outside it is
    logged loudly rather than silently returning None -- an unrecognised label is why a valid
    gift gets cancelled, and it must name the string it saw."""
    raw = str(_text_of(bus, "label_gift_type") or "").strip().lower()
    for tier, label in TIER_LABELS.items():
        if raw == label:
            return tier
    _trace("UNKNOWN gift label %r -- not in %s; selected_tier cannot resolve it and any verify "
           "against it will fail", raw, sorted(TIER_LABELS.values()))
    return None


def _committable(bus):
    """True when ok_payments is live -- the panel's own verdict that a payment is staged."""
    return str(_find(bus, "ok_payments").get("state") or "") in _CLICKABLE


def select_offer(bus, limit=2.0):
    """Select Offer Payment. True once it reads back `selected`.

    Required before the tier buttons: the panel needs a direction before a gift size takes."""
    n = _node(bus, PAY_OFFER)
    if n is None or not n.get("visible"):
        _trace("select_offer: %s absent/invisible", PAY_OFFER)
        return False
    if str(n.get("state") or "") == "selected":
        return True
    if not _path(n):
        _trace("select_offer: %s has no path", PAY_OFFER)
        return False
    nav.bus_click(bus, _path(n))
    ok = _wait(lambda: str(_find(bus, PAY_OFFER).get("state") or "") == "selected", limit)
    if not ok:
        _trace("select_offer: %s stayed %r after the click", PAY_OFFER,
               _find(bus, PAY_OFFER).get("state"))
    return ok


def choose_gift(bus, tier, limit=2.0, ready=2.5):
    """Click one gift tier. False when that tier stays unclickable -- treasury below its price.

    The tier buttons are enabled a beat AFTER the direction is applied, so read them only once
    the requested one has settled: sampling immediately reports every tier unavailable and turns
    an affordable gift into a spurious refusal."""
    if tier not in GIFT_TIERS:
        raise DiplomacyError("unknown gift tier %r -- want one of %s" % (tier, list(GIFT_TIERS)))
    node_id = "button_%s_gift" % tier
    _wait(lambda: bool(_node(bus, node_id)), ready)
    n = _node(bus, node_id)
    if n is None or not n.get("visible") or not _path(n):
        return False
    cur = selected_tier(bus)
    if cur == tier and _committable(bus):
        _trace("gift %s: already selected AND committable -- no click needed", tier)
        return True
    _trace("gift %s: clicking %s (currently selected=%r, committable=%s)",
           tier, node_id, cur, _committable(bus))
    nav.bus_click(bus, _path(n))
    ok = _wait(lambda: selected_tier(bus) == tier and _committable(bus), limit)
    if not ok:
        _trace("gift %s: after clicking %s the panel reads selected_tier=%r committable=%s "
               "-- neither settled within %ss", tier, node_id, selected_tier(bus),
               _committable(bus), limit)
    return ok


def commit_payment(bus, limit=3.0):
    """Press ok_payments to put the staged payment on the offer table."""
    n = _node(bus, "ok_payments")
    if n is None or not n.get("visible") or not _path(n):
        _trace("commit_payment: ok_payments absent/invisible (node=%s)", n is not None)
        return False
    if str(n.get("state") or "") not in _CLICKABLE:
        _trace("commit_payment: ok_payments state=%r not clickable -- nothing is staged",
               n.get("state"))
        return False
    nav.bus_click(bus, _path(n))
    ok = _wait(lambda: not _find(bus, "ok_payments").get("visible"), limit, step=0.2)
    if not ok:
        _trace("commit_payment: clicked ok_payments but the subpanel is still up")
    return ok


def cancel_payments(bus, limit=2.5):
    """Back out of the payments subpanel. True once it is gone (or was never up).

    Every failure inside the subpanel exits through here: leaving it up hides the faction list,
    which wedges close_panel AND the next diplomacy action, whatever went wrong."""
    n = _node(bus, "cancel_payments")
    if n is None or not n.get("visible"):
        return True
    if str(n.get("state") or "") not in _CLICKABLE or not _path(n):
        return False
    nav.bus_click(bus, _path(n))
    return _wait(lambda: not _find(bus, "cancel_payments").get("visible"), limit, step=0.2)


def stage_gift(bus, tier):
    """The payments walk, in the panel's required order: raise the subpanel, choose Offer Payment,
    pick the tier, commit. Returns what each step did plus the subpanel's own readouts.

    Every early return backs out of the subpanel first, so a refusal at any step leaves the panel
    on the negotiation view rather than on a screen with no exit."""
    out = {"tier": tier, "opened": False, "offer_selected": False, "chosen": False,
           "committed": False, "panel": None, "failed_at": None, "backed_out": None}
    _trace("gift %s: walk start", tier)

    def bail(where):
        out["failed_at"] = where
        out["panel"] = payment_state(bus)
        sys.stderr.write("diplomacy: gift %s FAILED at %s -- selected_tier=%r tiers=%s "
                         "treasury=%s value=%s committable=%s\n"
                         % (tier, where, selected_tier(bus),
                            {t: v.get("state") for t, v in (out["panel"]["tiers"] or {}).items()},
                            out["panel"].get("treasury"), out["panel"].get("selected_value"),
                            out["panel"].get("committable")))
        out["backed_out"] = cancel_payments(bus)
        return out

    if not open_payments(bus):
        out["failed_at"] = "payment_option_unavailable"
        sys.stderr.write("diplomacy: gift %s FAILED at payment_option_unavailable -- "
                         "diplomatic_option_payment state=%r offered=%s\n"
                         % (tier, offered_terms(bus).get(PAYMENT_OPTION),
                            sorted(offered_terms(bus))))
        out["backed_out"] = cancel_payments(bus)
        return out
    out["opened"] = True
    _trace("gift %s: payments subpanel open", tier)
    if not select_offer(bus):
        return bail("offer_direction")
    out["offer_selected"] = True
    out["panel"] = payment_state(bus)
    _trace("gift %s: offer selected -- tiers=%s selected_tier=%r treasury=%s value=%s "
           "committable=%s", tier,
           {t: v.get("state") for t, v in (out["panel"]["tiers"] or {}).items()},
           out["panel"].get("selected_tier"), out["panel"].get("treasury"),
           out["panel"].get("selected_value"), out["panel"].get("committable"))
    if not choose_gift(bus, tier):
        return bail("tier_not_selected")
    out["chosen"] = True
    out["panel"] = payment_state(bus)
    _trace("gift %s: tier chosen -- selected_tier=%r value=%s committable=%s", tier,
           out["panel"].get("selected_tier"), out["panel"].get("selected_value"),
           out["panel"].get("committable"))
    if not commit_payment(bus):
        return bail("commit")
    out["committed"] = True
    _trace("gift %s: committed to the offer table", tier)
    return out


def prepare(bus, faction_key, terms, gift=None):
    """Open a negotiation, stage `terms`, and stage `gift` through the payments subpanel.
    Sends nothing. Returns what reached the table."""
    terms = list(terms)[:MAX_TERMS]
    open_panel(bus)
    carried = success_chance(bus)
    select_faction(bus, faction_key)
    staged, rejected = [], []
    for t in terms:
        (staged if add_term(bus, t) else rejected).append(t)
    gift_out = None
    if gift:
        gift_out = stage_gift(bus, gift.get("tier") if isinstance(gift, dict) else gift)
        if gift_out.get("committed"):
            staged.append("gift_%s" % gift_out["tier"])
        else:
            rejected.append("gift_%s" % gift_out["tier"])
    chance = _find(bus, "label_deal_success_chance").get("text") if staged else None
    return {"faction": faction_key, "requested": terms, "staged": staged,
            "unavailable": rejected, "gift": gift_out,
            "success_chance": (str(chance).strip() or None) if chance is not None else None,
            "chance_carried_in": carried,
            "sendable": _sendable(bus)}


def _sendable(bus):
    n = _node(bus, "button_send")
    return bool(n and n.get("visible") and str(n.get("state")) in _CLICKABLE)


RESPONSE_ACK = "button_accept"


def offer_response(bus):
    """The AI's answer on the post-send response screen, or None when it is not up.

    `dy_text` carries the verdict in its STATE (they_accepted / they_declined ...), which is a
    far cleaner settle signal than inferring one from treasury movement."""
    for n in _tree(bus):
        if str(n.get("id") or "") != "dy_text" or not n.get("visible"):
            continue
        st = str(n.get("state") or "")
        if st.startswith("they_"):
            return {"answer": st, "accepted": st == "they_accepted",
                    "text": str(n.get("text") or "").strip()}
    return None


_OFFERS = ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
           "offers_list_panel|button_set_holder|%s|%s")
EXIT_AFTER_SEND = (
    (_OFFERS % ("button_set3", "button_accept"), "button_accept"),
    (_OFFERS % ("button_set1", "button_cancel"), "button_cancel"),
    ("diplomacy_dropdown|faction_panel|faction_panel_bottom|both_buttongroup|button_cancel",
     "button_cancel"),
)


def finish_after_send(bus, limit=2.0):
    """The ONLY way out after a send: three clicks, by full path, in order.

    MEASURED, not inferred: acknowledging alone leaves the panel up (root still present after 3s).
    Addressed by path because the panel carries several button_cancel nodes and a bare-id lookup
    returns whichever the tree yields first. Each step asserts the node it actually landed on and
    reads the panel's disappearance out of the click reply's own roots_after."""
    for i, (path, want_id) in enumerate(EXIT_AFTER_SEND):
        if ROOT not in interrupts.roots(bus):
            _trace("post-send exit: panel already gone after %d step(s)", i)
            return True
        r = bus.send("click", path, timeout=8.0) or {}
        if not r.get("found") or r.get("id") != want_id or not r.get("clicked"):
            _trace("post-send exit step %d wanted %s, got found=%s id=%r clicked=%s state=%r "
                   "-- %s", i + 1, want_id, r.get("found"), r.get("id"), r.get("clicked"),
                   r.get("state_before"), path)
            return False
        _trace("post-send exit step %d: clicked %s (%s -> %s)", i + 1, want_id,
               r.get("state_before"), r.get("state_after"))
        if ROOT not in (r.get("roots_after") or []):
            return True
    gone = _wait(lambda: ROOT not in interrupts.roots(bus), limit, step=0.1)
    if not gone:
        _trace("post-send exit: all %d steps clicked but %s is STILL open",
               len(EXIT_AFTER_SEND), ROOT)
    return gone


def send(bus):
    """Press Send. True if the button was live and pressed."""
    n = _node(bus, "button_send")
    if not (n and n.get("visible") and str(n.get("state")) in _CLICKABLE):
        return False
    _hardware_click(n)
    _wait(lambda: not _find(bus, "button_send").get("visible"), 2.5, step=0.2)
    return True


def propose(bus, faction_key, terms, gift=None):
    """Stage terms and any gift, send if the game allows, close, report what the panel did."""
    out = {"faction": faction_key, "requested": list(terms)[:MAX_TERMS],
           "stage": "open", "ok": False, "failed_at": None}
    try:
        out["stage"] = "prepare"
        out.update(prepare(bus, faction_key, terms, gift=gift))
        if not out["staged"] and not out.get("sendable"):
            out["failed_at"] = "deal_selection"
            return out
        if not out["staged"]:
            sys.stderr.write("diplomacy: our bookkeeping staged nothing but the panel says the "
                             "deal is SENDABLE -- trusting the panel and sending\n")
            out["staged_by_panel_only"] = True
        if not out.get("sendable"):
            out["failed_at"] = "ai_would_refuse"
            out["sent"] = False
            out["refused_by_ai"] = True
            return out
        out["stage"] = "send"
        out["sent"] = send(bus)
        out["refused_by_ai"] = not out["sent"]
        out["ok"] = bool(out["sent"])
        if not out["sent"]:
            out["failed_at"] = "send_refused"
            return out
        out["stage"] = "response"
        _wait(lambda: offer_response(bus) is not None, 6.0, step=0.25)
        out["response"] = offer_response(bus)
        if out["response"] is not None:
            out["accepted"] = bool(out["response"].get("accepted"))
        out["exited"] = finish_after_send(bus)
        return out
    except DiplomacyError as e:
        out["failed_at"] = "faction_selection" if out["stage"] == "prepare" else out["stage"]
        out["error"] = str(e)[:200]
        e.panel = dict(out)
        raise
    finally:
        out["stage"] = "closed"
        try:
            close_panel(bus)
        except DiplomacyError as ce:
            ce.panel = dict(out)
            raise
