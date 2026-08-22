from __future__ import annotations
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

import trace as trace

_FIND_T = 6.0
_TREE_T = 12.0


def _warn(where, detail):
    sys.stderr.write("nav: %s -> %s\n" % (where, detail))

DISMISS_BUTTON_IDS = frozenset((
    "button_accept",
    "button_ok",
    "button_close",
    "button_tick",
    "button_dismiss",
    "button_acknowledge",
    "button_continue",
))

DISMISS_BUTTON_TEXTS = frozenset((
    "continue", "ok", "okay", "close", "accept", "acknowledge", "dismiss",
))


def _is_dismiss_text(text):
    return str(text or "").strip().lower() in DISMISS_BUTTON_TEXTS


LABEL_IDS = frozenset(("button_txt", "dy_text", "text", "label", "dy_description"))


def _clickable_owner(path, node, by_path):
    if str(node.get("id") or "") not in LABEL_IDS:
        return path
    if "|" not in path:
        return path
    parent_path = path.rsplit("|", 1)[0]
    parent = by_path.get(parent_path)
    if parent and parent.get("visible") and str(parent.get("state")) in _CLICKABLE_STATES:
        return parent_path
    return path


PERSISTENT_ROOTS = frozenset((
    "3d_ui_parent", "hud_campaign", "resources_bar", "menu_bar", "panel_manager",
    "under_advisor_docker", "ai_attack_targets_parent", "mission_indicator_parent",
    "character_map_path_icons", "targeting_interface_dimming", "black_fade",
    "tooltip_default", "tooltip_captive_options", "tutorial_halo_group", "saving_icon",
    "qa_console", "help_panel", "campaign_space_bar_options",
))

_CLICKABLE_STATES = frozenset(("active", "default", "NewState", "selected", "hover", "down"))

TOOLTIP_ROOTS = frozenset(("province_publicorder_tooltip", "TechTooltipPopup",
                           "tooltip_corruption_breakdown",
                           "tooltip_pooled_resource_breakdown"))

BENIGN_PANELS = frozenset(("units_panel", "settlement_panel", "recruitment_options",
                           "influence_gained", "dlc27_hef_sotwt_scrolls_gained",
                           "skaven_revealed_anim",
                           "cinematic_bars", "advice_interface",
                           "movie_overlay_intro_movie")) | TOOLTIP_ROOTS

DECISION_ROOTS = frozenset(("diplomacy_dropdown", "ally_attacked"))

PROTECTED_SURFACES = frozenset(("popup_pre_battle", "popup_battle_results",
                                "settlement_captured", "appoint_new_general"))
ORDER_PENDINGS = frozenset(("PENDING_ATTACK",))

_LUA_ENGINE_PENDING = (
    "local function g(c,p) local ok,v=pcall(function() return c:Call(p) end) "
    "if ok and v~=nil then return tostring(v) end return 'nil' end "
    "local r=cco('CcoCampaignRoot','') local pa=nil "
    "pcall(function() pa=r:Call('PendingActionContext') end) "
    "if not pa then return 'none' end "
    "return g(pa,'IsActive')..'|'..g(pa,'ActionType')")


def engine_pending(bus, tries=2):
    for attempt in range(tries):
        try:
            r = bus.send("eval", _LUA_ENGINE_PENDING, timeout=8.0) or {}
            v = r.get("result")
            if v is not None:
                common.trylog("engine_pending_read", attempt + 1, tries, True)
                return v
        except Exception as e:
            _warn("engine_pending", repr(e)[:80])
        common.trylog("engine_pending_read", attempt + 1, tries, False)
        common.wait("engine_pending_retry", 0.3)
    return None


def pending_blocks(pend):
    if pend is None:
        return True
    s = str(pend)
    return s.startswith("true") and s.split("|")[-1] not in ORDER_PENDINGS


def protected_surface(root, pend):
    if root in PROTECTED_SURFACES:
        return True
    return root == "events" and pending_blocks(pend)

SCREEN_DUMP_DIR = common.SCREEN_DUMP_DIR
_DUMP_MEMO = {}


def dev_mode():
    return str(os.environ.get("TW_DEV", "")).strip() not in ("", "0", "false", "False")


def dump_screen(bus, root, why):
    import hashlib
    import json
    import os
    if not dev_mode():
        return None
    try:
        tr = bus.send("tree", "%s %d %d" % (root, 30, 80000), timeout=_TREE_T) or {}
        nodes = tr.get("nodes") or []
        sig = hashlib.sha1(json.dumps(nodes, sort_keys=True, default=str).encode()).hexdigest()
        prev = _DUMP_MEMO.get(root)
        if prev and prev[0] == sig:
            return prev[1]
        os.makedirs(SCREEN_DUMP_DIR, exist_ok=True)
        path = os.path.join(SCREEN_DUMP_DIR,
                            "%d_%s_%s.json" % (int(time.time() * 1000), why, str(root)[:40]))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "root": root, "why": why,
                       "roots": visible_roots(bus), "nodes": nodes}, fh, default=str)
        _DUMP_MEMO[root] = (sig, path)
        sys.stderr.write("nav: dumped %s (%d nodes) -> %s\n" % (root, len(nodes), path))
        return path
    except Exception as e:
        sys.stderr.write("nav: dump of %s failed -> %s\n" % (root, repr(e)[:90]))
        return None


_SEEN_ROOTS = set()
_IN_CENSUS = [False]


def census_roots(bus, roots=None):
    if not dev_mode() or _IN_CENSUS[0]:
        return []
    _IN_CENSUS[0] = True
    try:
        first = []
        for rid in (visible_roots(bus) if roots is None else roots):
            if not rid or rid in _SEEN_ROOTS:
                continue
            _SEEN_ROOTS.add(rid)
            if dump_screen(bus, rid, "census"):
                first.append(rid)
        if first:
            sys.stderr.write("nav: census captured first sighting of %s\n" % ",".join(first))
        return first
    except Exception as e:
        sys.stderr.write("nav: census failed -> %s\n" % repr(e)[:90])
        return []
    finally:
        _IN_CENSUS[0] = False


def _open_roots(bus):
    r = bus.send("roots", "", timeout=_FIND_T) or {}
    if not r.get("kids"):
        _warn("_open_roots", "empty/None roots reply (bus miss)")
    out = []
    for k in (r.get("kids") or []):
        rid = k.get("id")
        if k.get("visible") and rid and rid not in PERSISTENT_ROOTS:
            out.append(rid)
    return out


def _is_dismiss_id(nid):
    nid = str(nid or "")
    return nid in DISMISS_BUTTON_IDS or nid.startswith("button_ok")


WHITE_TIGER_TITLE = "The White Tiger's Calling"
WHITE_TIGER_TITLE_PATH = ("events|event_layouts|incident_large|incident_large|background|"
                          "header|panel_title|button_txt")
WHITE_TIGER_CONFIRM_PATH = ("events|event_layouts|incident_large|incident_large|background|"
                            "footer|text_button")

DECLARE_WAR_TITLE = "Declare War?"
DECLARE_WAR_ROOT = "move_options"
DECLARE_WAR_TITLE_PATH = "move_options|header|panel_title|title_frame"
DECLARE_WAR_CANCEL_PATH = "move_options|panel|options_bar3|button_txt"


def declare_war_panel(bus):
    tr = bus.send("tree", "%s 24 4000" % DECLARE_WAR_ROOT, timeout=_TREE_T) or {}
    if not tr.get("found"):
        return None
    nodes = tr.get("nodes") or []
    by_path = {str(n.get("path") or ""): n for n in nodes}
    title = by_path.get(DECLARE_WAR_TITLE_PATH)
    cancel = by_path.get(DECLARE_WAR_CANCEL_PATH)
    if (title is not None and title.get("visible")
            and str(title.get("text") or "").strip() == DECLARE_WAR_TITLE
            and cancel is not None and cancel.get("visible")):
        return {"nodes": nodes, "cancel": DECLARE_WAR_CANCEL_PATH,
                "label": str(cancel.get("text") or "cancel").strip() or "cancel"}
    return None


def find_dismiss_buttons(bus, root, max_depth=24, max_nodes=4000):
    tr = bus.send("tree", "%s %d %d" % (root, max_depth, max_nodes), timeout=_TREE_T) or {}
    if not tr.get("nodes"):
        _warn("find_dismiss_buttons(%s)" % root, "empty/None tree reply (bus miss)")
    nodes = tr.get("nodes") or []
    by_path = {str(n.get("path") or ""): n for n in nodes}
    if root == "events":
        title = by_path.get(WHITE_TIGER_TITLE_PATH)
        confirm = by_path.get(WHITE_TIGER_CONFIRM_PATH)
        if (title is not None and title.get("visible")
                and str(title.get("text") or "").strip() == WHITE_TIGER_TITLE
                and confirm is not None and confirm.get("visible")):
            sys.stderr.write("nav: hardcoded dismisser hit -- %r -> %s\n"
                             % (WHITE_TIGER_TITLE, WHITE_TIGER_CONFIRM_PATH))
            return [WHITE_TIGER_CONFIRM_PATH]
    if root == "move_options":
        title = by_path.get(DECLARE_WAR_TITLE_PATH)
        cancel = by_path.get(DECLARE_WAR_CANCEL_PATH)
        if (title is not None and title.get("visible")
                and str(title.get("text") or "").strip() == DECLARE_WAR_TITLE
                and cancel is not None and cancel.get("visible")):
            sys.stderr.write("nav: hardcoded dismisser hit -- %r -> %s\n"
                             % (DECLARE_WAR_TITLE, DECLARE_WAR_CANCEL_PATH))
            return [DECLARE_WAR_CANCEL_PATH]
    hits, seen = [], set()
    for n in nodes:
        if not n.get("visible") or str(n.get("state")) not in _CLICKABLE_STATES:
            continue
        path = str(n.get("path") or "")
        if _is_dismiss_id(n.get("id")):
            target = path
        elif _is_dismiss_text(n.get("text")):
            target = _clickable_owner(path, n, by_path)
        else:
            continue
        if target and target not in seen:
            seen.add(target)
            hits.append(target)
    return hits


def modeval(bus, expr, timeout=8.0):
    try:
        return bus.send("modeval", expr, timeout=timeout) or {}
    except Exception as e:
        _warn("modeval", "%s -> %s" % (expr[:60], repr(e)[:80]))
        return {}


def ccmd(bus, expr, timeout=12.0):
    try:
        return bus.send("ccmd", expr, timeout=timeout) or {}
    except Exception as e:
        _warn("ccmd", "%s -> %s" % (expr[:60], repr(e)[:80]))
        return {}


def is_hud_panel_open(bus, root):
    try:
        r = bus.send("eval",
                     'return tostring(common.get_context_value([[IsHUDPanelOpen("%s")]]))' % root,
                     timeout=8.0) or {}
    except Exception:
        return False
    return str(r.get("result") or "").lower() == "true"


CLOSE_AND_CLEAR = ('Do(DoIf(IsHUDPanelOpen("character_panel") || IsHUDPanelOpen("units_panel"), '
                   'CloseCurrentHUDPanel), DoIf(IsPanelOpen("character_details_panel") == false, '
                   'CampaignRoot.ClearSelection))')


def close_panel(bus, root, settle=0.7):
    before = visible_roots(bus)
    if root not in before:
        return True
    if is_hud_panel_open(bus, root):
        ccmd(bus, "CloseCurrentHUDPanel")
    else:
        modeval(bus, "CampaignUI.ClosePanel('%s') return 'called'" % root)
    return _await_root_gone(bus, root, settle, "panel_close_settle")


def diplomacy_owned(root):
    return root in DECISION_ROOTS or "diplo" in str(root).lower()


def _await_root_gone(bus, root, cap, tag, tick=0.1):
    t0 = time.time()
    while True:
        if root not in _open_roots(bus):
            common.waitlog(tag, time.time() - t0, True, root)
            return True
        if time.time() - t0 >= cap:
            common.waitlog(tag, time.time() - t0, False, root)
            return False
        time.sleep(tick)


def panel_sig(bus, root):
    tr = bus.send("tree", "%s 24 4000" % root, timeout=_TREE_T) or {}
    return tuple((str(n.get("path") or ""), str(n.get("text") or "")[:40])
                 for n in (tr.get("nodes") or [])
                 if n.get("visible") and str(n.get("state")) in _CLICKABLE_STATES)


def await_gone_or_changed(bus, root, before_sig, cap, tag, tick=0.1):
    t0 = time.time()
    while True:
        if root not in _open_roots(bus):
            common.waitlog(tag, time.time() - t0, True, "%s gone" % root)
            return "gone"
        if panel_sig(bus, root) != before_sig:
            common.waitlog(tag, time.time() - t0, True, "%s changed" % root)
            return "changed"
        if time.time() - t0 >= cap:
            common.waitlog(tag, time.time() - t0, False, "%s unchanged" % root)
            return "stuck"
        time.sleep(tick)


def _insist(bus, root, btn, settle, tries=2):
    out = []
    for attempt in range(tries):
        for key in ("space", "escape"):
            try:
                bus.send("key", "@root %s" % key, timeout=_FIND_T)
            except Exception:
                pass
        if _await_root_gone(bus, root, settle, "insist_keys_settle"):
            common.trylog("insist_retry", attempt + 1, tries, True, "%s closed by key" % root)
            return out
        try:
            res = bus.send("click", btn, timeout=_FIND_T) or {}
        except Exception as e:
            _warn("close_popups", "insist click failed on %s -> %s" % (root, repr(e)[:60]))
            common.trylog("insist_retry", attempt + 1, tries, False, "click raised on %s" % root)
            return out
        if res.get("clicked"):
            out.append(btn)
        if _await_root_gone(bus, root, settle, "insist_click_settle"):
            common.trylog("insist_retry", attempt + 1, tries, True, "%s closed by click" % root)
            return out
        common.trylog("insist_retry", attempt + 1, tries, False, "%s still open" % root)
    _warn("close_popups", "%s stayed open after %d insist rounds on %s" % (root, tries, btn))
    return out


def close_popups(bus, max_rounds=4, settle=0.7):
    clicked_paths = []
    protected = set()
    census_roots(bus)
    for _ in range(max_rounds):
        clicked_this_round = False
        pend = engine_pending(bus)
        for root in _open_roots(bus):
            if diplomacy_owned(root):
                continue
            if protected_surface(root, pend):
                protected.add(root)
                continue
            if root not in BASE_ROOTS and root not in BENIGN_PANELS:
                dump_screen(bus, root, "predismiss")
            for btn in find_dismiss_buttons(bus, root):
                sig = panel_sig(bus, root)
                res = bus.send("click", btn, timeout=_FIND_T) or {}
                if res.get("clicked"):
                    clicked_paths.append(btn)
                    clicked_this_round = True
                    outcome = await_gone_or_changed(bus, root, sig, settle,
                                                    "dismiss_click_settle")
                    if outcome == "stuck":
                        clicked_paths += _insist(bus, root, btn, settle)
                    else:
                        break
                else:
                    _warn("close_popups", "dismiss click did not register: %s (%s)" % (btn, res))
        if not clicked_this_round:
            break
    pend = engine_pending(bus)
    for root in _open_roots(bus):
        if root in BASE_ROOTS or root in BENIGN_PANELS or diplomacy_owned(root):
            continue
        if protected_surface(root, pend):
            protected.add(root)
            continue
        if close_panel(bus, root, settle=settle):
            clicked_paths.append("ClosePanel(%s)" % root)
    if protected:
        _warn("close_popups", "left decision surfaces open: %s" % sorted(protected))
    return clicked_paths


UI_BASE_W, UI_BASE_H = 1984.0, 1116.0
CLIENT = (0, 0, 2560, 1440)


def ui_to_screen(ux, uy, client=CLIENT):
    ox, oy, cw, ch = client
    return int(round(ox + ux * cw / UI_BASE_W)), int(round(oy + uy * ch / UI_BASE_H))


BUS_CMD = {"click": "click", "dclick": "dclick", "rclick": "rclick",
           "hover": "hover", "unhover": "unhover"}


def bus_input(bus, where, path, action="click", timeout=8.0):
    cmd = BUS_CMD.get(action)
    if bus is None or cmd is None or not path:
        _warn(where, "no bus/command/path for %r on %r" % (action, path))
        return None
    try:
        r = bus.send(cmd, path, timeout=timeout) or {}
    except Exception as e:
        _warn(where, "%s raised: %s" % (cmd, repr(e)[:100]))
        return None
    if r.get("clicked") or r.get("sent"):
        return r
    _warn(where, "%s not delivered: %s" % (cmd, str(r)[:160]))
    return None


def bus_click(bus, path):
    r = bus.send("click", path, timeout=_FIND_T) or {}
    trace.click("bus", path, result=r)
    if not r.get("clicked"):
        _warn("bus_click", "click reply missing/not clicked: %s (%s)" % (path, r))
    return r


def find_rect(bus, root, match):
    tr = bus.send("tree", "%s 30 9000" % root, timeout=_TREE_T) or {}
    if not tr.get("nodes"):
        _warn("find_rect(%s, %s)" % (root, match), "empty/None tree reply (bus miss)")
    fallback = None
    for n in (tr.get("nodes") or []):
        p = str(n.get("path") or "")
        if n.get("id") == match or p == match or p.endswith("|" + match):
            if n.get("visible") and n.get("x") is not None:
                return n
            fallback = fallback or n
    if fallback is not None:
        _warn("find_rect(%s, %s)" % (root, match), "only a non-visible/position-less node matched")
    else:
        _warn("find_rect(%s, %s)" % (root, match), "no node matched -> None")
    return fallback


def hover(bus, root, match, frac=(0.5, 0.5), settle=0.9):
    n = find_rect(bus, root, match)
    if not n or n.get("x") is None:
        _warn("hover(%s, %s)" % (root, match), "find_rect returned nothing/position-less -> None")
        return None
    r = bus_input(bus, "nav.py:hover(%s,%s)" % (root, match), n.get("path"), action="hover")
    common.wait("hover_settle", settle, match)
    return {"path": n.get("path"), "delivered": bool(r)}


BASE_ROOTS = frozenset((
    "ai_attack_targets_parent", "3d_ui_parent", "mission_indicator_parent", "hud_campaign",
    "menu_bar", "panel_manager", "under_advisor_docker", "tooltip_default", "saving_icon",
    "ai_turns", "campaign", "tutorial_halo_group", "text_pointer_parent",
    "character_map_path_icons", "targeting_interface_dimming", "black_fade", "resources_bar",
    "help_panel", "qa_console", "campaign_space_bar_options",
))


def visible_roots(bus):
    r = bus.send("roots", "", timeout=_FIND_T) or {}
    if not r.get("kids"):
        _warn("visible_roots", "empty/None roots reply (bus failure would otherwise read as a clean screen)")
    return [k.get("id") for k in (r.get("kids") or []) if k.get("visible") and k.get("id")]


def deselect(bus):
    return ccmd(bus, CLOSE_AND_CLEAR)


def capital_region(bus):
    s = bus.send("setts", "", timeout=_FIND_T) or {}
    if not s.get("setts"):
        _warn("capital_region", "empty/None setts reply (bus miss)")
    setts = s.get("setts") or []
    for x in setts:
        if x.get("capital") and x.get("region"):
            return x["region"]
    if not setts:
        _warn("capital_region", "no owned settlements -> None")
    return setts[0]["region"] if setts else None


if __name__ == "__main__":
    sys.path.insert(0, common.BUS)
    from bus import Bus
    _bus = Bus()
    before = _open_roots(_bus)
    print("open popup roots BEFORE:", before)
    closed = close_popups(_bus)
    print("clicked dismiss buttons:", closed)
    print("open popup roots AFTER :", _open_roots(_bus))
