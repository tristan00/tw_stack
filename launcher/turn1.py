r"""turn1.py -- turn-1 driver. `python turn1.py <step>` runs one STEPS entry, no arg runs them all."""
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")
from bus import Bus  # noqa: E402
import nav  # noqa: E402

MONOLITHS = "wh3_main_combi_region_the_monoliths"


def _warn(where, detail):
    sys.stderr.write("turn1: %s -> %s\n" % (where, detail))


def _ev(bus, expr):
    return (bus.send("eval", expr, timeout=6) or {}).get("result")


def treasury(bus):
    """Faction gold."""
    return _ev(bus, "cm:get_faction(cm:get_local_faction_name(true)):treasury()")


def clear_intro(bus, rounds=12):
    """Dismiss campaign-start popups; returns (ok, detail)."""
    for _ in range(rounds):
        views = set(nav.open_views(bus))
        if "events" in views:
            nav.hw_click(bus, "events", "button_accept")
            time.sleep(0.8)
            continue
        if "advice_interface" in views:
            n = nav.find_rect(bus, "advice_interface", "button_close")
            if n and n.get("x") is not None:
                nav.hw_click(bus, "advice_interface", "button_close")
            time.sleep(0.8)
            continue
        if views:
            nav.close_popups(bus)
            time.sleep(0.8)
            continue
        return True, "clean map"
    left = nav.open_views(bus)
    return (not left), ("cleared" if not left else "stuck: %s" % left)


def _hwclick_node(bus, n, settle=1.3):
    """Hardware-click the centre of an already-fetched tree node; returns the screen point."""
    sx, sy = nav.ui_to_screen(n["x"] + n["w"] / 2, n["y"] + n["h"] / 2)
    nav.mouse("move", sx, sy); time.sleep(0.2); nav.mouse("click", sx, sy); time.sleep(settle)
    return (sx, sy)


def _find_node(bus, root, pred, depth=30, nodes=9000):
    tr = bus.send("tree", "%s %d %d" % (root, depth, nodes), timeout=20) or {}
    if not tr.get("nodes"):
        _warn("_find_node(%s)" % root, "empty/None tree reply (bus timeout vs genuinely absent element)")
    for n in (tr.get("nodes") or []):
        if pred(n):
            return n
    return None


def build(bus, region=MONOLITHS):
    """Construct a building in `region`; returns (ok, detail). Asserts a treasury drop."""
    clear_intro(bus)
    t0 = treasury(bus)
    if not nav.open_settlement_panel(bus, region):
        return False, "settlement_panel did not open (roots=%s)" % nav.visible_roots(bus)
    short = region.split("region_")[-1]
    slot = _find_node(bus, "settlement_panel", lambda n: (
        n.get("id") == "button_expand_slot" and n.get("state") == "active"
        and short in str(n.get("path", "")) and n.get("visible") and n.get("x") is not None))
    if not slot:
        return False, "no buildable slot (button_expand_slot state=active) in %s" % short
    sx, sy = nav.ui_to_screen(slot["x"] + slot["w"] / 2, slot["y"] + slot["h"] / 2)

    def _cat():
        return _find_node(bus, "building_categories_popup", lambda n: (
            str(n.get("id", "")).startswith("CcoBuildingSetRecord") and n.get("visible") and n.get("x") is not None))

    nav.mouse("move", sx, sy); time.sleep(1.4)
    cat = _cat()
    if not cat:
        nav.mouse("click", sx, sy); time.sleep(1.4)
        cat = _cat()
    if not cat:
        return False, "slot hover+click opened no building-category flyout"
    cx, cy = nav.ui_to_screen(cat["x"] + cat["w"] / 2, cat["y"] + cat["h"] / 2)
    nav.mouse("move", cx, cy); time.sleep(1.4)
    card = _find_node(bus, "building_categories_popup", lambda n: (
        n.get("id") == "square_building_button" and n.get("state") == "normal"
        and n.get("visible") and n.get("x") is not None))
    if not card:
        return False, "category hover opened no buildable building card"
    bx, by = nav.ui_to_screen(card["x"] + card["w"] / 2, card["y"] + card["h"] / 2)
    nav.mouse("move", bx, by); time.sleep(0.3); nav.mouse("click", bx, by); time.sleep(1.4)
    t1 = treasury(bus)
    building = str(card["path"]).split("|")[-2]
    if t0 is not None and t1 is not None and t1 < t0:
        return True, "built %s: treasury %s -> %s" % (building, t0, t1)
    return False, "no treasury drop (%s -> %s); construction not confirmed" % (t0, t1)


def _select_army(bus, cqi):
    """Select a lord's army; True once units_panel is open."""
    x = _ev(bus, "cm:get_character_by_cqi(%d):display_position_x()" % cqi)
    y = _ev(bus, "cm:get_character_by_cqi(%d):display_position_y()" % cqi)
    if x is None or y is None:
        return False
    for click_y in (720, 640, 480, 300, 200):       # candidate banner heights
        for _ in range(3):
            if not nav.open_views(bus):
                break
            nav.deselect(bus); time.sleep(0.4)
        bus.send("focus", "xy %s %s 4.0 0.0 6.0" % (x, y), timeout=6)
        time.sleep(1.4)
        nav.mouse("move", 1280, click_y); time.sleep(0.2); nav.mouse("click", 1280, click_y)
        time.sleep(0.9)
        if "units_panel" in nav.visible_roots(bus):
            return True
    return "units_panel" in nav.visible_roots(bus)


_RECRUIT_BTN = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
                "button_subpanel|button_group_army|button_recruitment")


def recruit(bus, lord_cqi=56):
    """Recruit a unit into the lord's army; returns (ok, detail). Asserts a treasury drop."""
    clear_intro(bus)
    t0 = treasury(bus)
    if not _select_army(bus, lord_cqi):
        return False, "could not select lord %d's army (units_panel not open)" % lord_cqi
    if "units_panel" not in nav.visible_roots(bus):
        return False, "units_panel not open -- refusing to toggle recruitment (crash guard)"
    nav.bus_click(bus, _RECRUIT_BTN)
    time.sleep(1.3)
    card = _find_node(bus, "units_panel", lambda n: (
        str(n.get("id", "")).endswith("_recruitable") and n.get("visible")
        and str(n.get("state")) in nav._CLICKABLE_STATES))
    if not card:
        return False, "recruitment toggled but no recruitable unit card in units_panel"
    nav.bus_click(bus, card["path"])
    time.sleep(1.3)
    t1 = treasury(bus)
    unit = str(card["id"]).replace("_recruitable", "")
    if t0 is not None and t1 is not None and t1 < t0:
        return True, "recruited %s: treasury %s -> %s" % (unit, t0, t1)
    return False, "no treasury drop (%s -> %s); recruitment not confirmed" % (t0, t1)


_TECH_BTN = "hud_campaign|faction_buttons_docker|button_group_management|button_technology"
_TECH_OK = "technology_panel|button_ok_holder|button_ok"


def _is_researching(bus):
    return _ev(bus, "local f=cm:get_local_faction(true); if f then return f:is_currently_researching() end") is True


def research(bus):
    """Start a technology; returns (ok, detail). Asserts is_currently_researching."""
    clear_intro(bus)
    was = _is_researching(bus)
    nav.bus_click(bus, _TECH_BTN)
    time.sleep(1.8)
    if "technology_panel" not in nav.visible_roots(bus):
        return False, "technology_panel did not open"
    entry = _find_node(bus, "technology_panel", lambda n: (
        n.get("id") == "technology_entry" and str(n.get("state")) == "available" and n.get("visible")))
    if not entry:
        return False, "no 'available' technology_entry (needs a tab click, or all locked)"
    nav.bus_click(bus, entry["path"])
    time.sleep(1.2)
    nav.bus_click(bus, _TECH_OK)
    time.sleep(1.0)
    now = _is_researching(bus)
    return (bool(now), "research started (is_currently_researching %s -> %s)" % (was, now))


_DIPLO_BTN = "hud_campaign|faction_buttons_docker|button_group_management|button_diplomacy"
_DIPLO_ROOT = "diplomacy_dropdown"
_DIPLO_CLIP = "diplomacy_dropdown|faction_panel|faction_panel_top|sortable_list_factions|list_clip"
_DIPLO_LIST = _DIPLO_CLIP + "|list_box"
_DIPLO_DECLARE_OPT = ("diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|"
                      "offers_list_panel|list_possible_actions|diplomatic_option_declare_war")
_WAR_DECL_OK = ("diplomacy_dropdown|subpanel_group|war_declared|diplomacy_hud_war_declared|"
                "both_buttongroup|button_ok_declare")
_DIPLO_CANCEL = "diplomacy_dropdown|faction_panel|faction_panel_bottom|both_buttongroup|button_cancel"


def _find(bus, path):
    return bus.send("find", path, timeout=6) or {}


def _at_war(bus, fac):
    return _ev(bus, "local me=cm:get_local_faction(true); local o=cm:get_faction('%s'); "
                    "if me and o and not o:is_null_interface() then return me:at_war_with(o) end" % fac)


def _diplo_rect(bus):
    d = _find(bus, _DIPLO_ROOT)
    if d.get("w") and float(d["w"]) <= 2500:
        return (float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]))
    return (0.0, 0.0, 1984.0, 1116.0)


def _diplo_to_screen(bus, ux, uy):
    px, py, pw, ph = _diplo_rect(bus)
    return int(round((ux - px) * 2560.0 / pw)), int(round((uy - py) * 1440.0 / ph))


def _close_diplo(bus):
    for _ in range(2):
        if _DIPLO_ROOT not in nav.visible_roots(bus):
            return
        nav.bus_click(bus, _DIPLO_CANCEL); time.sleep(0.9)


def _diplo_click_id(bus, cid, require_active=True):
    """Click a diplomacy component by id. Returns its state, or None if not found."""
    tr = bus.send("tree", "diplomacy_dropdown 34 15000", timeout=25) or {}
    n = next((x for x in (tr.get("nodes") or [])
              if x.get("id") == cid and x.get("visible") and x.get("x") is not None), None)
    if not n:
        return None
    if require_active and n.get("state") == "inactive":
        return "inactive"
    nav.mouse("click", *nav.ui_to_screen(n["x"] + n["w"] / 2, n["y"] + n["h"] / 2))
    return n.get("state")


def diplomacy(bus):
    """Send a gift proposal through the diplomacy UI; returns (ok, detail). Asserts a treasury drop."""
    clear_intro(bus)
    nav.bus_click(bus, _DIPLO_BTN)
    time.sleep(1.8)
    if _DIPLO_ROOT not in nav.visible_roots(bus):
        return False, "diplomacy_dropdown did not open"
    tr = bus.send("tree", _DIPLO_LIST + " 3 500", timeout=12) or {}
    rows = [(str(n["id"])[len("faction_row_entry_"):], n) for n in (tr.get("nodes") or [])
            if str(n.get("id", "")).startswith("faction_row_entry_") and n.get("x") is not None]
    if not rows:
        _close_diplo(bus)
        return False, "no faction rows in diplomacy list"
    for fac, r in rows[:5]:
        nav.mouse("dclick", *nav.ui_to_screen(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2))
        time.sleep(1.6)
        t0 = treasury(bus)
        if _diplo_click_id(bus, "diplomatic_option_payment") != "active":
            continue
        time.sleep(1.2)
        _diplo_click_id(bus, "pay_offer"); time.sleep(0.5)
        for _ in range(4):
            _diplo_click_id(bus, "r_arrow_amount", require_active=False); time.sleep(0.3)
        _diplo_click_id(bus, "ok_payments"); time.sleep(1.0)
        _diplo_click_id(bus, "button_send")
        time.sleep(1.5)
        t1 = treasury(bus)
        if t0 is not None and t1 is not None and t1 < t0:
            _close_diplo(bus)
            return True, "sent gift proposal to %s: treasury %s -> %s" % (fac, t0, t1)
    _close_diplo(bus)
    return False, "could not send a proposal (button_send stayed inactive)"


_BTN_GENERAL = "hud_campaign|info_panel_holder|primary_info_panel_holder|info_button_list|button_general"
_CDP = "character_details_panel"
_CD_CTX = "character_details_panel|character_context_parent"
_CD_OK = _CD_CTX + "|button_bottom_holder|button_ok"
_CD_TAB_SKILLS = _CD_CTX + "|TabGroup|skills"
_CD_TAB_DETAILS = _CD_CTX + "|TabGroup|details"


def _open_details(bus, cqi):
    if not _select_army(bus, cqi):
        return False
    nav.bus_click(bus, _BTN_GENERAL)
    time.sleep(1.3)
    return _CDP in nav.visible_roots(bus)


def _has_skill(bus, cqi, key):
    return _ev(bus, "local c=cm:get_character_by_cqi(%d); if c and not c:is_null_interface() "
                    "then return c:has_skill('%s') end" % (cqi, key))


def items(bus, lord_cqi=56):
    """Equip a pooled item onto the lord; returns (ok, detail). Asserts the pool shrinks."""
    clear_intro(bus)
    if not _open_details(bus, lord_cqi):
        return False, "character_details_panel did not open"
    nav.bus_click(bus, _CD_TAB_DETAILS)
    time.sleep(1.0)

    def pool():
        tr = bus.send("tree", _CDP + " 32 14000", timeout=25) or {}
        return [n for n in (tr.get("nodes") or [])
                if n.get("id") == "ancillary_entry" and n.get("visible") and n.get("x") is not None]

    before = pool()
    active = [n for n in before if n.get("state") == "active"]
    if not active:
        nav.bus_click(bus, _CD_OK)
        return False, "no equippable pooled item (empty pool / no free matching slot)"
    e = active[0]
    nav.mouse("click", *nav.ui_to_screen(e["x"] + e["w"] / 2, e["y"] + e["h"] / 2))
    time.sleep(1.3)
    after = pool()
    nav.bus_click(bus, _CD_OK)
    return (len(after) < len(before), "equipped an item (pool %d -> %d)" % (len(before), len(after)))


def skills(bus, lord_cqi=56):
    """Spend a skill point on the lord; returns (ok, detail). Asserts has_skill flips True."""
    clear_intro(bus)
    if not _open_details(bus, lord_cqi):
        return False, "character_details_panel did not open"
    nav.bus_click(bus, _CD_TAB_SKILLS)
    time.sleep(1.0)
    tr = bus.send("tree", _CDP + " 30 12000", timeout=25) or {}
    nodes = [n for n in (tr.get("nodes") or [])
             if isinstance(n.get("id"), str) and n["id"].startswith("wh") and "_skill_" in n["id"] and n.get("visible")]
    tried = []
    for n in nodes:
        key = n["id"]
        if _has_skill(bus, lord_cqi, key):
            continue
        nav.bus_click(bus, n["path"] + "|card")
        time.sleep(1.1)
        if _has_skill(bus, lord_cqi, key):
            nav.bus_click(bus, _CD_OK)
            return True, "learned %s (has_skill False->True)" % key
        tried.append(key)
    nav.bus_click(bus, _CD_OK)
    return False, "no skill learnable (points available? tried=%s)" % tried[:6]


_BR = "popup_battle_results|mid|battle_results|post_battle_results_panel"


def _nearest_enemy_army(bus):
    ho = bus.send("hostiles", "", timeout=8) or {}
    arms = [h for h in (ho.get("hostiles") or []) if h.get("kind") == "army" and h.get("cqi")]
    arms.sort(key=lambda h: (h.get("dist") if h.get("dist") is not None else 9999))
    return arms[0] if arms else None


def _rclick_center_on(bus, dx, dy):
    """Focus a map display-position at screen centre, then right-click it."""
    bus.send("focus", "xy %s %s 4.0 0.0 6.0" % (dx, dy), timeout=6)
    time.sleep(1.6)
    nav.mouse("move", *nav.CENTER); time.sleep(0.2); nav.mouse("rclick", *nav.CENTER)
    time.sleep(1.8)


def _resolve_and_settle(bus):
    """Autoresolve the open pre-battle, take the post-battle decision, end on a clean map."""
    for _ in range(5):
        roots = nav.visible_roots(bus)
        if "popup_pre_battle" not in roots or "popup_battle_results" in roots:
            break
        bus.send("autoresolve", "", timeout=10)
        time.sleep(3.0)
    if "popup_battle_results" in nav.visible_roots(bus):
        chk = _find_node(bus, "popup_battle_results", lambda n: (
            n.get("id") == "button_accept" and "settlement_captured" in str(n.get("path", "")) and n.get("visible")))
        if chk:
            nav.bus_click(bus, chk["path"]); time.sleep(2.5)
            occ = _find_node(bus, "settlement_captured", lambda n: (
                n.get("id") == "dy_option" and str(n.get("text")).strip().lower() == "occupy"))
            if occ:
                nav.bus_click(bus, str(occ["path"]).rsplit("|dy_option", 1)[0]); time.sleep(2.5)
        else:
            nav.bus_click(bus, "%s|button_set_win_holder|button_set_win|button_captive_option_release" % _BR)
            time.sleep(1.5)
    nav.close_popups(bus); time.sleep(0.8)


def attack_character(bus, target_cqi, lord_cqi=56):
    """Attack an enemy character by cqi; returns (ok, detail). Asserts the target no longer exists."""
    clear_intro(bus)
    if target_cqi is None:
        return False, "no target character to attack"
    if not _select_army(bus, lord_cqi):
        return False, "could not select lord %d's army (units_panel not open)" % lord_cqi
    ex = _ev(bus, "cm:get_character_by_cqi(%d):display_position_x()" % target_cqi)
    ey = _ev(bus, "cm:get_character_by_cqi(%d):display_position_y()" % target_cqi)
    if ex is None or ey is None:
        return False, "target cqi %d has no display position" % target_cqi
    _rclick_center_on(bus, ex, ey)
    pb = False
    for _ in range(8):
        if "popup_pre_battle" in nav.visible_roots(bus):
            pb = True
            break
        time.sleep(0.7)
    if not pb:
        return False, "attack order did not open a pre-battle (out of range? roots=%s)" % nav.visible_roots(bus)
    _resolve_and_settle(bus)
    dead = _ev(bus, "not cm:get_character_by_cqi(%d)" % target_cqi)
    return (bool(dead), "defeated character cqi %d" % target_cqi if dead else "character cqi %d still alive after battle" % target_cqi)


def attack_settlement(bus, region, lord_cqi=56):
    """Attack and capture a settlement by region key; returns (ok, detail). Asserts we own it."""
    clear_intro(bus)
    if not _select_army(bus, lord_cqi):
        return False, "could not select lord %d's army (units_panel not open)" % lord_cqi
    dx = _ev(bus, "cm:get_region('%s'):settlement():display_position_x()" % region)
    dy = _ev(bus, "cm:get_region('%s'):settlement():display_position_y()" % region)
    if dx is None or dy is None:
        return False, "settlement region has no display position (%s)" % region
    _rclick_center_on(bus, dx, dy)
    pb = False
    for _ in range(8):
        if "popup_pre_battle" in nav.visible_roots(bus):
            pb = True
            break
        time.sleep(0.7)
    if not pb:
        return False, "attack order did not open a pre-battle (out of range this turn? roots=%s)" % nav.visible_roots(bus)
    _resolve_and_settle(bus)
    short = region.split("region_")[-1]
    mine = [s.get("region") for s in (bus.send("setts", "", timeout=6) or {}).get("setts") or []]
    owned = any(short in (m or "") for m in mine)
    return (owned, "own %s" % short if owned else "%s not owned (setts=%s)" % (short, mine))


def _todo(bus):
    return False, "step not implemented yet (implement strictly in task order)"


def _step_attack_xyion(bus):
    e = _nearest_enemy_army(bus)
    return attack_character(bus, e["cqi"]) if e else (False, "no enemy army found to attack")


def _step_build(bus):
    """Build in every owned settlement with a buildable slot; ok if at least one was queued."""
    clear_intro(bus)
    regs = [s.get("region") for s in (bus.send("setts", "", timeout=6) or {}).get("setts") or [] if s.get("region")]
    out, any_ok = [], False
    for reg in regs:
        ok, msg = build(bus, reg)
        any_ok = any_ok or ok
        out.append("%s:%s" % (reg.split("region_")[-1], "OK" if ok else msg))
    return any_ok, " | ".join(out)


STEPS = {          # order matters: run as listed
    "attack_xyion": _step_attack_xyion,
    "attack_shrine": lambda bus: attack_settlement(bus, "wh3_main_combi_region_shrine_of_ladrielle"),
    "build": _step_build,
    "recruit": recruit,
    "research": research,
    "diplomacy": diplomacy,
    "items": items,
    "skills": skills,
}


def main():
    bus = Bus()
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    names = list(STEPS) if step == "all" else [step]
    for name in names:
        print("== %s ==" % name)
        print("  views before:", nav.open_views(bus))
        res = STEPS[name](bus)
        ok, msg = res if isinstance(res, tuple) else (res, "")
        print("  views after :", nav.open_views(bus))
        print("  detail: %s" % msg)
        print("  RESULT: %s -> %s" % (name, "PASS" if ok else "FAIL"))
        if not ok:
            break


if __name__ == "__main__":
    main()
