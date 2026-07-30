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


CLICK_LOG = []          # (ts, path, clicked) for every click this module made -- read by the loop


def _click(bus, path, settle=1.5):
    try:
        r = bus.send("click", path, timeout=10.0) or {}
    except Exception as e:
        sys.stderr.write("interrupts: click %s -> %s\n" % (path.rsplit("|", 1)[-1], repr(e)[:70]))
        CLICK_LOG.append((time.time(), path, "error"))
        return False
    ok = bool(r.get("clicked"))
    CLICK_LOG.append((time.time(), path, ok))
    sys.stderr.write("interrupts: CLICK %s -> clicked=%s\n" % (path, ok))
    time.sleep(settle)
    return ok


def evidence(bus, why, shots_dir=None):
    """Screenshot + the exact click history + the visible roots, for a screen we could not drive.

    A stuck screen is precisely the case where the logs alone never tell you enough -- you need to
    SEE what was in front of the clicks. Returns the report dict (and the PNG path if it captured).
    """
    import os
    import subprocess
    rep = {"why": why, "roots": roots(bus), "ts": time.time(),
           "clicks": [(round(t, 1), p, c) for t, p, c in CLICK_LOG[-12:]]}
    shots_dir = shots_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "logs", "v7_shots")
    path = os.path.join(shots_dir, "stuck_%s_%d.png" % (why.replace(":", "_")[:40], int(time.time())))
    try:
        os.makedirs(shots_dir, exist_ok=True)
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "ps", "capture.ps1"),
                        path], capture_output=True, text=True, timeout=45)
        rep["screenshot"] = path if os.path.exists(path) else None
    except Exception as e:
        rep["screenshot_error"] = repr(e)[:120]
    sys.stderr.write("interrupts: STUCK (%s) roots=%s shot=%s\n  recent clicks: %s\n"
                     % (why, rep["roots"], rep.get("screenshot"), rep["clicks"]))
    return rep


def _found(bus, path):
    """Found AND actually clickable.

    ⚠ `found` alone is worthless here. The battle-results checkmark
    (button_set_settlement_captured|button_accept) reports found=True on a results panel where it is
    NOT visible, and SimulateLClick on it then returns clicked=True while doing nothing -- so the
    caller loops forever on a control that can never work. Live cost of trusting `found`: 24 clicks
    and 107 seconds per action. Requiring visible + a clickable state makes the caller fall through
    to the control the panel really offers (button_dismiss).
    """
    try:
        r = bus.send("find", path, timeout=8.0) or {}
        res = r.get("result") or {}
        return bool(res.get("found") and res.get("visible")
                    and str(res.get("state")) in _CLICKABLE)
    except Exception:
        return False


def _tree(bus, root, depth=22, nodes=4000):
    try:
        return (bus.send("tree", "%s %d %d" % (root, depth, nodes), timeout=20.0) or {}).get("nodes") or []
    except Exception as e:
        sys.stderr.write("interrupts: tree %s -> %s\n" % (root, repr(e)[:80]))
        return []


# --------------------------------------------------------------------------------- detection
def cancel_declare_war(bus):
    """Answer the modal "Declare War?" dialog with CANCEL MOVE -- never Declare War, never Request
    Military Access.

    The game raises this whenever a move/attack order would start a war ("Making this attack is an
    act of war. Do you wish to proceed?"). Declaring war and requesting military access are both
    DIPLOMACY, which the advisor did not choose and which is out of scope for this version, so the
    only correct answer is to withdraw the order.

    ⚠ This one matters out of proportion to its size: the dialog is MODAL, so until it is answered
    every later action is silently refused. Live-verified -- one bad move order left it up and the
    next nine actions all failed as `command_silently_refused` with no other symptom.

    ⚠ UNVERIFIED ROOT ID: the dialog was cleared before its root could be captured, so this does
    NOT match on a root name. It scans EVERY non-base visible root for a clickable control that
    reads "Cancel Move" -- which is why it works without knowing what the dialog is called, and why
    it is only tried when the ordinary dismiss-button drain has already found nothing.
    """
    steps = []
    for root in [x for x in roots(bus)
                 if x not in nav.BASE_ROOTS and x not in DIPLOMACY_HUD_ROOTS]:
        target = None
        for n in _tree(bus, root):
            nid = str(n.get("id") or "").lower()
            txt = str(n.get("text") or "").strip().lower()
            if not n.get("visible") or str(n.get("state")) not in _CLICKABLE:
                continue
            if txt in ("cancel move", "cancel") or "cancel_move" in nid or "button_cancel" in nid:
                target = n.get("path")
                break
        if target is None:
            continue
        if _click(bus, target, settle=2.0):
            steps.append("declare_war_cancelled:%s" % root)
        if root in roots(bus):
            sys.stderr.write("interrupts: %s still open after cancel\n" % root)
    return steps


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
    """Click autoresolve in the open pre-battle. NEVER touches the manual-battle buttons.

    Covers BOTH pre-battle variants: the one we open by attacking, and the Battle Deployment screen
    the game raises when a faction attacks US during its own turn. Same root (popup_pre_battle),
    different layout -- which is why the button is RESOLVED BY SEARCH rather than a fixed path.

    Prefers the bus click (SimulateLClick by path), which does not touch the OS cursor and so cannot
    steal the mouse from whoever is at the keyboard. Verified on the deployment screen: the button
    reports visible=True / state=active, which is exactly the condition SimulateLClick needs. The
    hardware click stays as the fallback for the case where the bus click does not register.
    """
    n = nav.find_rect(bus, "popup_pre_battle", "button_autoresolve")
    if not n or n.get("path") is None:
        sys.stderr.write("interrupts: button_autoresolve not found in popup_pre_battle\n")
        return False
    if n.get("visible") and _click(bus, n["path"], settle=1.0):
        if _wait_root(bus, "popup_battle_results", tries=20, pause=1.0):
            return True
        sys.stderr.write("interrupts: bus click on autoresolve did not open the results\n")
    if n.get("x") is None:
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
    """⚠ NEVER REPEAT A STEP THAT DEMONSTRABLY DID NOTHING.

    The battle-results checkmark reports `clicked: True` even when the popup does not close (the
    bus click lies -- see click_actions). Looping on "is a battle root still open" therefore span:
    live, this re-clicked settlement_captured_checkmark 24 times and burned 107 SECONDS per action,
    which was ~105s of every ~117s decision cycle. So each round must make the ROOT SET change; if
    it does not, the screen is stuck on something we cannot drive and looping is pure waste.
    """
    steps = []
    for _ in range(max_rounds):
        before = tuple(roots(bus))
        if "popup_pre_battle" in before:
            steps.append("autoresolve:%s" % autoresolve(bus))
        elif "popup_battle_results" in before or "settlement_captured" in before:
            steps.extend(handle_results(bus))
            time.sleep(1.5)
        else:
            break
        if tuple(roots(bus)) == before:
            steps.append("stuck:%s" % ",".join(x for x in before if x not in nav.BASE_ROOTS))
            sys.stderr.write("interrupts: battle screen did not change after a step -- giving up "
                             "rather than re-clicking (%s)\n" % (before,))
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
_stuck_sig = [None]         # the root signature we already gave up on


def forget_stuck():
    """Called when something external changed the screen (a turn ended, an action was confirmed),
    so the next stuck screen is retried rather than assumed identical."""
    _stuck_sig[0] = None


def resolve(bus, max_rounds=6):
    """Clear every interrupt currently on screen. Returns the steps taken ([] = clean screen).

    THIS RUNS AFTER EVERY ACTION, so its cost on a clean screen must be ONE `roots` call (~101ms)
    and nothing more.

    Two limiters, both learned from a live 107s-per-action stall:
      * a round that leaves the visible root set UNCHANGED means nothing here can drive it, so stop
        instead of walking every open panel's tree again (the outer loop used to multiply the inner
        one, 6 x 4 = 24 clicks on a control that could never work);
      * once a root set has been declared stuck, DO NOT RE-ATTEMPT IT -- no trees, no clicks, no
        repeat screenshot -- until the roots actually change. A screen we could not drive a second
        ago is not going to yield to the same clicks a second later.
    """
    steps = []
    for _ in range(max_rounds):
        before = tuple(roots(bus))
        if before and before == _stuck_sig[0]:
            return ["stuck_unchanged"]          # cheap: one roots call, then out
        kinds = pending(bus)
        if not kinds:
            _stuck_sig[0] = None
            break
        if "battle" in kinds:
            steps.extend(resolve_battle(bus))
            if tuple(roots(bus)) == before:
                break
            continue
        if "diplomacy" in kinds:
            steps.extend(decline_diplomacy(bus))
            if tuple(roots(bus)) == before:
                break
            continue
        try:
            n = len(nav.close_popups(bus))
        except Exception as e:
            sys.stderr.write("interrupts: close_popups -> %s\n" % repr(e)[:80])
            break
        if n:
            steps.append("popups_cleared:%d" % n)
            continue
        # Nothing carried a standard dismiss button. Before giving up, try the modals whose
        # controls are named differently -- "Declare War?" answers to "Cancel Move", not button_ok,
        # so the generic drain silently does nothing and the run stalls behind it.
        s = cancel_declare_war(bus)
        if s:
            steps.extend(s)
            continue
        # something is open that we cannot dismiss -- record it with a screenshot so the next
        # unhandled screen is diagnosable instead of just slow
        if [x for x in before if x not in nav.BASE_ROOTS and x not in DIPLOMACY_HUD_ROOTS]:
            steps.append("undismissable:%s" % ",".join(x for x in before if x not in nav.BASE_ROOTS))
            if before != _stuck_sig[0]:
                evidence(bus, "undismissable")
            _stuck_sig[0] = before
        break
    return steps
