r"""interrupts.py -- every screen that appears WITHOUT the advisor asking for it.

Most of these land between turns, while the AI factions play: a defensive battle, a diplomacy offer
from someone who just declared on us, an event popup. None of them are advisor decisions yet, so
the loop's job is simply to get past them without stranding the run on a screen nobody drives.

    BATTLE          popup_pre_battle -> autoresolve -> popup_battle_results -> captives/occupation
    DIPLOMACY       an incoming proposal -> DECLINE. Never accept: accepting silently rewrites the
                    campaign (wars, treaties, payments) in ways the advisor never chose, and
                    diplomacy is explicitly out of scope for this version.
    EVENT POPUPS    generic dismiss buttons (nav.close_popups)

⚠ THE HARD RULE: the manual-battle entries (button_attack / button_spectate) are NEVER clicked.
Autoresolve only -- a testing agent dropped into a real-time battle is unrecoverable without a human.

⚠ KNOWN LIMIT, deliberately not papered over: a MODAL popup pauses the game tick, and the mod runs
on that tick, so the bus stops answering entirely. Nothing here can see or clear those. That case is
the watchdog's: no progress -> screenshot -> abandon the run. Blind clicking at fixed coordinates was
tried and rejected -- an unverified rescue is worse than a clean restart.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

import nav                                                  # noqa: E402

_BR = "popup_battle_results|mid|battle_results|post_battle_results_panel"
BATTLE_ROOTS = ("popup_pre_battle", "popup_battle_results", "settlement_captured")
CAPTIVE_PREFERENCE = ("release", "enslave", "kill")
OCCUPY_PREFERENCE = ("occupy", "loot & occupy", "sack", "raze")
# an incoming proposal is refused by whichever of these the panel actually shows
DECLINE_TOKENS = ("decline", "reject", "refuse", "cancel", "close", "no_deal")
_CLICKABLE = ("active", "hover", "selected")
# ⚠ `diplomacy_dropdown` is a PERSISTENT HUD root, present on a perfectly clean screen (verified
# live on turn 1 of a fresh campaign). Matching "diplo" alone would flag every single loop iteration
# as an incoming proposal and fire a decline click at nothing.
DIPLOMACY_HUD_ROOTS = frozenset(("diplomacy_dropdown",))


def roots(bus):
    try:
        return nav.visible_roots(bus)
    except Exception as e:
        sys.stderr.write("interrupts: roots -> %s\n" % repr(e)[:90])
        return []


def _wait_root(bus, root, tries=30, pause=1.0):
    for _ in range(tries):
        if root in roots(bus):
            return True
        time.sleep(pause)
    return False


def _click(bus, path, settle=1.5):
    try:
        r = bus.send("click", path, timeout=10.0) or {}
    except Exception as e:
        sys.stderr.write("interrupts: click %s -> %s\n" % (path.rsplit("|", 1)[-1], repr(e)[:70]))
        return False
    time.sleep(settle)
    return bool(r.get("clicked"))


def _found(bus, path):
    try:
        r = bus.send("find", path, timeout=8.0) or {}
        return bool((r.get("result") or {}).get("found"))
    except Exception:
        return False


def _tree(bus, root, depth=22, nodes=4000):
    try:
        return (bus.send("tree", "%s %d %d" % (root, depth, nodes), timeout=20.0) or {}).get("nodes") or []
    except Exception as e:
        sys.stderr.write("interrupts: tree %s -> %s\n" % (root, repr(e)[:80]))
        return []


# --------------------------------------------------------------------------------- detection
def diplomacy_roots(bus, r=None):
    """Open diplomacy roots that are an actual PROPOSAL, excluding the persistent HUD dropdown."""
    r = roots(bus) if r is None else r
    return [x for x in r if "diplo" in str(x).lower() and x not in DIPLOMACY_HUD_ROOTS]


def pending(bus):
    """Which interrupt kinds are on screen right now: {'battle', 'diplomacy', 'popup'} subset."""
    r = roots(bus)
    out = set()
    if any(x in r for x in BATTLE_ROOTS):
        out.add("battle")
    if diplomacy_roots(bus, r):
        out.add("diplomacy")
    if [x for x in r if x not in nav.BASE_ROOTS and x not in DIPLOMACY_HUD_ROOTS]:
        out.add("popup")
    return out


def in_battle(bus):
    return "battle" in pending(bus)


# --------------------------------------------------------------------------------- battle
def autoresolve(bus):
    """Click autoresolve in the open pre-battle. NEVER touches the manual-battle buttons."""
    n = nav.find_rect(bus, "popup_pre_battle", "button_autoresolve")
    if not n or n.get("x") is None:
        sys.stderr.write("interrupts: button_autoresolve not found in popup_pre_battle\n")
        return False
    sx, sy = nav.ui_to_screen(n["x"] + n["w"] / 2.0, n["y"] + n["h"] / 2.0)
    nav.mouse("move", sx, sy)
    time.sleep(0.3)
    nav.mouse("click", sx, sy)
    return _wait_root(bus, "popup_battle_results", tries=30, pause=1.0)


def handle_results(bus):
    """Take whatever post-battle option is on screen. Returns the steps performed."""
    steps = []
    cap = "%s|button_set_settlement_captured|button_accept" % _BR
    if _found(bus, cap) and _click(bus, cap, settle=2.5):
        steps.append("settlement_captured_checkmark")
    for choice in CAPTIVE_PREFERENCE:
        p = "%s|button_set_win_holder|button_set_win|button_captive_option_%s" % (_BR, choice)
        if _found(bus, p):
            if _click(bus, p):
                steps.append("captives:%s" % choice)
            break
    if "settlement_captured" in roots(bus):
        steps.append("occupation:%s" % (occupy(bus) or "none"))
    return steps


def occupy(bus):
    """Click the first PRESENT occupation option, preferring Occupy. Returns the label clicked."""
    opts = {}
    for n in _tree(bus, "settlement_captured", 22, 3000):
        if n.get("id") == "dy_option" and n.get("text"):
            opts[str(n["text"]).strip().lower()] = str(n["path"]).rsplit("|dy_option", 1)[0]
    if not opts:
        sys.stderr.write("interrupts: settlement_captured has no dy_option nodes\n")
        return None
    for want in OCCUPY_PREFERENCE:
        if want in opts:
            return want if _click(bus, opts[want] + "|option_button", settle=2.5) else None
    label, path = next(iter(opts.items()))
    return label if _click(bus, path + "|option_button", settle=2.5) else None


def resolve_battle(bus, max_rounds=4):
    steps = []
    for _ in range(max_rounds):
        r = roots(bus)
        if "popup_pre_battle" in r:
            steps.append("autoresolve:%s" % autoresolve(bus))
            continue
        if "popup_battle_results" in r or "settlement_captured" in r:
            steps.extend(handle_results(bus))
            time.sleep(1.5)
            continue
        break
    return steps


# --------------------------------------------------------------------------------- diplomacy
def decline_diplomacy(bus):
    """Refuse an incoming proposal by clicking whichever decline/close control the panel shows.

    Resolved by SCANNING the open diplomacy root for a clickable button whose id carries a decline
    token -- not by a hardcoded path, because the panel differs by proposal type. Post-asserted:
    the root has to be gone afterwards, or this reports failure.
    """
    steps = []
    for root in diplomacy_roots(bus):
        target = None
        for n in _tree(bus, root):
            nid = str(n.get("id") or "").lower()
            if (n.get("visible") and str(n.get("state")) in _CLICKABLE
                    and any(tok in nid for tok in DECLINE_TOKENS)):
                target = n.get("path")
                break
        if target and _click(bus, target, settle=2.0):
            steps.append("diplomacy_declined:%s" % target.rsplit("|", 1)[-1])
        else:
            sys.stderr.write("interrupts: no clickable decline control in %s\n" % root)
        if root in roots(bus):                       # post-assert: it must actually be gone
            sys.stderr.write("interrupts: diplomacy root %s still open after decline\n" % root)
            steps.append("diplomacy_stuck:%s" % root)
    return steps


# --------------------------------------------------------------------------------- everything
def resolve(bus, max_rounds=6):
    """Clear every interrupt currently on screen. Returns the steps taken ([] = clean screen)."""
    steps = []
    for _ in range(max_rounds):
        kinds = pending(bus)
        if not kinds:
            break
        if "battle" in kinds:
            steps.extend(resolve_battle(bus))
            continue
        if "diplomacy" in kinds:
            steps.extend(decline_diplomacy(bus))
            continue
        try:
            n = len(nav.close_popups(bus))
        except Exception as e:
            sys.stderr.write("interrupts: close_popups -> %s\n" % repr(e)[:80])
            break
        if not n:
            break                                    # something is open that we cannot dismiss
        steps.append("popups_cleared:%d" % n)
    return steps
