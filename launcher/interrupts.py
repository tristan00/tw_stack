from __future__ import annotations

import json
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
    "button_hef_intrigue_at_the_court",
    "button_white_tower",
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
    "button_nakai_temples",
    "button_zoom",
    "button_txt",
))
DISPLAY_PREFIXES = ("unit_",)


def _unknown_controls(ctrls, known):
    return sorted(i for i in ctrls
                  if str(i).startswith("button_")
                  and i not in known and i not in DISPLAY_CONTROLS)


_CHOOSER = [None]
_CAMPAIGN = [None]
_WORLD = [None]
_LAST_POLICY = [None]
_LAST_SCORES = [None]


def set_chooser(fn):
    _CHOOSER[0] = fn


def set_snapshot(campaign, world=None):
    _CAMPAIGN[0] = campaign
    _WORLD[0] = world


def _campaign_hint():
    return _CAMPAIGN[0]


def _world_hint():
    return _WORLD[0]


def _choose(screen, options, campaign=None, panel=None, meta=None, live=None):
    opts = sorted(options)
    if not opts:
        return None
    fn = _CHOOSER[0]
    if fn is None:
        raise RuntimeError(
            "no interrupt chooser installed -- the advisor owns this decision and must call "
            "interrupts.set_chooser() before driving any screen. Refusing to invent a policy in "
            "the launcher (screen=%s, offered=%s)" % (screen, opts))
    if live is None:
        raise RuntimeError(
            "interrupt screen %s called _choose() with no live-option reader. Every interrupt "
            "screen must be able to re-read what it is offering at pick time, otherwise a "
            "recommendation cannot be checked against the panel and phantom options enter the "
            "corpus as confirmed actions (offered=%s)" % (screen, opts))
    got, policy, scores = fn(screen, opts, campaign, panel, _world_hint(), meta)
    if got not in options:
        raise RuntimeError(
            "chooser returned %r which is NOT among the legal options %s for %s. Taking it anyway "
            "is how button_attack eventually gets clicked." % (got, opts, screen))
    present = live()
    if got not in present:
        raise PhantomOption(
            "PHANTOM OPTION on %s: the advisor recommended %r but the panel that is open does NOT "
            "offer it. offered_to_advisor=%s live_on_panel=%s -- the option list did not come from "
            "the screen currently up, so the pick, the click and the recorded outcome would all be "
            "fiction. Refusing to click it or record it."
            % (screen, got, opts, sorted(present)))
    _LAST_POLICY[0] = policy
    _LAST_SCORES[0] = dict(scores or {})
    sys.stderr.write("interrupts: SCREEN %s offered=%s -> %r (%s) scores=%s\n"
                     % (screen, opts, got, policy, _fmt_scores(scores)))
    return got


def _fmt_scores(scores):
    out = {}
    for k, v in sorted((scores or {}).items()):
        if isinstance(v, dict):
            out[k] = {kk: round(vv, 3) for kk, vv in v.items() if isinstance(vv, (int, float))}
        elif isinstance(v, (int, float)):
            out[k] = round(v, 4)
        else:
            out[k] = v
    return out


UNHANDLED_LOG = r"D:/twdata/runs/human/unhandled_screens.jsonl"


def _report_unhandled(bus, screen, unknown, offered, root=None):
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
    pass


class PhantomOption(UnhandledScreen):
    pass
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


def _refusal(gone, clicked):
    if gone is None:
        return "confirm_unreadable_bus_failure"
    if gone:
        return None
    return "command_silently_refused" if clicked else "execute_failed"


def _root_gone(bus, root, tries=3, pause=0.4):
    for _ in range(tries):
        try:
            return root not in nav.visible_roots(bus)
        except Exception:
            time.sleep(pause)
    return None


def _wait_root(bus, root, tries=30, pause=1.0):
    for _ in range(tries):
        if root in roots(bus):
            return True
        time.sleep(pause)
    return False


CLICK_LOG = []


_UI_HIDING = [None]

_LUA_UI_HIDING = ("local ok,v=pcall(function() return cm:is_ui_hiding_enabled() end) "
                  "if not ok then return 'nil' end return tostring(v)")


def _sample_ui_hiding(bus, after_path):
    try:
        r = bus.send("eval", _LUA_UI_HIDING, timeout=6.0) or {}
    except Exception:
        return None
    raw = str(r.get("result"))
    now = True if raw == "true" else (False if raw == "false" else None)
    prev = _UI_HIDING[0]
    _UI_HIDING[0] = now
    if now is True and prev is not True:
        sys.stderr.write("interrupts: !! UI HIDING FLIPPED ON immediately after this click: %s "
                         "(previous sample=%s)\n" % (after_path, prev))
    try:
        import trace as TR
        TR.emit("ui_hiding_sample", path=after_path, ui_hiding=now, previous=prev,
                flipped_on=bool(now is True and prev is not True))
    except Exception:
        pass
    return now


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
    _sample_ui_hiding(bus, path)
    deadline = time.time() + settle
    while time.time() < deadline:
        time.sleep(0.3)
        if tuple(roots(bus)) != before:
            break
    return ok


def evidence(bus, why, shots_dir=None):
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
                        path], capture_output=True, text=True, timeout=45,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        rep["screenshot"] = path if os.path.exists(path) else None
    except Exception as e:
        rep["screenshot_error"] = repr(e)[:120]
    sys.stderr.write("interrupts: STUCK (%s) roots=%s shot=%s\n  recent clicks: %s\n"
                     % (why, rep["roots"], rep.get("screenshot"), rep["clicks"]))
    return rep


def _found(bus, path):
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


def live_control_ids(bus, root):
    return {str(n.get("id") or "") for n in _tree(bus, root)
            if n.get("visible") and str(n.get("state")) in _CLICKABLE}


def live_option_texts(bus, root):
    out = set()
    for n in _tree(bus, root, 22, 3000):
        if str(n.get("id")) != "dy_option" or not n.get("visible"):
            continue
        if str(n.get("state")) not in _CLICKABLE:
            continue
        t = str(n.get("text") or "").strip().lower()
        if t:
            out.add(t)
    return out


def cancel_declare_war(bus):
    steps = []
    for root in [x for x in roots(bus)
                 if x not in nav.BASE_ROOTS and x not in DIPLOMACY_HUD_ROOTS
                 and x not in BENIGN_PANELS]:
        tree = _tree(bus, root)
        targets = {}
        for n in tree:
            nid = str(n.get("id") or "")
            txt = str(n.get("text") or "").strip().lower()
            if not n.get("visible") or str(n.get("state")) not in _CLICKABLE:
                continue
            if (txt in ("cancel move", "cancel") or "cancel_move" in nid.lower()
                    or "button_cancel" in nid.lower()) and nid not in targets:
                targets[nid] = n.get("path")
        if not targets:
            continue
        labels = _control_labels(tree, targets)
        opts = {k: {"context": None, "text": labels.get(k) or k,
                    "dilemma_id": root, "option_id": k, "payload": [], "subtree": []}
                for k in targets}
        key = _choose("declare_war_cancel", sorted(opts), _campaign_hint(), meta=opts,
                      live=lambda: live_control_ids(bus, root))
        t0 = time.time()
        clicked = _click(bus, targets[key], settle=2.0)
        gone = _root_gone(bus, root)
        _record_choice("declare_war_cancel", root, opts, key,
                       extra={"tree": tree, "root_context": root},
                       executed=clicked, confirmed=gone,
                       refusal=_refusal(gone, clicked),
                       latency_ms=int((time.time() - t0) * 1000))
        if clicked:
            steps.append("declare_war_cancelled:%s" % root)
    return steps


def diplomacy_roots(bus, r=None):
    r = roots(bus) if r is None else r
    return [x for x in r if "diplo" in str(x).lower() and x not in DIPLOMACY_HUD_ROOTS]


DEFEAT_ROOT_TOKENS = ("defeat", "victory", "campaign_end", "game_over", "campaign_result")


def defeat_screen(bus, r=None):
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
    out = {}
    for n in _tree(bus, "popup_pre_battle", 30, 40000):
        i = str(n.get("id") or "")
        if i in ("dy_result", "dy_casualties") and n.get("visible"):
            out[i[3:]] = {"text": str(n.get("text") or "").strip() or None,
                          "state": str(n.get("state") or "") or None}
    return out


def resolve_prebattle(bus):
    ctrls = _clickable_controls(bus, "popup_pre_battle")
    unknown = _unknown_controls(ctrls, KNOWN_PREBATTLE_CONTROLS)
    if unknown:
        _report_unhandled(bus, "pre_battle", unknown, sorted(ctrls), root="popup_pre_battle")
        raise UnhandledScreen(
            "pre-battle screen offers UNHANDLED option(s) %s -- add them to PREBATTLE_PREFERENCE "
            "only if they resolve the battle WITHOUT entering it, otherwise to FORBIDDEN_CLICK_IDS. "
            "clickable=%s" % (unknown, sorted(ctrls)))
    legal = [i for i in PREBATTLE_CHOOSABLE if i in ctrls and i not in PREBATTLE_NEVER]
    if not legal:
        sys.stderr.write("interrupts: pre-battle offers no usable control -- clickable=%s\n"
                         % ",".join(sorted(ctrls)[:10]))
        return False
    forecast = prebattle_forecast(bus)
    m = _sticky_choice("pre_battle", "popup_pre_battle", legal, forecast,
                       live=lambda: live_control_ids(bus, "popup_pre_battle"))
    if m["tries"] > _ANSWER_TRIES:
        sys.stderr.write("interrupts: pre_battle held pick %r already failed %d tries -- "
                         "leaving the screen to the watchdog\n" % (m["want"], _ANSWER_TRIES))
        return False
    target = m["want"]
    _LAST_POLICY[0], _LAST_SCORES[0] = m["policy"], dict(m["scores"] or {})
    final_try = m["tries"] >= _ANSWER_TRIES
    opts = _options_of(bus, "popup_pre_battle", legal)
    tree = _tree(bus, "popup_pre_battle", 30, 40000)
    offered_all = sorted({str(n.get("id")) for n in tree
                          if str(n.get("id") or "").startswith("button_") and n.get("visible")})
    t0 = time.time()
    off = bus.out_offset()
    clicked = _click(bus, ctrls[target], settle=1.5)
    if not clicked:
        if final_try:
            _record_choice("pre_battle", "popup_pre_battle", opts, target,
                           extra={"panel": forecast, "tree": tree, "controls": offered_all},
                           executed=False, confirmed=False, refusal="execute_failed",
                           latency_ms=int((time.time() - t0) * 1000))
        return False
    if target in ("button_continue_siege", "button_surround", "button_retreat",
                  "button_sally_forth", "button_maintain_blockade", "button_demand_surrender"):
        name = target[len("button_"):]
        bus.wait_row(("panel",), timeout=4.0, offset=off,
                     pred=lambda r: r.get("opened") is False
                     and "pre_battle" in str(r.get("name") or ""))
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
    if ok:
        _ANSWER_MEMO.pop("popup_pre_battle", None)
    if ok or final_try:
        _record_choice("pre_battle", "popup_pre_battle", opts, target,
                       extra={"panel": forecast, "tree": tree, "controls": offered_all},
                       executed=clicked, confirmed=bool(ok),
                       refusal=None if ok else "command_silently_refused",
                       latency_ms=int((time.time() - t0) * 1000))
    return outcome


def _results_appeared(bus, offset, timeout=20.0):
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
    out = {}
    for n in _tree(bus, root, 22, 4000):
        if not n.get("visible") or str(n.get("state")) not in _CLICKABLE:
            continue
        nid = str(n.get("id") or "")
        if nid and nid not in out:
            out[nid] = n.get("path")
    return out


def handle_results(bus):
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
        target = (_choose("battle_results", fates, _campaign_hint(),
                          live=lambda: live_control_ids(bus, "popup_battle_results")) if fates
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
    opts = {}
    for n in _tree(bus, "settlement_captured", 22, 3000):
        if str(n.get("id")) != "dy_option" or not n.get("text"):
            continue
        if not n.get("visible") or str(n.get("state")) not in _CLICKABLE:
            continue
        opts[str(n["text"]).strip().lower()] = str(n["path"]).rsplit("|dy_option", 1)[0]
    if not opts:
        sys.stderr.write("interrupts: settlement_captured has no clickable dy_option nodes\n")
        return None
    detail = {k: {"context": None, "text": k} for k in opts}
    want = _choose("occupation", sorted(opts), _campaign_hint(),
                   live=lambda: live_option_texts(bus, "settlement_captured"))
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
    steps = []
    for root in open_roots:
        if root in nav.BASE_ROOTS or root in DIPLOMACY_HUD_ROOTS or root in BENIGN_PANELS:
            continue
        tree = _tree(bus, root)
        if not any(WAR_DECLARED_MARKER in str(n.get("text") or "").lower() for n in tree):
            continue
        targets = {}
        for n in tree:
            nid = str(n.get("id") or "")
            if (n.get("visible") and str(n.get("state")) in _CLICKABLE
                    and any(t in nid.lower() for t in ACCEPT_TOKENS)
                    and nid not in targets):
                targets[nid] = n.get("path")
        if not targets:
            _report_unhandled(bus, "war_declared", ["no accept-family control"],
                              sorted(_clickable_controls(bus, root)), root=root)
            raise UnhandledScreen(
                "war_declared notice on %s offers no accept-family control -- refusing to leave a "
                "blocking screen unanswered or to guess at it. clickable=%s"
                % (root, sorted(_clickable_controls(bus, root))))
        labels = _control_labels(tree, {k: v for k, v in targets.items()})
        opts = {k: {"context": None, "text": labels.get(k) or k,
                    "dilemma_id": root, "option_id": k, "payload": [], "subtree": []}
                for k in targets}
        key = _choose("war_declared", sorted(opts), _campaign_hint(), meta=opts,
                      live=lambda: live_control_ids(bus, root))
        t0 = time.time()
        clicked = _click(bus, targets[key], settle=2.0)
        gone = _root_gone(bus, root)
        _record_choice("war_declared", root, opts, key,
                       extra={"tree": tree, "root_context": root},
                       executed=clicked, confirmed=gone,
                       refusal=_refusal(gone, clicked),
                       latency_ms=int((time.time() - t0) * 1000))
        if clicked:
            steps.append("war_declared_acknowledged:%s" % root)
    return steps


def answer_diplomacy(bus):
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
        want = _choose("diplomacy", sorted(answers), _campaign_hint(),
                       live=lambda: live_control_ids(bus, root))
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
                       refusal=_refusal(gone, clicked),
                       latency_ms=int((time.time() - t0) * 1000))
        try:
            import diplo_stream as DS
            keys = DS.faction_keys_in(tree)
            for k in keys:
                DS.track(k)
            DS.emit("deal", channel="diplomacy_hud", root=root, chosen=want, answer=kind,
                    options=sorted(answers), executed=clicked, confirmed=gone,
                    faction_keys=keys)
        except Exception as e:
            sys.stderr.write("interrupts: diplo_stream emit (diplomacy_hud) -> %s\n"
                             % repr(e)[:80])
    return steps


PROPOSAL_ROOT = "diplomacy_dropdown"
PROPOSAL_ANSWER_IDS = frozenset(("button_accept", "button_cancel"))
PROPOSAL_KNOWN_NONANSWERS = frozenset(("button_ok_war_declared", "button_ok_declare",
                                       "button_cancel_declare"))

_ANSWER_MEMO = {}
_ANSWER_TRIES = 2
_ANSWER_TTL = 180.0


def reset_answers():
    _ANSWER_MEMO.clear()


def _sticky_choice(screen, root, options, panel=None, live=None):
    m = _ANSWER_MEMO.get(root)
    if m and m.get("want") in options and time.time() - m.get("ts", 0) <= _ANSWER_TTL:
        m["tries"] += 1
        return m
    m = {"want": _choose(screen, sorted(options), _campaign_hint(), panel, live=live),
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


def _screen_facts(tree):
    facts = {"strength_ranks": [], "reliability": [], "settlements": None}
    for n in tree or []:
        t = str(n.get("text") or "").strip()
        if t.startswith("Strength Rank:"):
            facts["strength_ranks"].append(t.split(":", 1)[1].strip())
        elif t.startswith("Reliability:"):
            facts["reliability"].append(t.split(":", 1)[1].strip())
        elif (str(n.get("id")) == "opponent_settlement_number"
                and facts["settlements"] is None and t):
            facts["settlements"] = t
    return facts


def _drive_decision(bus, root, kind, opts, detail, extra):
    steps = []
    m = _sticky_choice(kind, root, opts, live=lambda: live_control_ids(bus, root))
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
                       refusal=_refusal(gone, clicked),
                       latency_ms=int((time.time() - t0) * 1000))
        try:
            import diplo_stream as DS
            tree = extra.get("tree") or []
            keys = DS.faction_keys_in(tree)
            for k in keys:
                DS.track(k)
            DS.emit("deal", channel=kind, chosen=want, answer=detail[want]["answer"],
                    options=sorted(opts), executed=clicked, confirmed=gone,
                    policy=_LAST_POLICY[0],
                    proposer=extra.get("proposer"), speech=extra.get("speech"),
                    attitude=extra.get("attitude"), variant=extra.get("variant"),
                    facts=_screen_facts(tree), faction_keys=keys,
                    pair=({k: DS.pair_relations(bus, k) for k in keys[:4]}
                          if gone else None))
        except Exception as e:
            sys.stderr.write("interrupts: diplo_stream emit failed -> %s\n" % repr(e)[:90])
    if gone:
        _ANSWER_MEMO.pop(root, None)
    return steps


def _first_text(nodes, node_id, path_token=None):
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
    extra = {"tree": tree,
             "proposer": _first_text(tree, "faction_title", "faction_right_status_panel"),
             "speech": _first_text(tree, "dy_text", "speech_bubble"),
             "attitude": _first_text(tree, "dy_value")}
    return _drive_decision(bus, PROPOSAL_ROOT, kind, opts, detail, extra)


ALLY_ATTACKED_ROOT = "ally_attacked"
ALLY_ATTACKED_DECISIONS = frozenset(("button_join_aggressor", "button_join_defender",
                                     "decline_button"))
ALLY_ATTACKED_FURNITURE = frozenset(("button_txt", "button_flame", "button_parent",
                                     "button_default_selection"))


def ally_attacked_options(nodes):
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
    if not out:
        raise UnhandledScreen(
            "ally_attacked offers no clickable decision at all -- refusing to guess. "
            "visible known=%s" % sorted(out))
    return out


def answer_ally_attacked(bus):
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
    _stuck_sig[0] = None


def choose_dilemma(bus, open_roots):
    steps = []
    for root in open_roots:
        if (root in nav.BASE_ROOTS or root in DIPLOMACY_HUD_ROOTS or root in BENIGN_PANELS
                or root in BATTLE_ROOTS):
            continue
        dilemma = _is_dilemma(bus, root)
        if not dilemma and not (root == "events" and any(
                str(n.get("text") or "").strip() == "Purification Chant"
                for n in _tree(bus, root) if n.get("visible"))):
            continue
        found = _dilemma_options(bus, root) if dilemma else {}
        if not found:
            ctrls = _clickable_controls(bus, root)
            actionable = {i: p for i, p in ctrls.items()
                          if i not in SCROLL_CHROME_IDS and i not in DISPLAY_CONTROLS}
            if not actionable:
                continue
            ack = sorted(i for i in actionable
                         if any(t in i.lower() for t in ACCEPT_TOKENS)
                         or i in ("button_dismiss", "button_close"))
            if len(actionable) == 1:
                ack = sorted(actionable)
            if ack and len(ack) == len(actionable):
                tree = _tree(bus, root)
                labels = _control_labels(tree, actionable)
                opts = {i: {"context": None, "text": labels.get(i) or i,
                            "dilemma_id": root, "option_id": i,
                            "payload": [], "subtree": []} for i in ack}
                key = _choose("event_ack", sorted(opts), _campaign_hint(), meta=opts,
                              live=lambda: live_control_ids(bus, root))
                t0 = time.time()
                clicked = _click(bus, actionable[key], settle=2.0)
                gone = _root_gone(bus, root)
                _record_choice("event_ack", root, opts, key,
                               extra={"tree": tree, "root_context": root, "dilemma_id": root},
                               executed=clicked, confirmed=gone,
                               refusal=_refusal(gone, clicked),
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
        before_tree = _tree(bus, root)
        _require_choice_data(bus, root, found)
        dilemma_id = sorted({v["dilemma_id"] for v in found.values()})[0]
        detail = {"root": root, "root_context": dilemma_id,
                  "options": {k: {"context": v["context"], "text": v["text"],
                                  "dilemma_id": v["dilemma_id"], "option_id": v["option_id"],
                                  "payload": v["payload"], "subtree": v["subtree"]}
                              for k, v in found.items()}}
        key = _choose("dilemma", sorted(opts), _campaign_hint(), meta=found,
                      live=lambda: set(_dilemma_options(bus, root) or {}))
        sys.stderr.write("interrupts: dilemma %s (%s) -- %d options -> %r\n"
                         % (root, dilemma_id, len(opts), key))
        t0 = time.time()
        clicked = _click(bus, opts[key], settle=2.5)
        gone = _root_gone(bus, root)
        if clicked:
            steps.append("dilemma:%s:%s" % (root, key))
        _record_choice("dilemma", root, detail["options"], key,
                       extra={"root_context": detail.get("root_context"),
                              "dilemma_id": dilemma_id, "tree": before_tree},
                       executed=clicked, confirmed=gone,
                       refusal=_refusal(gone, clicked),
                       latency_ms=int((time.time() - t0) * 1000))
        break
    return steps


def _control_labels(tree, actionable):
    texts = {str(n.get("path") or ""): str(n.get("text") or "").strip()
             for n in tree if str(n.get("text") or "").strip() and n.get("visible")}
    out = {}
    for cid, path in actionable.items():
        label = texts.get(path)
        if not label:
            label = next((t for p, t in texts.items() if p.startswith(path + "|")), None)
        if label:
            out[cid] = label
    return out


def _require_choice_data(bus, root, found):
    problems = []
    ids = sorted({v.get("dilemma_id") for v in found.values()})
    if len(ids) != 1 or not ids[0]:
        problems.append("dilemma_id not single and non-empty: %s" % ids)
    opt_ids = [v.get("option_id") for v in found.values()]
    if any(not o for o in opt_ids):
        problems.append("option_id missing on %d of %d options"
                        % (sum(1 for o in opt_ids if not o), len(opt_ids)))
    if len(set(opt_ids)) != len(opt_ids):
        problems.append("option_id not unique: %s" % sorted(opt_ids))
    unlabelled = sorted(v.get("option_id") for v in found.values() if not v.get("text"))
    if unlabelled:
        problems.append("no label text for option(s) %s" % unlabelled)
    if not problems:
        return
    _report_unhandled(bus, "dilemma", problems, sorted(found), root=root)
    raise UnhandledScreen(
        "DILEMMA DATA INCOMPLETE on %s with %d options -- %s. Refusing to answer or record: an "
        "unlabelled or unidentified choice makes the whole corpus unusable. records=%s"
        % (root, len(found), "; ".join(problems), sorted(found)))


_INTERRUPT_LOG = []


def _record_choice(kind, root, options, chosen, extra=None,
                   executed=None, confirmed=None, refusal=None, latency_ms=None):
    confirmed_b = None if confirmed is None else bool(confirmed)
    executed_b = None if executed is None else bool(executed)
    counted = None if confirmed_b is None else bool(executed_b and confirmed_b)
    scores = _LAST_SCORES[0] or {}
    if scores:
        options = {k: (dict(v or {}, **scores[k]) if isinstance(scores.get(k), dict)
                       else dict(v or {}, score=scores.get(k)))
                   for k, v in options.items()}
    _INTERRUPT_LOG.append(dict(extra or {}, kind=kind, root=root, options=options,
                               chosen=chosen,
                               chosen_context=(options.get(chosen) or {}).get("context"),
                               executed=executed_b, confirmed=confirmed_b, counted=counted,
                               refusal=refusal, latency_ms=latency_ms,
                               policy=_LAST_POLICY[0], ts=time.time()))


def drain_interrupt_records():
    out = list(_INTERRUPT_LOG)
    del _INTERRUPT_LOG[:]
    return out


def _options_of(bus, root, ids):
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





def _read_tree_or_die(bus, root, tries=3, pause=0.4, timeout=2.0):
    for attempt in range(tries):
        try:
            r = bus.send("tree", "%s %d %d" % (root, 22, 4000), timeout=timeout) or {}
        except Exception as e:
            sys.stderr.write("interrupts: dilemma tree read %d/%d on %s -> %s\n"
                             % (attempt + 1, tries, root, repr(e)[:80]))
            time.sleep(pause)
            continue
        nodes = r.get("nodes") or []
        if nodes:
            return nodes
        sys.stderr.write("interrupts: dilemma tree read %d/%d on %s -> empty reply\n"
                         % (attempt + 1, tries, root))
        time.sleep(pause)
    raise UnhandledScreen(
        "could not read the %s tree in %d attempts at %.0fs -- refusing to answer a dilemma whose "
        "options were never read. This is a bus failure, not an empty panel."
        % (root, tries, timeout))


def _dilemma_options(bus, root):
    tree = _read_tree_or_die(bus, root)
    texts = [(str(n.get("path") or ""), str(n.get("text") or "").strip())
             for n in tree if str(n.get("text") or "").strip() and n.get("visible")]
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
        label = next((t for p, t in texts if p == sub + "|choice_button|button_txt"), None)
        payload = [t for p, t in texts if p.startswith(sub + "|payload_list|")]
        out[record] = {"path": path, "text": label, "payload": payload,
                       "subtree": [dict(x) for x in tree
                                   if str(x.get("path") or "").startswith(sub)],
                       "context": (str(n["context"]) if n.get("context") else None)}
    return _with_identity(out)


def _with_identity(found):
    import os as _os
    records = sorted(found)
    shared = _os.path.commonprefix(records) if len(records) > 1 else ""
    for r in records:
        found[r]["dilemma_id"] = shared or r
        found[r]["option_id"] = r[len(shared):] or r
    return found




def _is_dilemma(bus, root):
    tree = _read_tree_or_die(bus, root)
    return any(DILEMMA_LIST + "|" in str(n.get("path") or "") and n.get("visible")
               for n in tree)


def resolve(bus, max_rounds=6):
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
                _report_unhandled(bus, "stuck_unchanged",
                                  ["no control cleared the screen on a second pass"],
                                  sorted({k for r in before for k in _clickable_controls(bus, r)}),
                                  root=",".join(before))
                raise UnhandledScreen(
                    "screen UNCHANGED after a full resolve pass and close_popups cleared nothing "
                    "-- roots=%s. Refusing to keep acting into a blocking screen: every action "
                    "from here is recorded against a state the agent cannot see." % (list(before),))
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
            drivable = {x: _clickable_controls(bus, x) for x in odd}
            drivable = {k: v for k, v in drivable.items() if v}
            steps.append("%s:%s" % ("undismissable" if drivable else "transient", ",".join(odd)))
            if drivable:
                if before != _stuck_sig[0]:
                    evidence(bus, "undismissable")
                    _stuck_sig[0] = before
                else:
                    _report_unhandled(bus, "undismissable",
                                      sorted({k for v in drivable.values() for k in v}),
                                      sorted(drivable), root=",".join(sorted(drivable)))
                    raise UnhandledScreen(
                        "UNDISMISSABLE screen persisted across two resolve passes with clickable "
                        "controls that none of the handlers claimed -- %s. Refusing to continue: "
                        "actions taken now are recorded against a blocked screen."
                        % json.dumps({k: sorted(v) for k, v in drivable.items()})[:400])
        break
    return steps
