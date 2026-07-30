r"""nav.py -- deterministic in-campaign navigation via the command bus (FRESH build).

The single primitive is the mod's UI driver over the bus:
    bus.send("roots", "")            -> {"kids":[{id,visible,children}, ...]}   top-level components
    bus.send("tree",  "<path> D N")  -> {"nodes":[{id,path,visible,state,text,tooltip,children}, ...]}
    bus.send("find",  "<path>")      -> one component's describe()
    bus.send("click", "<path>")      -> SimulateLClick; {"found":bool,"clicked":bool}

Everything here composes those. First tool: close_popups() -- clear notification/message popups
(e.g. the "Marked for Death" event) so the screen reaches a clean, interactable state.

DESIGN NOTES
- A popup lives in its OWN top-level ROOT (the "Marked for Death" event is the `events` root; its
  dismiss control is `events|button_set|accept_holder|button_accept`). So we scan open roots, skip
  the persistent HUD/map roots that are never popups, and click the dismiss button in the rest.
- Generalised by a curated set of DISMISS button ids (not this one popup). These are pure
  acknowledge/close controls -- NOT decision buttons: we deliberately do NOT auto-answer DILEMMAS or
  other real choices (those belong to the advisor). Add ids here as new popups are observed.
"""
from __future__ import annotations
import sys, time

_FIND_T = 6.0
_TREE_T = 12.0


def _warn(where, detail):
    """Log (never swallow silently) a soft failure -- element/query not found or errored."""
    sys.stderr.write("nav: %s -> %s\n" % (where, detail))

# Dismiss / acknowledge / close controls across WH3 popups. Pure "make it go away" buttons only --
# never a decision-accept. Extend as new popups are seen; keep it a set of stable ids.
DISMISS_BUTTON_IDS = frozenset((
    "button_accept",        # event feed notifications (Marked for Death, New Targets, ...)
    "button_ok",            # generic info dialogs
    "button_close",
    "button_tick",          # confirm/close on some panels
    "button_dismiss",
    "button_acknowledge",
    "button_continue",      # some info sequences
))

# Top-level roots that are the persistent campaign UI, NEVER a popup -- do not scan/close these.
# NOTE: `advice_interface` is deliberately NOT here. It is a persistent HUD element (the advisor)
# but it ALSO shows dismissable advice popups (e.g. the legendary-lord intro quote "Fear the
# shadows... Alith Anar!"); its close control `…|maximised_button_docker|button_close` is in
# DISMISS_BUTTON_IDS and is only VISIBLE while advice is showing, so the generic scan dismisses the
# advice without ever touching the minimised advisor (its other buttons -- toggle_options / prev /
# next -- are not in the dismiss set).
PERSISTENT_ROOTS = frozenset((
    "3d_ui_parent", "hud_campaign", "resources_bar", "menu_bar", "panel_manager",
    "under_advisor_docker", "ai_attack_targets_parent", "mission_indicator_parent",
    "character_map_path_icons", "targeting_interface_dimming", "black_fade",
    "tooltip_default", "tooltip_captive_options", "tutorial_halo_group", "saving_icon",
    "qa_console", "help_panel", "campaign_space_bar_options",
))

# A state we consider clickable (not disabled/greyed).
_CLICKABLE_STATES = frozenset(("active", "default", "NewState", "selected", "hover", "down"))


def _open_roots(bus):
    """Currently-open top-level roots that are candidate popups (visible, not persistent)."""
    r = bus.send("roots", "", timeout=_FIND_T) or {}
    if not r.get("kids"):
        _warn("_open_roots", "empty/None roots reply (bus miss)")
    out = []
    for k in (r.get("kids") or []):
        rid = k.get("id")
        if k.get("visible") and rid and rid not in PERSISTENT_ROOTS:
            out.append(rid)
    return out


def find_dismiss_buttons(bus, root, max_depth=24, max_nodes=4000):
    """Every VISIBLE dismiss/close button (by id) inside `root`. Returns [component_path, ...]."""
    tr = bus.send("tree", "%s %d %d" % (root, max_depth, max_nodes), timeout=_TREE_T) or {}
    if not tr.get("nodes"):
        _warn("find_dismiss_buttons(%s)" % root, "empty/None tree reply (bus miss)")
    hits = []
    for n in (tr.get("nodes") or []):
        if (n.get("id") in DISMISS_BUTTON_IDS
                and n.get("visible")
                and str(n.get("state")) in _CLICKABLE_STATES):
            hits.append(n.get("path"))
    return hits


def close_popups(bus, max_rounds=8, settle=0.7):
    """Scan open popup roots and click their dismiss buttons until the screen is clear.

    Loops (popups queue: closing one reveals the next) up to max_rounds. Returns the list of
    component paths actually clicked, in order. Idempotent: no popups -> returns [].
    """
    clicked_paths = []
    for _ in range(max_rounds):
        clicked_this_round = False
        for root in _open_roots(bus):
            for btn in find_dismiss_buttons(bus, root):
                res = bus.send("click", btn, timeout=_FIND_T) or {}
                if res.get("clicked"):
                    clicked_paths.append(btn)
                    clicked_this_round = True
                    time.sleep(settle)   # let the popup tear down / the next one animate in
                else:
                    _warn("close_popups", "dismiss click did not register: %s (%s)" % (btn, res))
        if not clicked_this_round:
            break
    return clicked_paths


# ---------------------------------------------------------------------------------------------
# CAMERA FOCUS -- deterministically center the campaign camera on any lord / hero / settlement.
# The move itself is the mod's `focus` handler (cm:set_camera_position lives only inside the mod);
# these are thin, typed wrappers + entity enumerators. A character/settlement resolves to its
# DISPLAY position inside the handler, so any entity the game knows (yours OR enemy) is focusable.
# ---------------------------------------------------------------------------------------------

def focus_char(bus, cqi):
    """Center the camera on a character (lord OR hero) by command_queue_index. Returns the mod's
    focus response {kind,x,y,moved,...}; `moved` is True when the camera was repositioned."""
    return bus.send("focus", "char %d" % int(cqi), timeout=_FIND_T) or {}


def focus_settlement(bus, region_key):
    """Center the camera on a settlement by its region key (yours or an enemy's)."""
    return bus.send("focus", "sett %s" % region_key, timeout=_FIND_T) or {}


def focus_xy(bus, x, y, d=8.0, b=0.0, h=6.0):
    """Center the camera on raw map coords (escape hatch / tuning)."""
    return bus.send("focus", "xy %s %s %s %s %s" % (x, y, d, b, h), timeout=_FIND_T) or {}


def list_lords_and_heroes(bus):
    """Every character of the player's faction: [{cqi, subtype, is_leader, has_army, kind}]. kind is
    'lord' if it commands an army (has_army), else 'hero'."""
    r = bus.send("chars", "", timeout=_FIND_T) or {}
    out = []
    for c in (r.get("chars") or []):
        out.append({"cqi": c.get("cqi"), "subtype": c.get("subtype"),
                    "is_leader": c.get("is_leader"),
                    "kind": "lord" if c.get("has_army") else "hero"})
    # dedup by cqi (chars can appear under multiple roles)
    seen, uniq = set(), []
    for c in out:
        if c["cqi"] not in seen:
            seen.add(c["cqi"]); uniq.append(c)
    return uniq


def list_settlements(bus, include_enemy=True):
    """Player settlements (from setts) + optionally visible enemy settlements (from hostiles), each
    as {region, x, y, owner}. Region key is what focus_settlement needs."""
    out = []
    s = bus.send("setts", "", timeout=_FIND_T) or {}
    for x in (s.get("setts") or []):
        if x.get("region"):
            out.append({"region": x.get("region"), "x": x.get("x"), "y": x.get("y"), "owner": "player"})
    if include_enemy:
        h = bus.send("hostiles", "", timeout=_FIND_T) or {}
        for x in (h.get("hostiles") or []):
            if x.get("kind") == "settlement" and x.get("region"):
                out.append({"region": x.get("region"), "x": x.get("x"), "y": x.get("y"),
                            "owner": x.get("faction")})
    seen, uniq = set(), []
    for x in out:
        if x["region"] not in seen:
            seen.add(x["region"]); uniq.append(x)
    return uniq


# =====================================================================================================
# COORDINATE MAPPING + HARDWARE CLICK -- the reusable primitive for driving ANY on-screen UI element.
# `find`/`tree` return a component's rect from UIComponent:Position(), in the engine's VIRTUAL UI canvas
# (1984x1116 -- verified in twapi/access.py and correlation.py `UI_VIRTUAL_W=1984`). At a 2560x1440
# client with origin (0,0) the map to screen pixels is a uniform x1.2903 (2560/1984 == 1440/1116).
# Some components -- notably settlement BUILDING SLOTS -- ignore SimulateLClick and need a REAL hardware
# click at that pixel (input.ps1); that is why hw_click exists alongside bus_click.
# CAVEAT: a FULLSCREEN panel that hides hud_campaign flips the frame to render-space (~3968x2232);
# for those, pass the open panel's own logical rect as `client` (it still reports 0,0,1984,1116).
# =====================================================================================================
import subprocess as _sp

UI_BASE_W, UI_BASE_H = 1984.0, 1116.0
CLIENT = (0, 0, 2560, 1440)                 # screen rect of the game client (borderless, primary monitor)
CENTER = (1280, 720)
_PS_INPUT = r"D:\tw_stack\launcher\ps\input.ps1"


def ui_to_screen(ux, uy, client=CLIENT):
    """Map a virtual-UI coordinate (from find/tree) to an absolute screen pixel."""
    ox, oy, cw, ch = client
    return int(round(ox + ux * cw / UI_BASE_W)), int(round(oy + uy * ch / UI_BASE_H))


def mouse(action, x=None, y=None, d=None):
    """Synthetic mouse/key via ps/input.ps1 (absolute screen px, focus-independent).
    action = move | click | rclick | dclick | drag | wheel | key."""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", _PS_INPUT, action]
    for v in (x, y, d):
        if v is not None:
            cmd.append(str(v))
    return (_sp.run(cmd, capture_output=True, text=True, timeout=30).stdout or "").strip()


def bus_click(bus, path):
    """SimulateLClick a component by pipe-path. Works for BUTTONS; a silent no-op on building slots."""
    r = bus.send("click", path, timeout=_FIND_T) or {}
    if not r.get("clicked"):
        _warn("bus_click", "click reply missing/not clicked: %s (%s)" % (path, r))
    return r


def find_rect(bus, root, match):
    """First node under `root` whose id==match OR whose path ends with `match`, as its describe() dict
    (x,y,w,h in UI coords + path). Uses `tree` (index-walk) because name-resolve fails on context-id
    paths. Prefers a visible, positioned node."""
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


def hw_click(bus, root, match, frac=(0.5, 0.5), settle=0.25):
    """Hardware-click (real mouse) the CENTER of a component located by find_rect, converting its
    UI-space rect to screen pixels. Use for elements SimulateLClick won't fire (e.g. building slots)."""
    n = find_rect(bus, root, match)
    if not n or n.get("x") is None:
        _warn("hw_click(%s, %s)" % (root, match), "find_rect returned nothing/position-less -> None")
        return None
    sx, sy = ui_to_screen(n["x"] + n["w"] * frac[0], n["y"] + n["h"] * frac[1])
    mouse("move", sx, sy); time.sleep(settle); mouse("click", sx, sy)
    return {"screen": [sx, sy], "path": n.get("path"), "id": n.get("id")}


def hw_hover(bus, root, match, frac=(0.5, 0.5), settle=0.9):
    """Move the cursor onto a component (NO click) -- for hover-triggered flyouts/tooltips."""
    n = find_rect(bus, root, match)
    if not n or n.get("x") is None:
        _warn("hw_hover(%s, %s)" % (root, match), "find_rect returned nothing/position-less -> None")
        return None
    sx, sy = ui_to_screen(n["x"] + n["w"] * frac[0], n["y"] + n["h"] * frac[1])
    mouse("move", sx, sy); time.sleep(settle)
    return {"screen": [sx, sy], "path": n.get("path")}


# =====================================================================================================
# VIEW / STATE MANAGEMENT -- know what panel is open, and get back to a clean campaign map.
# =====================================================================================================
# Roots ALWAYS present on the plain campaign map (never a "view"). Anything else visible is a panel.
BASE_ROOTS = frozenset((
    "ai_attack_targets_parent", "3d_ui_parent", "mission_indicator_parent", "hud_campaign",
    "menu_bar", "panel_manager", "under_advisor_docker", "tooltip_default", "saving_icon",
    "character_map_path_icons", "targeting_interface_dimming", "black_fade", "resources_bar",
    "help_panel", "qa_console", "campaign_space_bar_options",
))


def visible_roots(bus):
    r = bus.send("roots", "", timeout=_FIND_T) or {}
    if not r.get("kids"):
        _warn("visible_roots", "empty/None roots reply (bus failure would otherwise read as a clean screen)")
    return [k.get("id") for k in (r.get("kids") or []) if k.get("visible") and k.get("id")]


def open_views(bus):
    """Non-base panels/views currently open -- what `quit_views` must clear."""
    return [r for r in visible_roots(bus) if r not in BASE_ROOTS]


def is_clean(bus):
    """True when only the base campaign HUD is showing (no panel/view/popup open)."""
    return not open_views(bus)


def deselect(bus):
    """Deselect the current army/character by left-clicking an empty patch of map (top-centre, above
    every panel). Closes units_panel/character_details_panel that carry no close button. (The game
    ignores synthetic ESC scancodes, so a map click -- not ESC -- is the reliable deselect.)"""
    mouse("click", 1280, 210)
    time.sleep(0.5)


def quit_views(bus, max_rounds=6):
    """Return to a clean campaign map: drain popups, click any panel close/ok/cancel control, and
    deselect a selected army. Returns True once is_clean()."""
    for _ in range(max_rounds):
        close_popups(bus)
        views = open_views(bus)
        if not views:
            return True
        acted = False
        for root in views:
            tr = bus.send("tree", "%s 22 5000" % root, timeout=_TREE_T) or {}
            for n in (tr.get("nodes") or []):
                idd = str(n.get("id") or "").lower()
                if (n.get("visible") and str(n.get("state")) in _CLICKABLE_STATES
                        and any(w in idd for w in ("button_close", "button_ok", "button_cancel",
                                                   "button_dismiss", "button_tick"))):
                    bus.send("click", n.get("path"), timeout=_FIND_T)
                    acted = True; time.sleep(0.6); break
            if acted:
                break
        if not acted:
            deselect(bus)
        time.sleep(0.5)
    return is_clean(bus)


# =====================================================================================================
# CAMERA + VIEW OPENERS
# =====================================================================================================
def capital_region(bus):
    """Region key of the player's province capital (fallback: first owned settlement)."""
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


def recenter_capital(bus):
    """Focus the camera on the player's capital settlement. Returns the region key focused."""
    reg = capital_region(bus)
    if reg:
        focus_settlement(bus, reg)
    return reg


def focus_settlement_tight(bus, region, d=4.0):
    """Focus TIGHT on a settlement (mod's `sett` focus is a fixed d=8 -- too wide to click reliably).
    Resolves the settlement's display position via eval and centres it with a close zoom."""
    x = (bus.send("eval", "cm:get_region('%s'):settlement():display_position_x()" % region, timeout=_FIND_T) or {}).get("result")
    y = (bus.send("eval", "cm:get_region('%s'):settlement():display_position_y()" % region, timeout=_FIND_T) or {}).get("result")
    if x is None or y is None:
        return focus_settlement(bus, region)
    return bus.send("focus", "xy %s %s %s 0.0 6.0" % (x, y, d), timeout=_FIND_T)


def open_settlement_panel(bus, region, tries=3):
    """Open a settlement's management panel (root `settlement_panel`). Focuses the settlement TIGHT
    then left-clicks its centre; if an army gets selected instead, deselects and retries."""
    for _ in range(tries):
        focus_settlement_tight(bus, region)
        time.sleep(1.6)
        mouse("move", *CENTER); time.sleep(0.2); mouse("click", *CENTER)
        time.sleep(1.2)
        if "settlement_panel" in visible_roots(bus):
            return True
        deselect(bus)
    _warn("open_settlement_panel(%s)" % region, "settlement_panel never opened after %d tries" % tries)
    return "settlement_panel" in visible_roots(bus)


if __name__ == "__main__":
    # Self-test / live tool: clear whatever popups are on screen right now.
    sys.path.insert(0, r"D:\tw_stack\bus")
    from bus import Bus  # noqa
    _bus = Bus()
    before = _open_roots(_bus)
    print("open popup roots BEFORE:", before)
    closed = close_popups(_bus)
    print("clicked dismiss buttons:", closed)
    print("open popup roots AFTER :", _open_roots(_bus))
