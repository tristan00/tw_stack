"""interrupts.py -- driving screens that appear without the advisor asking for them."""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

import nav

_BR = "popup_battle_results|mid|battle_results|post_battle_results_panel"
BATTLE_ROOTS = ("popup_pre_battle", "popup_battle_results", "settlement_captured")
FORBIDDEN_CLICK_IDS = frozenset({"button_attack", "button_spectate", "button_retreat"})
SCROLL_CHROME_IDS = frozenset({"top", "bottom", "handle", "vslider"})

PREBATTLE_PREFERENCE = (
    "button_autoresolve",
    "button_continue_siege",
)

PREBATTLE_CHOOSABLE = (
    "button_autoresolve",
    "button_continue_siege",
    "button_surround",
    "button_retreat",
    "button_sally_forth",
    "button_maintain_blockade",
    "button_demand_surrender",
)
PREBATTLE_NEVER = frozenset(("button_attack", "button_spectate"))

CAPTIVE_PREFIX = "button_captive_option_"


def is_captive_option(control_id):
    return str(control_id).startswith(CAPTIVE_PREFIX)


CAPTIVE_OPTIONS = frozenset((
    "button_captive_option_release",
    "button_captive_option_enslave",
    "button_captive_option_kill",
    "button_captive_option_enslave_replenishment_only",
    "button_captive_option_enslave_slaves_only",
))
ADVANCE_PREFERENCE = (
    "button_accept",
    "button_dismiss",
)


RESULTS_DECLINED = frozenset((
    "button_call_up_the_trolls",
))
KNOWN_RESULTS_CONTROLS = CAPTIVE_OPTIONS | frozenset(ADVANCE_PREFERENCE) | RESULTS_DECLINED
PREBATTLE_DECLINED = FORBIDDEN_CLICK_IDS | PREBATTLE_NEVER | frozenset(PREBATTLE_CHOOSABLE) | frozenset((
    "button_dismiss", "button_cancel_ready", "button_save",
    "button_add_charges",
))
KNOWN_PREBATTLE_CONTROLS = frozenset(PREBATTLE_PREFERENCE) | PREBATTLE_DECLINED


DISPLAY_CONTROLS = frozenset((
    "army_button", "button_info", "button_preview_map",
    "card_image_holder", "icon", "selected_frame",
    "button_finance",
    "button_books_of_nagash",
    "button_zoom",
    "button_txt",
))
DISPLAY_PREFIXES = ("unit_",)


def _unknown_controls(ctrls, known):
    """`button_*` ids in `ctrls` that are neither in `known` nor in DISPLAY_CONTROLS."""
    return sorted(i for i in ctrls
                  if str(i).startswith("button_")
                  and i not in known and i not in DISPLAY_CONTROLS)


_CHOOSER = [None]
_CAMPAIGN = [None]
_LAST_POLICY = [None]
_LAST_SCORES = [None]


def set_chooser(fn):
    """Install the chooser: fn(screen, options, campaign) -> (option, policy). Required."""
    _CHOOSER[0] = fn


def set_campaign(campaign):
    """The campaign state passed to the chooser."""
    _CAMPAIGN[0] = campaign


def _campaign_hint():
    return _CAMPAIGN[0]


def _choose(screen, options, campaign=None):
    """Pick one of `options` via the installed chooser; a pick outside `options` raises."""
    opts = sorted(options)
    if not opts:
        return None
    fn = _CHOOSER[0]
    if fn is None:
        raise RuntimeError(
            "no interrupt chooser installed -- the advisor owns this decision and must call "
            "interrupts.set_chooser() before driving any screen. Refusing to invent a policy in "
            "the launcher (screen=%s, offered=%s)" % (screen, opts))
    got, policy, scores = fn(screen, opts, campaign)
    if got not in options:
        raise RuntimeError(
            "chooser returned %r which is NOT among the legal options %s for %s. Taking it anyway "
            "is how button_attack eventually gets clicked." % (got, opts, screen))
    _LAST_POLICY[0] = policy
    _LAST_SCORES[0] = dict(scores or {})
    sys.stderr.write("interrupts: SCREEN %s offered=%s -> %r (%s) scores=%s\n"
                     % (screen, opts, got, policy,
                        {k: round(v, 4) for k, v in sorted((scores or {}).items())}))
    return got


UNHANDLED_LOG = r"D:/twdata/runs/human/unhandled_screens.jsonl"


def _report_unhandled(bus, screen, unknown, offered, root=None):
    """Append an unhandled-option record (with screenshot) to UNHANDLED_LOG. Never raises."""
    import json
    rec = {"ts": time.time(), "when": time.strftime("%Y-%m-%d %H:%M:%S"), "screen": screen,
           "unknown": list(unknown), "offered": list(offered), "root": root}
    try:
        rec["roots"] = roots(bus)
    except Exception:
        rec["roots"] = None
    try:
        dump = [root] if root else [rt for rt in (rec["roots"] or [])
                                    if rt not in nav.BASE_ROOTS and rt not in BENIGN_PANELS]
        rec["trees"] = {rt: _tree(bus, rt) for rt in dump}
    except Exception as e:
        rec["trees_error"] = repr(e)[:120]
    try:
        want = {str(u) for u in unknown}
        rec["unknown_nodes"] = [dict(n, root=rt) for rt, ns in (rec.get("trees") or {}).items()
                                for n in ns if str(n.get("id") or "") in want]
    except Exception as e:
        rec["unknown_nodes_error"] = repr(e)[:120]
    try:
        rec["screenshot"] = (evidence(bus, "unhandled_%s" % screen) or {}).get("screenshot")
    except Exception as e:
        rec["screenshot_error"] = repr(e)[:120]
    try:
        import os
        os.makedirs(os.path.dirname(UNHANDLED_LOG), exist_ok=True)
        with open(UNHANDLED_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        sys.stderr.write("interrupts: unhandled-screen record NOT written: %s\n" % repr(e)[:120])
    sys.stderr.write("UNHANDLED_OPTION %s %s offered=%s shot=%s\n"
                     % (screen, list(unknown), list(offered), rec.get("screenshot")))
    return rec


class UnhandledScreen(BaseException):
    """An interrupt screen offered an option this code cannot pick or record."""
ACCEPT_TOKENS = ("accept", "confirm", "button_ok", "button_yes")
DECLINE_TOKENS = ("decline", "reject", "refuse", "cancel", "close", "no_deal")
DIPLOMACY_NEVER_CLICK_PREFIXES = ("diplomatic_option",)
DIPLOMACY_NEVER_CLICK_IDS = frozenset(("button_send",))
_CLICKABLE = ("active", "hover", "selected")
DIPLOMACY_HUD_ROOTS = frozenset(("diplomacy_dropdown", "diplomacy_attitude_tooltip"))
WAR_DECLARED_MARKER = "declared war on you"
BENIGN_PANELS = nav.BENIGN_PANELS

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


CLICK_LOG = []


def _click(bus, path, settle=1.5):
    before = tuple(roots(bus))
    try:
        r = bus.send("click", path, timeout=10.0) or {}
    except Exception as e:
        sys.stderr.write("interrupts: click %s -> %s\n" % (path.rsplit("|", 1)[-1], repr(e)[:70]))
        CLICK_LOG.append((time.time(), path, "error"))
        return False
    ok = bool(r.get("clicked"))
    CLICK_LOG.append((time.time(), path, ok))
    sys.stderr.write("interrupts: CLICK %s -> clicked=%s\n" % (path, ok))
    deadline = time.time() + settle
    while time.time() < deadline:
        time.sleep(0.3)
        if tuple(roots(bus)) != before:
            break
    return ok


def evidence(bus, why, shots_dir=None):
    """Screenshot + recent clicks + visible roots; returns the report dict."""
    import os
    import subprocess
    rep = {"why": why, "roots": roots(bus), "ts": time.time(),
           "clicks": [(round(t, 1), p, c) for t, p, c in CLICK_LOG[-12:]]}
    rep["dumps"] = [p for p in (nav.dump_screen(bus, r, "stuck_" + why.replace(":", "_")[:24])
                                for r in rep["roots"] if r not in nav.BASE_ROOTS) if p]
    shots_dir = shots_dir or r"D:\twdata\logs\launcher\v7_shots"
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
    """Found AND visible AND in a clickable state."""
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


def cancel_declare_war(bus):
    """Click a "Cancel Move" control in any non-base root. Returns the steps taken."""
    steps = []
    for root in [x for x in roots(bus)
                 if x not in nav.BASE_ROOTS and x not in DIPLOMACY_HUD_ROOTS
                 and x not in BENIGN_PANELS]:
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
    """Visible diplomacy roots, excluding DIPLOMACY_HUD_ROOTS."""
    r = roots(bus) if r is None else r
    return [x for x in r if "diplo" in str(x).lower() and x not in DIPLOMACY_HUD_ROOTS]


DEFEAT_ROOT_TOKENS = ("defeat", "victory", "campaign_end", "game_over", "campaign_result")


def defeat_screen(bus, r=None):
    """Root name matching DEFEAT_ROOT_TOKENS, or None. Root-only: never walks a tree."""
    r = roots(bus) if r is None else r
    for x in r:
        low = str(x).lower()
        if any(tok in low for tok in DEFEAT_ROOT_TOKENS):
            return x
    return None


DEFEAT_PROBE = (
    "local f=cm:get_local_faction(true) if not f then return 'nofaction' end "
    "local ok,d=pcall(function() return f:is_dead() end) "
    "if not ok then return 'err:'..tostring(d) end "
    "return tostring(d)..'|'..f:region_list():num_items()")


def defeated_probe(bus):
    """Engine-side death check: True/False, or None when the probe itself failed (logged)."""
    try:
        r = bus.send("eval", DEFEAT_PROBE, timeout=10.0) or {}
    except Exception as e:
        sys.stderr.write("interrupts: defeat probe bus error -> %s\n" % repr(e)[:90])
        return None
    if r.get("error"):
        sys.stderr.write("interrupts: defeat probe eval error -> %s\n" % str(r.get("error"))[:120])
        return None
    res = str(r.get("result") or "")
    if res.startswith("true"):
        sys.stderr.write("interrupts: DEFEAT PROBE -> faction is dead (%s)\n" % res)
        return True
    if res.startswith("false"):
        return False
    sys.stderr.write("interrupts: defeat probe unexpected result %r\n" % res[:80])
    return None


def pending(bus):
    """Which interrupt kinds are on screen: a subset of {'battle', 'diplomacy', 'popup'}."""
    r = roots(bus)
    out = set()
    if r and "hud_campaign" not in r:
        out.add("popup")
    if any(x in r for x in BATTLE_ROOTS):
        out.add("battle")
    if diplomacy_roots(bus, r):
        out.add("diplomacy")
    if [x for x in r if x not in nav.BASE_ROOTS and x not in DIPLOMACY_HUD_ROOTS
            and x not in BENIGN_PANELS]:
        out.add("popup")
    return out


def in_battle(bus):
    return "battle" in pending(bus)


def prebattle_forecast(bus):
    """The autoresolve forecast the panel shows: {result, casualties}, each text + state.

    dy_result / dy_casualties are present whatever the faction (verified on a Skaven panel
    carrying its own Menace Below elements)."""
    out = {}
    for n in _tree(bus, "popup_pre_battle", 30, 40000):
        i = str(n.get("id") or "")
        if i in ("dy_result", "dy_casualties") and n.get("visible"):
            out[i[3:]] = {"text": str(n.get("text") or "").strip() or None,
                          "state": str(n.get("state") or "") or None}
    return out


def resolve_prebattle(bus):
    """Drive the open pre-battle panel forward. Returns the control used, or False."""
    ctrls = _clickable_controls(bus, "popup_pre_battle")
    unknown = _unknown_controls(ctrls, KNOWN_PREBATTLE_CONTROLS)
    if unknown:
        _report_unhandled(bus, "pre_battle", unknown, sorted(ctrls), root="popup_pre_battle")
        raise UnhandledScreen(
            "pre-battle screen offers UNHANDLED option(s) %s -- add them to PREBATTLE_PREFERENCE "
            "only if they resolve the battle WITHOUT entering it, otherwise to FORBIDDEN_CLICK_IDS. "
            "clickable=%s" % (unknown, sorted(ctrls)))
    legal = [i for i in PREBATTLE_CHOOSABLE if i in ctrls and i not in PREBATTLE_NEVER]
    target = _choose("pre_battle", legal, _campaign_hint()) if legal else None
    if target is None:
        sys.stderr.write("interrupts: pre-battle offers no usable control -- clickable=%s\n"
                         % ",".join(sorted(ctrls)[:10]))
        return False
    opts = _options_of(bus, "popup_pre_battle", legal)
    forecast = prebattle_forecast(bus)
    # the real control set, not our belief about it: a control can be present and clickable and
    # still be unable to end the screen (button_surround on a DEFENSIVE battle, measured)
    tree = _tree(bus, "popup_pre_battle", 30, 40000)
    offered_all = sorted({str(n.get("id")) for n in tree
                          if str(n.get("id") or "").startswith("button_") and n.get("visible")})
    t0 = time.time()
    off = bus.out_offset()
    clicked = _click(bus, ctrls[target], settle=1.5)
    if not clicked:
        _record_choice("pre_battle", "popup_pre_battle", opts, target,
                       extra={"panel": forecast, "tree": tree, "controls": offered_all},
                       executed=False, confirmed=False, refusal="execute_failed",
                       latency_ms=int((time.time() - t0) * 1000))
        return False
    if target in ("button_continue_siege", "button_surround", "button_retreat",
                  "button_sally_forth", "button_maintain_blockade", "button_demand_surrender"):
        name = target[len("button_"):]
        ok = "popup_pre_battle" not in roots(bus)
        if not ok:
            sys.stderr.write("interrupts: %s clicked but the pre-battle is still open\n" % name)
        outcome = name if ok else False
    else:
        ok = _results_appeared(bus, off, timeout=20.0)
        outcome = "autoresolve" if ok else False
    if not ok:
        still_up = "popup_pre_battle" in roots(bus)
        sys.stderr.write("interrupts: %s did NOT resolve the battle (pre_battle still open=%s)\n"
                         % (target, still_up))
    _record_choice("pre_battle", "popup_pre_battle", opts, target,
                   extra={"panel": forecast, "tree": tree, "controls": offered_all},
                   executed=clicked, confirmed=bool(ok),
                   refusal=None if ok else "command_silently_refused",
                   latency_ms=int((time.time() - t0) * 1000))
    return outcome


def _results_appeared(bus, offset, timeout=20.0):
    """Battle resolved: the mod's battle_completed/panel row, else the results root."""
    deadline = time.time() + timeout
    off = offset

    def _wanted(r):
        if r.get("cmd") == "battle_completed":
            return True
        return bool(r.get("opened")) and "battle_results" in str(r.get("name") or "")

    while time.time() < deadline:
        row, off = bus.wait_row(("battle_completed", "panel"),
                                timeout=min(2.0, max(0.1, deadline - time.time())),
                                offset=off, pred=_wanted)
        if row is not None:
            if _wait_root(bus, "popup_battle_results", tries=6, pause=0.5):
                return True
            continue
        if "popup_battle_results" in roots(bus):
            return True
    return False


def _clickable_controls(bus, root="popup_battle_results"):
    """{id: path} for every visible+clickable node under `root`."""
    out = {}
    for n in _tree(bus, root, 22, 4000):
        if not n.get("visible") or str(n.get("state")) not in _CLICKABLE:
            continue
        nid = str(n.get("id") or "")
        if nid and nid not in out:
            out[nid] = n.get("path")
    return out


def handle_results(bus):
    """Drive the post-battle panel to closure. Returns the steps performed."""
    steps = []
    last, repeats, idle_waits = None, 0, 0
    for _ in range(4):
        if "popup_battle_results" not in roots(bus):
            break
        ctrls = _clickable_controls(bus)
        unknown = _unknown_controls([c for c in ctrls if not is_captive_option(c)],
                                    KNOWN_RESULTS_CONTROLS)
        if unknown:
            _report_unhandled(bus, "battle_results", unknown, sorted(ctrls),
                              root="popup_battle_results")
            raise UnhandledScreen(
                "battle-results screen offers UNHANDLED option(s) %s -- add each one to "
                "CAPTIVE_OPTIONS if it is a captive's fate (it then joins the uniform draw and is "
                "recorded under its own id), or to ADVANCE_PREFERENCE if it advances the panel. "
                "clickable=%s" % (unknown, sorted(ctrls)))
        fates = sorted(i for i in ctrls if is_captive_option(i))
        target = (_choose("battle_results", fates, _campaign_hint()) if fates
                  else next((i for i in ADVANCE_PREFERENCE if i in ctrls), None))
        if target is None:
            idle_waits += 1
            if idle_waits > 1:
                sys.stderr.write("interrupts: results panel still offers no advance control (%s)\n"
                                 % ",".join(sorted(ctrls)[:6]))
                break
            time.sleep(1.0)
            continue
        decisions = sorted([i for i in ctrls if is_captive_option(i)]
                           + [i for i in ADVANCE_PREFERENCE if i in ctrls])
        opts_before = _options_of(bus, "popup_battle_results", decisions)
        repeats = repeats + 1 if target == last else 0
        if repeats >= 2:
            sys.stderr.write("interrupts: %s clicked twice with no effect -- not hammering it\n"
                             % target)
            _record_choice("battle_results", "popup_battle_results", opts_before, target,
                           executed=True, confirmed=False, refusal="command_silently_refused")
            break
        last = target
        t0 = time.time()
        roots_before = set(roots(bus))
        clicked = _click(bus, ctrls[target], settle=2.5)
        moved = set(roots(bus)) != roots_before
        _record_choice("battle_results", "popup_battle_results", opts_before, target,
                       executed=clicked, confirmed=bool(moved),
                       refusal=None if moved else ("command_silently_refused" if clicked
                                                   else "execute_failed"),
                       latency_ms=int((time.time() - t0) * 1000))
        steps.append("%s:%s" % (target, clicked))
    if "settlement_captured" in roots(bus):
        steps.append("occupation:%s" % (occupy(bus) or "none"))
    return steps


def occupy(bus):
    """Pick one of the captured settlement's options. Returns the label clicked, or None."""
    opts = {}
    for n in _tree(bus, "settlement_captured", 22, 3000):
        if n.get("id") == "dy_option" and n.get("text"):
            opts[str(n["text"]).strip().lower()] = str(n["path"]).rsplit("|dy_option", 1)[0]
    if not opts:
        sys.stderr.write("interrupts: settlement_captured has no dy_option nodes\n")
        return None
    detail = {k: {"context": None, "text": k} for k in opts}
    want = _choose("occupation", sorted(opts), _campaign_hint())
    t0 = time.time()
    clicked = _click(bus, opts[want], settle=2.5)
    if not clicked:
        _record_choice("occupation", "settlement_captured", detail, want,
                       executed=False, confirmed=False, refusal="execute_failed",
                       latency_ms=int((time.time() - t0) * 1000))
        return None
    gone = "settlement_captured" not in roots(bus)
    if not gone:
        sys.stderr.write("interrupts: occupation %r clicked but settlement_captured is still open\n"
                         % want)
    _record_choice("occupation", "settlement_captured", detail, want,
                   executed=clicked, confirmed=gone,
                   refusal=None if gone else "command_silently_refused",
                   latency_ms=int((time.time() - t0) * 1000))
    return want if gone else None


def resolve_battle(bus, max_rounds=4):
    """Drive battle screens, stopping as soon as a round leaves the root set unchanged."""
    steps = []
    for _ in range(max_rounds):
        before = tuple(roots(bus))
        if "popup_pre_battle" in before:
            steps.append("prebattle:%s" % resolve_prebattle(bus))
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


def acknowledge_war_declared(bus, open_roots):
    """Acknowledge a 'faction has declared war on you' notification. Returns the steps taken."""
    steps = []
    for root in open_roots:
        if root in nav.BASE_ROOTS or root in DIPLOMACY_HUD_ROOTS or root in BENIGN_PANELS:
            continue
        tree = _tree(bus, root)
        if not any(WAR_DECLARED_MARKER in str(n.get("text") or "").lower() for n in tree):
            continue
        target = None
        for n in tree:
            nid = str(n.get("id") or "").lower()
            if (n.get("visible") and str(n.get("state")) in _CLICKABLE
                    and any(t in nid for t in ACCEPT_TOKENS)):
                target = (str(n.get("id")), n.get("path"))
                break
        if target is None:
            _report_unhandled(bus, "war_declared", ["no accept-family control"], [], root=root)
            continue
        opts = {target[0]: {"context": None, "text": "acknowledge war declaration"}}
        t0 = time.time()
        clicked = _click(bus, target[1], settle=2.0)
        gone = root not in roots(bus)
        _record_choice("war_declared", root, opts, target[0],
                       extra={"tree": tree},
                       executed=clicked, confirmed=gone,
                       refusal=None if gone else ("command_silently_refused" if clicked
                                                  else "execute_failed"),
                       latency_ms=int((time.time() - t0) * 1000))
        if clicked:
            steps.append("war_declared_acknowledged:%s" % root)
    return steps


def answer_diplomacy(bus):
    """Answer an incoming proposal with one of its DECLINE controls. Returns the steps taken."""
    steps = []
    for root in diplomacy_roots(bus):
        answers = {}
        tree = _tree(bus, root)
        for n in tree:
            nid = str(n.get("id") or "")
            low = nid.lower()
            if not n.get("visible") or str(n.get("state")) not in _CLICKABLE:
                continue
            if low.startswith(DIPLOMACY_NEVER_CLICK_PREFIXES) or nid in DIPLOMACY_NEVER_CLICK_IDS:
                continue
            if nid in FORBIDDEN_CLICK_IDS:
                continue
            kind = ("accept" if any(t in low for t in ACCEPT_TOKENS)
                    else "decline" if any(t in low for t in DECLINE_TOKENS)
                    else None)
            if kind and nid not in answers:
                answers[nid] = (n.get("path"), kind)
        if not answers:
            sys.stderr.write("interrupts: no clickable answer control in %s\n" % root)
            steps.append("diplomacy_stuck:%s" % root)
            continue
        detail = _options_of(bus, root, sorted(answers))
        for k, (_path, kind) in answers.items():
            detail[k] = dict(detail.get(k) or {}, answer=kind)
        want = _choose("diplomacy", sorted(answers), _campaign_hint())
        target, kind = answers[want]
        sys.stderr.write("interrupts: diplomacy %s -- %d answer(s) %s -> %r (%s)\n"
                         % (root, len(answers), sorted(answers), want, kind))
        t0 = time.time()
        clicked = _click(bus, target, settle=2.0)
        gone = root not in roots(bus)
        if clicked:
            steps.append("diplomacy_%s:%s" % (kind, want))
        if not gone:
            sys.stderr.write("interrupts: diplomacy root %s still open after %s\n" % (root, kind))
        if not clicked or not gone:
            steps.append("diplomacy_stuck:%s" % root)
        _record_choice("diplomacy", root, detail, want,
                       extra={"answer": kind, "tree": tree},
                       executed=clicked, confirmed=gone,
                       refusal=None if gone else ("command_silently_refused" if clicked
                                                  else "execute_failed"),
                       latency_ms=int((time.time() - t0) * 1000))
    return steps


PROPOSAL_ROOT = "diplomacy_dropdown"
PROPOSAL_ANSWER_IDS = frozenset(("button_accept", "button_cancel"))
# same-root subpanel controls that are dismiss/confirm furniture, not proposal answers:
# war_declared ack + declare-war confirm, both cleared by nav.close_popups' button_ok* rule
PROPOSAL_KNOWN_NONANSWERS = frozenset(("button_ok_war_declared", "button_ok_declare",
                                       "button_cancel_declare"))

_ANSWER_MEMO = {}                    # root -> {"want","policy","scores","tries","ts"}
_ANSWER_TRIES = 2
_ANSWER_TTL = 180.0


def _sticky_choice(screen, root, options):
    """The advisor's pick for `root`, chosen ONCE and held while the screen persists, so a retry
    re-clicks the SAME answer instead of re-rolling a war decision. tries counts attempts."""
    m = _ANSWER_MEMO.get(root)
    if m and m.get("want") in options and time.time() - m.get("ts", 0) <= _ANSWER_TTL:
        m["tries"] += 1
        return m
    m = {"want": _choose(screen, sorted(options), _campaign_hint()),
         "policy": _LAST_POLICY[0], "scores": dict(_LAST_SCORES[0] or {}),
         "tries": 1, "ts": time.time()}
    _ANSWER_MEMO[root] = m
    return m


def _await_root_gone(bus, root, limit=6.0):
    deadline = time.time() + limit
    while time.time() < deadline:
        if root not in roots(bus):
            return True
        time.sleep(0.5)
    return False


def _drive_decision(bus, root, kind, opts, detail, extra):
    """Click the held pick for a decision screen. Bounded retries (same pick), then give up to
    the watchdog; exactly ONE record per screen appearance -- at resolution or at the final try."""
    steps = []
    m = _sticky_choice(kind, root, opts)
    if m["tries"] > _ANSWER_TRIES:
        steps.append("%s_gave_up:%s" % (kind, root))
        return steps
    want = m["want"]
    t0 = time.time()
    clicked = _click(bus, opts[want], settle=2.0)
    gone = _await_root_gone(bus, root)
    if clicked:
        steps.append("%s_%s:%s" % (kind, detail[want]["answer"], want))
    if not clicked or not gone:
        steps.append("%s_stuck:%s" % (kind, root))
    if gone or m["tries"] >= _ANSWER_TRIES:
        _LAST_POLICY[0], _LAST_SCORES[0] = m["policy"], dict(m["scores"] or {})
        _record_choice(kind, root, detail, want,
                       extra=dict(extra, answer=detail[want]["answer"]),
                       executed=clicked, confirmed=gone,
                       refusal=None if gone else ("command_silently_refused" if clicked
                                                  else "execute_failed"),
                       latency_ms=int((time.time() - t0) * 1000))
    if gone:
        _ANSWER_MEMO.pop(root, None)
    return steps


def _first_text(nodes, node_id, path_token=None):
    """Text of the first visible node with id `node_id` (optionally path-filtered), else None."""
    for n in nodes:
        if str(n.get("id") or "") != node_id or not n.get("visible"):
            continue
        if path_token and path_token not in str(n.get("path") or ""):
            continue
        t = str(n.get("text") or "").strip()
        if t:
            return t
    return None


def proposal_options(nodes):
    """{answer_id: path} for an incoming diplomacy screen, {} when `nodes` is not one.

    Three states exist in the 20260801 dump corpus (77 dumps): a PROPOSAL offers accept+cancel
    both clickable (14/14 observed); a NOTICE (e.g. treaty termination) has button_accept only --
    button_cancel absent from the tree entirely -- and accept is an acknowledgment (2/2 observed);
    a decline that is present but unclickable was never observed and raises rather than guesses.
    The incoming marker is button_accept visible+clickable; the 'Waiting on player...' labels are
    visible=false in every dump. v1 policy (operator, 2026-08-01): accept/decline only --
    button_counteroffer is NOT an option.
    """
    out, present, odd = {}, set(), set()
    for n in nodes:
        nid = str(n.get("id") or "")
        if nid in PROPOSAL_ANSWER_IDS:
            present.add(nid)
            if (n.get("visible") and str(n.get("state")) in _CLICKABLE and nid not in out):
                out[nid] = str(n.get("path") or "")
            continue
        if (nid.startswith("button_") and nid not in PROPOSAL_KNOWN_NONANSWERS
                and nid != "button_counteroffer"
                and n.get("visible") and str(n.get("state")) in _CLICKABLE
                and (any(t in nid for t in ACCEPT_TOKENS)
                     or any(t in nid for t in DECLINE_TOKENS))):
            odd.add(nid)
    if "button_accept" not in out:
        return {}
    if odd:
        raise UnhandledScreen(
            "incoming diplomacy screen carries UNKNOWN answer-family control(s) %s -- classify "
            "each (answer -> PROPOSAL_ANSWER_IDS, furniture -> PROPOSAL_KNOWN_NONANSWERS) before "
            "this screen can be answered. Guessing here is how deals get signed blind."
            % sorted(odd))
    if "button_cancel" in present and "button_cancel" not in out:
        raise UnhandledScreen(
            "incoming proposal has a decline control that is NOT clickable -- a proposal this "
            "code cannot refuse must not be answered by it. clickable=%s" % sorted(out))
    return out


def answer_incoming_proposal(bus):
    """Answer an incoming proposal on the diplomacy dropdown: the advisor picks accept or decline.

    Replaces two wrong behaviours: answer_diplomacy never saw this root (DIPLOMACY_HUD_ROOTS),
    and nav.close_popups treated button_accept as a dismiss -- i.e. blind-accepted the deal.
    Caveat (accepted): if the OUTGOING walk strands its negotiation-response screen (close_panel
    raised), that screen also shows accept/cancel and gets answered here as if incoming -- the
    advisor deciding a stranded negotiation beats a stuck campaign.
    """
    if PROPOSAL_ROOT not in roots(bus):
        return []
    tree = _tree(bus, PROPOSAL_ROOT)
    try:
        opts = proposal_options(tree)
    except UnhandledScreen:
        _report_unhandled(bus, "diplomacy_proposal",
                          sorted({str(n.get("id")) for n in tree
                                  if n.get("visible") and str(n.get("state")) in _CLICKABLE
                                  and str(n.get("id") or "").startswith("button_")}),
                          sorted(PROPOSAL_ANSWER_IDS), root=PROPOSAL_ROOT)
        raise
    if not opts:
        return []
    kind = "diplomacy_proposal" if "button_cancel" in opts else "diplomacy_notice"
    detail = _options_of(bus, PROPOSAL_ROOT, sorted(opts))
    for k in detail:
        detail[k] = dict(detail[k],
                         answer=("decline" if k == "button_cancel"
                                 else "accept" if kind == "diplomacy_proposal"
                                 else "acknowledge"))
    # no "chance" field: label_deal_success_chance is visible only on the outgoing negotiation
    # screen; on incoming proposals the hidden node carries a stale placeholder (14/14 dumps)
    extra = {"tree": tree,
             "proposer": _first_text(tree, "faction_title", "faction_right_status_panel"),
             "speech": _first_text(tree, "dy_text", "speech_bubble"),
             "attitude": _first_text(tree, "dy_value")}
    return _drive_decision(bus, PROPOSAL_ROOT, kind, opts, detail, extra)


ALLY_ATTACKED_ROOT = "ally_attacked"
ALLY_ATTACKED_DECISIONS = frozenset(("button_join_aggressor", "button_join_defender",
                                     "decline_button"))
# display furniture of the join-button assembly, measured in dump 1785619114999
ALLY_ATTACKED_FURNITURE = frozenset(("button_txt", "button_flame", "button_parent",
                                     "button_default_selection"))


def ally_attacked_options(nodes):
    """{decision_id: path} for a call-to-arms screen. Raises UnhandledScreen when the screen
    does not offer both a join and a decline, or offers a join control outside the known set.

    Grounded in dump 1785619114999 (offensive variant): button_join_aggressor visible+active,
    decline_button visible+active, button_join_defender present but hidden. The defensive
    variant has never been tree-dumped; deriving from visibility handles it the moment it is.
    Ids like button_txt/button_flame/option1_text are display furniture of the join assembly.
    """
    out, unknown = {}, []
    for n in nodes:
        nid = str(n.get("id") or "")
        if not n.get("visible") or str(n.get("state")) not in _CLICKABLE:
            continue
        if nid in ALLY_ATTACKED_DECISIONS:
            out.setdefault(nid, str(n.get("path") or ""))
        elif ((nid.startswith("button_") or "decline" in nid)
                and nid not in ALLY_ATTACKED_FURNITURE):
            unknown.append(nid)
    if unknown:
        raise UnhandledScreen(
            "ally_attacked offers UNKNOWN decision control(s) %s -- add each to "
            "ALLY_ATTACKED_DECISIONS so it can be both picked and recorded. visible known=%s"
            % (sorted(unknown), sorted(out)))
    if "decline_button" not in out or not any(k.startswith("button_join") for k in out):
        raise UnhandledScreen(
            "ally_attacked variant without a clickable join+decline pair (visible decisions=%s) "
            "-- this code cannot answer it and will not dismiss a war decision" % sorted(out))
    return out


def answer_ally_attacked(bus):
    """Answer a call-to-arms: the advisor picks join (whichever side is offered) or decline.

    Both options are real decisions -- join enters a war; decline breaks the alliance and costs
    reliability -- so the pick is recorded like every other interrupt decision.
    """
    steps = []
    if ALLY_ATTACKED_ROOT not in roots(bus):
        return steps
    tree = _tree(bus, ALLY_ATTACKED_ROOT)
    try:
        opts = ally_attacked_options(tree)
    except UnhandledScreen:
        _report_unhandled(bus, "ally_attacked",
                          [str(n.get("id")) for n in tree
                           if n.get("visible") and str(n.get("state")) in _CLICKABLE],
                          sorted(ALLY_ATTACKED_DECISIONS), root=ALLY_ATTACKED_ROOT)
        raise
    detail = _options_of(bus, ALLY_ATTACKED_ROOT, sorted(opts))
    for k in detail:
        detail[k] = dict(detail[k], answer=("decline" if k == "decline_button" else "join"))
    extra = {"tree": tree, "variant": _first_text(tree, "dy_subtitle")}
    return _drive_decision(bus, ALLY_ATTACKED_ROOT, "ally_attacked", opts, detail, extra)


_stuck_sig = [None]


def forget_stuck():
    """Clear the stuck-screen memo so the next stuck screen is retried."""
    _stuck_sig[0] = None


def choose_dilemma(bus, open_roots):
    """Answer a dilemma by clicking one of its choice records. Returns the steps taken."""
    steps = []
    for root in open_roots:
        if (root in nav.BASE_ROOTS or root in DIPLOMACY_HUD_ROOTS or root in BENIGN_PANELS
                or root in BATTLE_ROOTS):
            continue
        if not _is_dilemma(bus, root):
            continue
        found = _dilemma_options(bus, root)
        if not found:
            ctrls = _clickable_controls(bus, root)
            actionable = {i: p for i, p in ctrls.items()
                          if str(i).startswith("button_")
                          and i not in SCROLL_CHROME_IDS and i not in DISPLAY_CONTROLS}
            if not actionable:
                continue
            ack = sorted(i for i in actionable
                         if any(t in i.lower() for t in ACCEPT_TOKENS)
                         or i in ("button_dismiss", "button_close"))
            if ack and len(ack) == len(actionable):
                tree = _tree(bus, root)
                opts = {i: {"context": None, "text": "acknowledge event"} for i in ack}
                key = ack[0]
                t0 = time.time()
                clicked = _click(bus, actionable[key], settle=2.0)
                gone = root not in roots(bus)
                _record_choice("event_ack", root, opts, key, extra={"tree": tree},
                               executed=clicked, confirmed=gone,
                               refusal=None if gone else ("command_silently_refused" if clicked
                                                          else "execute_failed"),
                               latency_ms=int((time.time() - t0) * 1000))
                if clicked:
                    steps.append("event_ack:%s:%s" % (root, key))
                break
            _report_unhandled(bus, "dilemma", ["no %s choice records" % DILEMMA_LIST],
                              sorted(ctrls), root=root)
            raise UnhandledScreen(
                "dilemma %s is open but no choice records were found under %s -- refusing to click "
                "anything. clickable=%s" % (root, DILEMMA_LIST, sorted(ctrls)))
        opts = {k: v["path"] for k, v in found.items()}
        detail = {"root": root, "root_context": None,
                  "options": {k: {"context": v["context"], "text": v["text"]}
                              for k, v in found.items()}}
        key = _choose("dilemma", sorted(opts), _campaign_hint())
        sys.stderr.write("interrupts: dilemma %s -- %d options -> %r\n" % (root, len(opts), key))
        t0 = time.time()
        clicked = _click(bus, opts[key], settle=2.5)
        gone = root not in roots(bus)
        if clicked:
            steps.append("dilemma:%s:%s" % (root, key))
        _record_choice("dilemma", root, detail["options"], key,
                       extra={"root_context": detail.get("root_context"),
                              "tree": _tree(bus, root)},
                       executed=clicked, confirmed=gone,
                       refusal=None if gone else ("command_silently_refused" if clicked
                                                  else "execute_failed"),
                       latency_ms=int((time.time() - t0) * 1000))
        break
    return steps


_INTERRUPT_LOG = []


def _record_choice(kind, root, options, chosen, extra=None,
                   executed=None, confirmed=None, refusal=None, latency_ms=None):
    """Buffer one interrupt-screen decision. `options` is {id: {"context":..., "text":...}}.

    `confirmed` comes only from a post-assert on game state, never from a click reporting True.
    """
    confirmed_b = None if confirmed is None else bool(confirmed)
    executed_b = None if executed is None else bool(executed)
    counted = None if confirmed_b is None else bool(executed_b and confirmed_b)
    scores = _LAST_SCORES[0] or {}
    if scores:
        options = {k: dict(v or {}, score=scores.get(k)) for k, v in options.items()}
    _INTERRUPT_LOG.append(dict(extra or {}, kind=kind, root=root, options=options,
                               chosen=chosen,
                               chosen_context=(options.get(chosen) or {}).get("context"),
                               executed=executed_b, confirmed=confirmed_b, counted=counted,
                               refusal=refusal, latency_ms=latency_ms,
                               policy=_LAST_POLICY[0], ts=time.time()))


def drain_interrupt_records():
    """Take and clear everything recorded since the last drain."""
    out = list(_INTERRUPT_LOG)
    del _INTERRUPT_LOG[:]
    return out


def _options_of(bus, root, ids):
    """{id: {"context","text"}} for the given control ids, read from the tree."""
    out = {}
    try:
        for n in _tree(bus, root, 22, 4000):
            nid = str(n.get("id") or "")
            if nid in ids and nid not in out:
                out[nid] = {"context": (str(n["context"]) if n.get("context") else None),
                            "text": (str(n.get("text") or "") or None)}
    except Exception as e:
        sys.stderr.write("interrupts: option detail read failed on %s -> %r\n" % (root, e))
    for i in ids:
        out.setdefault(i, {"context": None, "text": None})
    return out


DILEMMA_MARKER = "dilemma_active"
DILEMMA_LIST = "dilemma_list"


def _dilemma_options(bus, root):
    """{record: {"path","text","context"}} for each path segment directly under DILEMMA_LIST."""
    tree = _tree(bus, root, 22, 4000)
    texts = [(str(n.get("path") or ""), str(n.get("text") or "").strip())
             for n in tree if str(n.get("text") or "").strip()]
    out = {}
    for n in tree:
        if not n.get("visible") or str(n.get("state")) not in _CLICKABLE:
            continue
        path = str(n.get("path") or "")
        parts = path.split("|")
        if DILEMMA_LIST not in parts:
            continue
        i = parts.index(DILEMMA_LIST)
        if i + 1 >= len(parts):
            continue
        record = parts[i + 1]
        prev = out.get(record)
        if prev is not None and len(prev["path"]) <= len(path):
            continue
        sub = "|".join(parts[:i + 2])
        label = sorted(t for p, t in texts if p == sub or p.startswith(sub + "|"))
        out[record] = {"path": path, "text": (label[0] if label else None),
                       "context": (str(n["context"]) if n.get("context") else None)}
    return out


def _is_dilemma(bus, root):
    """True if any node path under `root` contains DILEMMA_MARKER."""
    return any(DILEMMA_MARKER in str(n.get("path") or "") for n in _tree(bus, root, 22, 4000))


def resolve(bus, max_rounds=6):
    """Clear every interrupt currently on screen. Returns the steps taken ([] = clean screen)."""
    steps = []
    for _ in range(max_rounds):
        before = tuple(roots(bus))
        if before and before == _stuck_sig[0]:
            try:
                n = len(nav.close_popups(bus))
            except Exception as e:
                sys.stderr.write("interrupts: close_popups (memo probe) -> %s\n" % repr(e)[:80])
                n = 0
            if not n:
                return ["stuck_unchanged"]
            _stuck_sig[0] = None
            steps.append("popups_cleared:%d" % n)
            continue
        for r in before:
            if r not in nav.BASE_ROOTS and r not in BENIGN_PANELS:
                nav.dump_screen(bus, r, "interrupt")
        kinds = pending(bus)
        if (not kinds and ALLY_ATTACKED_ROOT not in before
                and PROPOSAL_ROOT not in before):
            _stuck_sig[0] = None
            break
        if "battle" in kinds:
            steps.extend(resolve_battle(bus))
            if tuple(roots(bus)) == before:
                break
            continue
        if ALLY_ATTACKED_ROOT in before:
            s = answer_ally_attacked(bus)
            if s:
                steps.extend(s)
                if tuple(roots(bus)) == before:
                    break
                continue
        if PROPOSAL_ROOT in before:
            s = answer_incoming_proposal(bus)
            if s:
                steps.extend(s)
                if tuple(roots(bus)) == before:
                    break
                continue
        if "diplomacy" in kinds:
            steps.extend(answer_diplomacy(bus))
            if tuple(roots(bus)) == before:
                break
            continue
        s = choose_dilemma(bus, list(before))
        if s:
            steps.extend(s)
            if tuple(roots(bus)) == before:
                break
            continue
        s = acknowledge_war_declared(bus, list(before))
        if s:
            steps.extend(s)
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
        s = cancel_declare_war(bus)
        if s:
            steps.extend(s)
            continue
        odd = [x for x in before if x not in nav.BASE_ROOTS and x not in DIPLOMACY_HUD_ROOTS
               and x not in BENIGN_PANELS]
        if odd:
            drivable = any(_clickable_controls(bus, x) for x in odd)
            steps.append("%s:%s" % ("undismissable" if drivable else "transient", ",".join(odd)))
            if drivable:
                if before != _stuck_sig[0]:
                    evidence(bus, "undismissable")
                _stuck_sig[0] = before
        break
    return steps
