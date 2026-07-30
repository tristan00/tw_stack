r"""turn1.py -- hands-off turn-1 driver. Each step is a function that MUST work on its own via the
nav primitives (no live LLM decisions). Run `python turn1.py <step>` to exercise one step, or with no
arg to run the whole sequence. Built up one verified step at a time: clear_intro -> build -> recruit
-> research -> diplomacy -> skills. Every step reports PASS/FAIL from OBJECTIVE bus state.
"""
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")
from bus import Bus  # noqa: E402
import nav  # noqa: E402

MONOLITHS = "wh3_main_combi_region_the_monoliths"


def _warn(where, detail):
    """Log (never swallow silently) a soft failure -- element/query not found or errored."""
    sys.stderr.write("turn1: %s -> %s\n" % (where, detail))


# ---------------------------------------------------------------- game-data readers (for asserts)
def _ev(bus, expr):
    return (bus.send("eval", expr, timeout=6) or {}).get("result")


def treasury(bus):
    """Faction gold -- construction/recruitment deduct from it immediately, so a drop within a step
    proves the order was accepted by the GAME (not just that a button reported clicked)."""
    return _ev(bus, "cm:get_faction(cm:get_local_faction_name(true)):treasury()")


# ---------------------------------------------------------------- step 1: clear intro popups
def clear_intro(bus, rounds=12):
    """Dismiss the campaign-start popups (Marked-for-Death `events` + advisor `advice_interface`)
    to reach a clean, interactable map. The events checkmark ignores SimulateLClick -> HARDWARE click.
    Returns True when no non-base view remains."""
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


# ---------------------------------------------------------------- step 2: construct a building
def _hwclick_node(bus, n, settle=1.3):
    """Hardware-click the centre of an already-fetched tree node (UI coords -> screen)."""
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
    """Construct a building in `region` (a settlement we OWN). ROBUST hover-cascade, verified live by
    game data: open settlement_panel -> HOVER the empty buildable slot -> a category flyout opens (as a
    CHILD of settlement_panel) -> HOVER a category -> its buildings flyout opens -> click a building
    card. All positions come from the bus tree (no screenshot). ASSERT: treasury drops."""
    clear_intro(bus)
    t0 = treasury(bus)
    if not nav.open_settlement_panel(bus, region):
        return False, "settlement_panel did not open (roots=%s)" % nav.visible_roots(bus)
    short = region.split("region_")[-1]
    # ROBUST empty-slot signal: a `button_expand_slot` whose state is 'active' (locked slots read
    # 'locked'; built slots have a square_building_button state 'built_panel'). The CcoCampaignBuildingSlot's
    # OWN state reads a misleading 'building', which is why filtering on state=='empty' missed it.
    slot = _find_node(bus, "settlement_panel", lambda n: (
        n.get("id") == "button_expand_slot" and n.get("state") == "active"
        and short in str(n.get("path", "")) and n.get("visible") and n.get("x") is not None))
    if not slot:
        return False, "no buildable slot (button_expand_slot state=active) in %s" % short
    # Open the category flyout. An 'expand' slot (button_expand_slot) opens it on HOVER; a plain
    # empty slot opens it on CLICK. Try hover, then click.
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
    # HOVER a category -> its buildings flyout appears with buildable cards.
    cx, cy = nav.ui_to_screen(cat["x"] + cat["w"] / 2, cat["y"] + cat["h"] / 2)
    nav.mouse("move", cx, cy); time.sleep(1.4)
    card = _find_node(bus, "building_categories_popup", lambda n: (
        n.get("id") == "square_building_button" and n.get("state") == "normal"
        and n.get("visible") and n.get("x") is not None))
    if not card:
        return False, "category hover opened no buildable building card"
    bx, by = nav.ui_to_screen(card["x"] + card["w"] / 2, card["y"] + card["h"] / 2)
    nav.mouse("move", bx, by); time.sleep(0.3); nav.mouse("click", bx, by); time.sleep(1.4)
    # ASSERT FROM GAME DATA: queuing construction deducts its cost from the treasury.
    t1 = treasury(bus)
    building = str(card["path"]).split("|")[-2]
    if t0 is not None and t1 is not None and t1 < t0:
        return True, "built %s: treasury %s -> %s" % (building, t0, t1)
    return False, "no treasury drop (%s -> %s); construction not confirmed" % (t0, t1)


# ---------------------------------------------------------------- step 2: recruit a unit (FIRST action)
def _select_army(bus, cqi):
    """Select a lord's army on the map -> units_panel opens. Clicking the map CENTRE hits the army
    when the lord is on open ground, but when he's stationed ON a settlement the settlement fills the
    centre and his army BANNER floats high above it -- so we try several click heights (banner first
    for the on-settlement case) and stop as soon as units_panel opens, closing any settlement_panel a
    wrong click opened."""
    x = _ev(bus, "cm:get_character_by_cqi(%d):display_position_x()" % cqi)
    y = _ev(bus, "cm:get_character_by_cqi(%d):display_position_y()" % cqi)
    if x is None or y is None:
        return False
    for click_y in (720, 640, 480, 300, 200):
        for _ in range(3):                          # clean panels/selection first
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


# The recruitment button toggles a DOCKED sub-panel of units_panel. CTD GUARD (from twapi/verbs/
# recruit.py): clicking it with NO army selected can crash the engine (0xc0000409) -- only ever click
# it while units_panel is confirmed open.
_RECRUIT_BTN = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
                "button_subpanel|button_group_army|button_recruitment")


def recruit(bus, lord_cqi=56):
    """Recruit a unit into the lord's army; ASSERT from game data (treasury drops when a unit is
    queued). Flow (from the working recruit.py): select army -> toggle button_recruitment open (a
    docked child of units_panel) -> SimulateLClick a `<unit_key>_recruitable` card."""
    clear_intro(bus)
    t0 = treasury(bus)
    if not _select_army(bus, lord_cqi):
        return False, "could not select lord %d's army (units_panel not open)" % lord_cqi
    if "units_panel" not in nav.visible_roots(bus):   # CTD GUARD
        return False, "units_panel not open -- refusing to toggle recruitment (crash guard)"
    nav.bus_click(bus, _RECRUIT_BTN)                  # toggle recruitment_options open
    time.sleep(1.3)
    card = _find_node(bus, "units_panel", lambda n: (
        str(n.get("id", "")).endswith("_recruitable") and n.get("visible")
        and str(n.get("state")) in nav._CLICKABLE_STATES))
    if not card:
        return False, "recruitment toggled but no recruitable unit card in units_panel"
    nav.bus_click(bus, card["path"])                  # cards are a plain SimulateLClick -> queues unit
    time.sleep(1.3)
    t1 = treasury(bus)
    unit = str(card["id"]).replace("_recruitable", "")
    if t0 is not None and t1 is not None and t1 < t0:
        return True, "recruited %s: treasury %s -> %s" % (unit, t0, t1)
    return False, "no treasury drop (%s -> %s); recruitment not confirmed" % (t0, t1)


# ---------------------------------------------------------------- step 5: research
_TECH_BTN = "hud_campaign|faction_buttons_docker|button_group_management|button_technology"
_TECH_OK = "technology_panel|button_ok_holder|button_ok"


def _is_researching(bus):
    return _ev(bus, "local f=cm:get_local_faction(true); if f then return f:is_currently_researching() end") is True


def research(bus):
    """Start a technology; ASSERT from game data (is_currently_researching flips True). Flow (from
    twapi/verbs/research.py): open technology_panel via button_technology -> SimulateLClick an
    'available' technology_entry (sets it as current research) -> button_ok to close."""
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
    nav.bus_click(bus, _TECH_OK)   # confirm/close; research was committed by the node click
    time.sleep(1.0)
    now = _is_researching(bus)
    return (bool(now), "research started (is_currently_researching %s -> %s)" % (was, now))


# ---------------------------------------------------------------- step 6: diplomacy (declare war)
# Diplomacy is a FULLSCREEN panel: it hides hud_campaign, so component coords are mapped via the
# diplomacy_dropdown panel's OWN rect (not the ×1.2903 hud frame). The faction row needs a HARDWARE
# double-click (SimulateLClick won't open negotiation). Reliably assertable: at_war_with flips.
# (Trade/NAP proposals aren't reliable -- button_send stays inactive for any deal the AI would refuse.)
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
    """Click a diplomacy component by its id (positions via tree -> x1.2903 map; find can't resolve
    the deep diplomacy paths). Returns the node state, or None if not found. Skips the click when
    require_active and the node is inactive."""
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
    """Send a diplomatic PROPOSAL through the real diplomacy UI, ASSERTED by game data (treasury
    drops). Turn-1 economics: every BARE offer (trade/NAP/alliance) is REFUSED -- button_send stays
    inactive -- so this sends the one proposal the AI always accepts: a gift (payment to them).
    Flow: open diplomacy -> HARDWARE double-click a faction -> diplomatic_option_payment -> pay_offer
    radio -> raise the amount -> ok_payments -> button_send."""
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
        nav.mouse("dclick", *nav.ui_to_screen(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2))  # open negotiation
        time.sleep(1.6)
        t0 = treasury(bus)
        if _diplo_click_id(bus, "diplomatic_option_payment") != "active":
            continue
        time.sleep(1.2)
        _diplo_click_id(bus, "pay_offer"); time.sleep(0.5)          # pay THEM (a gift)
        for _ in range(4):
            _diplo_click_id(bus, "r_arrow_amount", require_active=False); time.sleep(0.3)  # raise amount
        _diplo_click_id(bus, "ok_payments"); time.sleep(1.0)
        _diplo_click_id(bus, "button_send")                          # send (active for a gift)
        time.sleep(1.5)
        t1 = treasury(bus)
        if t0 is not None and t1 is not None and t1 < t0:
            _close_diplo(bus)
            return True, "sent gift proposal to %s: treasury %s -> %s" % (fac, t0, t1)
    _close_diplo(bus)
    return False, "could not send a proposal (button_send stayed inactive)"


# ---------------------------------------------------------------- steps 7-8: items + skills
# The character-details panel (opened via button_general while the lord is selected) hosts both the
# skills tree and the equipment/global-pool. Skill card + ancillary_entry are bus SimulateLClicks.
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
    """Equip a pooled item onto the lord (Details tab -> Global Pool -> click an ACTIVE ancillary_entry).
    ASSERT from game data: the pool loses that equippable item (it moved onto the character). The pool
    fills from battles' 'Equipment Gained'."""
    clear_intro(bus)
    if not _open_details(bus, lord_cqi):
        return False, "character_details_panel did not open"
    nav.bus_click(bus, _CD_TAB_DETAILS)
    time.sleep(1.0)

    def pool():   # ALL pooled items (an equipped item LEAVES the pool -> total shrinks)
        tr = bus.send("tree", _CDP + " 32 14000", timeout=25) or {}
        return [n for n in (tr.get("nodes") or [])
                if n.get("id") == "ancillary_entry" and n.get("visible") and n.get("x") is not None]

    before = pool()
    active = [n for n in before if n.get("state") == "active"]
    if not active:
        nav.bus_click(bus, _CD_OK)
        return False, "no equippable pooled item (empty pool / no free matching slot)"
    e = active[0]
    # ancillary_entry ignores SimulateLClick (like building slots) -> HARDWARE click at its centre.
    nav.mouse("click", *nav.ui_to_screen(e["x"] + e["w"] / 2, e["y"] + e["h"] / 2))
    time.sleep(1.3)
    after = pool()
    nav.bus_click(bus, _CD_OK)
    return (len(after) < len(before), "equipped an item (pool %d -> %d)" % (len(before), len(after)))


def skills(bus, lord_cqi=56):
    """Spend a skill point on the lord (Skills tab -> click a learnable node's card). ASSERT from game
    data: has_skill(key) flips False->True."""
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


# ---------------------------------------------------------------- battle helpers (steps 1-2)
_BR = "popup_battle_results|mid|battle_results|post_battle_results_panel"


def _nearest_enemy_army(bus):
    ho = bus.send("hostiles", "", timeout=8) or {}
    arms = [h for h in (ho.get("hostiles") or []) if h.get("kind") == "army" and h.get("cqi")]
    arms.sort(key=lambda h: (h.get("dist") if h.get("dist") is not None else 9999))
    return arms[0] if arms else None


def _rclick_center_on(bus, dx, dy):
    """Focus a map display-position at screen centre, then RIGHT-CLICK it (issue a move/attack order)."""
    bus.send("focus", "xy %s %s 4.0 0.0 6.0" % (dx, dy), timeout=6)
    time.sleep(1.6)
    nav.mouse("move", *nav.CENTER); time.sleep(0.2); nav.mouse("rclick", *nav.CENTER)
    time.sleep(1.8)


def _resolve_and_settle(bus):
    """Autoresolve the open pre-battle, then take the post-battle decision from GAME UI: a captured
    settlement -> checkmark then Occupy; a field win -> release captives. Ends on a clean map."""
    # The pre-battle UI can lag; retry autoresolve until it actually closes (or results appear).
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
    """GENERAL: attack ANY enemy character (by command_queue_index) with a lord's army -- select the
    lord, right-click the target to give battle, autoresolve, take the post-battle decision. Not
    specific to any one target. ASSERT from game data that the target character no longer exists."""
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
    dead = _ev(bus, "not cm:get_character_by_cqi(%d)" % target_cqi)   # nil/false once defeated
    return (bool(dead), "defeated character cqi %d" % target_cqi if dead else "character cqi %d still alive after battle" % target_cqi)


def attack_settlement(bus, region, lord_cqi=56):
    """GENERAL: attack + capture ANY settlement (by region key) with a lord's army. Not specific to
    one target. ASSERT from game data that we now OWN that region."""
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


# THE TASK -- exact order, do not reorder / skip / substitute. Each entry applies a GENERAL primitive
# (attack_character / attack_settlement / build / recruit / ...) to this task's specific target; the
# primitives are reusable for any character/settlement. (clear_intro is internal setup, not an action.)
def _step_attack_xyion(bus):
    e = _nearest_enemy_army(bus)              # the Marked-for-Death target = nearest enemy army
    return attack_character(bus, e["cqi"]) if e else (False, "no enemy army found to attack")


def _step_build(bus):
    """Build in EVERY owned settlement that has a buildable slot (both missing buildings), via the one
    generalized build() helper. PASS if at least one construction was queued (treasury dropped)."""
    clear_intro(bus)
    regs = [s.get("region") for s in (bus.send("setts", "", timeout=6) or {}).get("setts") or [] if s.get("region")]
    out, any_ok = [], False
    for reg in regs:
        ok, msg = build(bus, reg)
        any_ok = any_ok or ok
        out.append("%s:%s" % (reg.split("region_")[-1], "OK" if ok else msg))
    return any_ok, " | ".join(out)


STEPS = {
    "attack_xyion": _step_attack_xyion,                                                        # 1
    "attack_shrine": lambda bus: attack_settlement(bus, "wh3_main_combi_region_shrine_of_ladrielle"),  # 2
    "build": _step_build,  # 3 (every owned settlement's missing building)
    "recruit": recruit,    # 4
    "research": research,  # 5
    "diplomacy": diplomacy,  # 6
    "items": items,        # 7
    "skills": skills,      # 8
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
