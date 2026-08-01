r"""click_actions.py -- executors driven through UI components; they register into cco_actions.REGISTRY."""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

from cco_actions import _G, _ev, register       # noqa: E402

EDICT_STACK = "hud_campaign|BL_parent|stack_incentives"
RECRUIT_BTN = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
               "button_subpanel|button_group_army|button_recruitment")
CREATE_ARMY_BTN = ("hud_campaign|hud_center_docker|hud_center|small_bar|button_subpanel_parent|"
                   "button_subpanel|button_group_settlement|button_create_army")
CHAR_PANEL = "character_panel|character_panel_info_holder"
LORD_TYPE_LIST = CHAR_PANEL + "|lords_and_agents_holder|lord_parent|list_box"
AGENT_TYPE_LIST = CHAR_PANEL + "|lords_and_agents_holder|agent_parent|list_box"
BUTTON_CONFIRM = CHAR_PANEL + "|footer|button_confirm"
CANDIDATE_LIST = (CHAR_PANEL + "|general_selection_panel|main_holder|character_list_parent|"
                  "character_list|listview|list_clip|list_box")
BUTTON_RAISE = CHAR_PANEL + "|footer|button_raise"
LORE_LIST = (CHAR_PANEL + "|general_selection_panel|main_holder|lore_select_parent|"
             "lord_magic_lore_type|lore_type_list")


def _click(bus, path, timeout=10.0):
    """Returns True only if the component was found and clicked."""
    try:
        r = bus.send("click", path, timeout=timeout) or {}
    except Exception as e:
        sys.stderr.write("click_actions: click %s -> %s\n" % (path.rsplit("|", 1)[-1], repr(e)[:70]))
        return False
    if not r.get("clicked"):
        sys.stderr.write("click_actions: click NOT registered: %s (found=%s)\n"
                         % (path.rsplit("|", 1)[-1], r.get("found")))
    return bool(r.get("clicked"))


def _find(bus, path, timeout=8.0):
    try:
        r = bus.send("find", path, timeout=timeout) or {}
        return (r.get("result") or {}), (r.get("child_ids") or [])
    except Exception:
        return {}, []


def engine_click(bus, component_id):
    """Click a component by id, resolved recursively from RootComponent."""
    lua = ('common.call_context_command([[RootComponent.ChildContext("%s").SimulateLClick]]) '
           'return "sent"' % component_id)
    try:
        return _ev(bus, lua, timeout=20.0) == "sent"
    except Exception as e:
        sys.stderr.write("click_actions: engine_click %s -> %s" % (component_id, repr(e)[:80]) + chr(10))
        return False


def _until(pred, cap, step=0.2):
    """True as soon as `pred` holds; `cap` is a ceiling, not a sleep."""
    deadline = time.time() + cap
    while time.time() < deadline:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def clear_screen(bus):
    """Close open panels, then leftover popups; returns the number of popups closed."""
    import nav
    try:
        _ev(bus, 'common.call_context_command([[CloseAllPanels]]) return "sent"', timeout=15.0)
        _until(lambda: not [r for r in (nav.visible_roots(bus) or [])
                            if r not in nav.BASE_ROOTS], 1.0)
    except Exception as e:
        sys.stderr.write("click_actions: CloseAllPanels -> %s" % repr(e)[:80] + chr(10))
    n = 0
    try:
        n = len(nav.close_popups(bus))
    except Exception as e:
        sys.stderr.write("click_actions: close_popups -> %s" % repr(e)[:80] + chr(10))
    try:
        if any(r not in nav.BASE_ROOTS for r in (nav.visible_roots(bus) or [])):
            nav.deselect(bus)
            _until(lambda: not [r for r in (nav.visible_roots(bus) or [])
                                if r not in nav.BASE_ROOTS], 1.2)
    except Exception as e:
        sys.stderr.write("click_actions: deselect -> %s" % repr(e)[:80] + chr(10))
    return n


def select_settlement(bus, region):
    r = _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s') if not s then return 'NO-SETT' end "
                      "local ok,e=pcall(function() s:Call('Select') end) return 'ok='..tostring(ok)" % region,
            timeout=20.0)
    _until(lambda: bool(_roots(bus)), 1.5)
    return r == "ok=true"


def select_character(bus, cqi):
    r = _ev(bus, _G + "local c=cco('CcoCampaignCharacter','%s') if not c then return 'NO-CHAR' end "
                      "local ok,e=pcall(function() c:Call('Select') end) return 'ok='..tostring(ok)" % cqi,
            timeout=20.0)
    _until(lambda: bool(_roots(bus)), 1.2)
    return r == "ok=true"


def prepare(bus, kind, entity_id, expect_root=None, timeout=6.0):
    """Clear the screen, select the subject ("settlement"|"lord"), await expect_root; -> (ok, reason).

    The snapshot and the execute of one action both call this; when the second call would
    rebuild a state that is still standing, it returns immediately instead of closing the
    panel it is about to reopen."""
    import nav
    if (expect_root and is_selected(bus, kind, entity_id) is True
            and expect_root in (nav.visible_roots(bus) or [])):
        return True, "already_ready"
    clear_screen(bus)
    if kind == "settlement":
        ok = select_settlement(bus, entity_id)
    elif kind == "lord":
        ok = select_character(bus, entity_id)
    else:
        return False, "unknown_subject_kind_%s" % kind
    if not ok:
        return False, "could_not_select_%s_%s" % (kind, entity_id)
    if expect_root:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if expect_root in (nav.visible_roots(bus) or []):
                    return True, "ready"
            except Exception:
                pass
            time.sleep(0.5)
        return False, "expected_panel_%s_never_opened" % expect_root
    return True, "ready"


def is_selected(bus, kind, entity_id):
    """Is this exact character/settlement the one currently selected? None when unreadable.

    IsSelected is validated: it answers true/false on a real context and nil on a wrong
    property name, so nil is treated as unknown and never as 'no'."""
    ctor = ("cco('CcoCampaignCharacter','%s')" % entity_id if kind == "lord"
            else "cco('CcoCampaignSettlement','settlement:%s')" % entity_id)
    try:
        v = _ev(bus, _G + "local c=%s if not c then return 'nil' end "
                          "return ts(g(c,'IsSelected'))" % ctor, timeout=8.0)
    except Exception:
        return None
    v = str(v or "").lower()
    return True if v == "true" else (False if v == "false" else None)


def _treasury(bus):
    return _ev(bus, "return cm:get_faction(cm:get_local_faction_name(true)):treasury()", timeout=8.0)


def _roots(bus):
    import nav
    try:
        return nav.visible_roots(bus)
    except Exception:
        return None


STANCE_STACK = "hud_campaign|BL_parent|land_stance_button_stack|clip_parent|stack_background"
_STANCE_PREFIX = "button_"


def stance_options(bus):
    """{stance_key: state}; an empty dict means the stack could not be read."""
    _res, kids = _find(bus, STANCE_STACK, timeout=12.0)
    out = {}
    for k in kids:
        if not k.startswith(_STANCE_PREFIX) or k == "button_default":
            continue
        key = k[len(_STANCE_PREFIX):]
        res, _ = _find(bus, "%s|%s" % (STANCE_STACK, k), timeout=8.0)
        out[key] = res.get("state")
    if not out:
        sys.stderr.write("click_actions: stance stack %s enumerated 0 buttons\n" % STANCE_STACK)
    return out


def _selected_edict(bus, region):
    return _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s');"
                         "local m=g(s,'FactionProvinceManagerContext'); if not m then return 'NO-MGR' end "
                         "local i=g(m,'SelectedInitiative'); if i then return ts(g(i,'Key')) end "
                         "return 'none'" % region, timeout=12.0)


def edict_options(bus, region):
    """The edict keys available to this settlement's province."""
    raw = _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s');"
                        "local m=g(s,'FactionProvinceManagerContext'); if not m then return '' end "
                        "local l=g(m,'InitiativeList'); if type(l)~='table' then return '' end local o={} "
                        "for i=1,#l do o[#o+1]=ts(g(l[i],'Key')) end return table.concat(o,',')"
             % region, timeout=20.0)
    return [k for k in str(raw or "").split(",") if k and k != "nil"]


def _edict_snapshot(bus, ctx, pick):
    region = ctx["entity_id"]
    prepare(bus, "settlement", region)
    return {"selected": _selected_edict(bus, region), "options": edict_options(bus, region)}


def _edict_gate(bus, ctx, pick, before):
    if pick["key"] not in (before.get("options") or []):
        return False, "edict_not_available_in_province"
    if before.get("selected") == pick["key"]:
        return False, "already_selected"
    return True, None


def _edict_execute(bus, ctx, pick, before):
    """Click this province's edict button in the incentives stack."""
    ok, why = prepare(bus, "settlement", ctx["entity_id"])
    if not ok:
        sys.stderr.write("click_actions: edict refused, not a known state -> %s" % why + chr(10))
        return False
    key = "button_%s" % pick["key"]
    for path in ("%s|%s" % (EDICT_STACK, key),
                 "%s|clip_parent|stack_background|%s" % (EDICT_STACK, key)):
        res, _ = _find(bus, path)
        if res.get("found"):
            return _click(bus, path)
    sys.stderr.write("click_actions: edict button %s not resolvable in the stack\n" % key)
    return False


def _edict_confirm(bus, ctx, pick, before):
    sel = _selected_edict(bus, ctx["entity_id"])
    return (sel == pick["key"]), {"selected": sel}


register("edict", {
    "layer": "click", "signal": "selected_initiative_key",
    "snapshot": _edict_snapshot, "gates": [_edict_gate],
    "execute": _edict_execute, "confirm": _edict_confirm,
    "timeout_s": 6.0, "poll_s": 1.2,
})


def _pending_recruits(bus, cqi):
    """Unit keys currently queued on this force, as a sorted comma string."""
    return _ev(bus, "local c=cm:get_character_by_cqi(%s) "
                    "if not c or not c:has_military_force() then return '' end "
                    "local ok,it=pcall(function() return c:military_force():recruitment_items() end) "
                    "if not ok or not it then return '' end "
                    "local n=0 pcall(function() n=#it end) "
                    "local ks={} for i=1,n do ks[#ks+1]=tostring(it[i]) end "
                    "table.sort(ks) return table.concat(ks,',')" % cqi, timeout=12.0)


POOL_ANCHOR = "unit_list"
QUEUE_SEP = "@"


def split_key(pick):
    """(unit_key, queue) from a recruit pick. The offer key is `<unit>@<queue>` so local and
    global recruitment of the same unit are different recommendations; `params['queue']` wins
    when both are present."""
    raw = str(pick.get("key") or "")
    unit, _, suffix = raw.partition(QUEUE_SEP)
    queue = str((pick.get("params") or {}).get("queue") or suffix or "").lower() or None
    return unit, queue


def card_queue(path):
    """Which recruitment pool a card belongs to, read STRUCTURALLY: the path segment directly
    before `unit_list`, whatever it is called.

    Verified live: |global|unit_list| and |local1|unit_list|. There are more pools than those
    two (mercenary, allied/outpost, horde variants), so the name is never matched against a
    list -- an unrecognised pool must arrive tagged with its real name, not as None.
    """
    segs = str(path or "").split("|")
    try:
        i = segs.index(POOL_ANCHOR)
    except ValueError:
        return None
    return segs[i - 1] if i > 0 else None


POOL_LIST = ("units_panel|main_units_panel|recruitment_docker|recruitment_options|"
             "recruitment_listbox|recruitment_pool_list|list_clip|list_box")


def recruitable_units(bus):
    """The recruitment cards on screen: pool, cost, upkeep and turns per card.

    Cost and turns are per POOL, not per unit -- the same unit is dearer and slower globally
    (measured: peasant spearmen 350g/1t local vs 629g/2t global), so they belong to the card."""
    try:
        tr = bus.send("tree", "units_panel 30 9000", timeout=20) or {}
    except Exception:
        return []
    nodes = tr.get("nodes") or []
    by_path = {}
    for n in nodes:
        by_path.setdefault(str(n.get("path") or ""), []).append(n)

    def _child_text(card_path, *tail):
        want = card_path + "|" + "|".join(tail)
        for n in by_path.get(want, []):
            t = str(n.get("text") or "").strip()
            if t:
                return t
        return None

    def _num(v):
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    out = []
    for n in nodes:
        i = str(n.get("id") or "")
        if not (i.endswith("_recruitable") and n.get("visible")):
            continue
        path = str(n.get("path") or "")
        out.append({"key": i[:-len("_recruitable")], "state": n.get("state"), "path": path,
                    "queue": card_queue(path),
                    "cost": _num(_child_text(path, "external_holder", "RecruitmentCost", "Cost")),
                    "upkeep": _num(_child_text(path, "external_holder", "UpkeepCost", "Upkeep")),
                    "turns": _num(_child_text(path, "unit_icon", "Turns"))})
    return out


def recruitment_capacity(bus):
    """{pool: {"total", "used", "free"}} from each pool's capacity strip.

    Global slots are shared by every army in the faction; local slots are contested only
    inside the province, so the two numbers drive very different decisions."""
    out = {}
    _res, pools = _find(bus, POOL_LIST, timeout=12.0)
    for pool in pools or []:
        _r, slots = _find(bus, "%s|%s|recruitment_cap|capacity_listview" % (POOL_LIST, pool),
                          timeout=10.0)
        if not slots:
            continue
        used = sum(1 for s in slots if str(s) == "used_slot")
        out[pool] = {"total": len(slots), "used": used, "free": len(slots) - used}
    return out


def _recruit_snapshot(bus, ctx, pick):
    prepare(bus, "lord", ctx["entity_id"], expect_root="units_panel")
    r = _roots(bus)
    return {"treasury": _treasury(bus), "pending": _pending_recruits(bus, ctx["entity_id"]),
            "units_panel_open": (r is not None and "units_panel" in r),
            "capacity": recruitment_capacity(bus)}


def _recruit_gate(bus, ctx, pick, before):
    if not before.get("units_panel_open"):
        return False, "units_panel_not_open_CTD_guard"
    _unit, queue = split_key(pick)
    cap = before.get("capacity") or {}
    if queue and cap:
        pools = {k: v for k, v in cap.items()
                 if str(k).rstrip("0123456789") == queue or str(k) == queue}
        if not pools:
            return False, "queue_%s_not_offered_here" % queue
        if not any((v or {}).get("free") for v in pools.values()):
            # a full pool refuses the click; spending the action to discover that is waste
            return False, "%s_recruitment_slots_full" % queue
    return True, None


def _recruit_execute(bus, ctx, pick, before):
    """Run the recruit click, always closing the panel afterwards."""
    try:
        return _recruit_execute_inner(bus, ctx, pick, before)
    finally:
        clear_screen(bus)


def _recruit_execute_inner(bus, ctx, pick, before):
    ok, why = prepare(bus, "lord", ctx["entity_id"], expect_root="units_panel")
    if not ok:
        sys.stderr.write("click_actions: recruit_unit refused, not a known state -> %s" % why + chr(10))
        return False
    cards = recruitable_units(bus)
    if not cards:
        if not _click(bus, RECRUIT_BTN):
            return False
        _until(lambda: bool(recruitable_units(bus)), 1.4)
        cards = recruitable_units(bus)
    unit, want_q = split_key(pick)
    same_key = [c for c in cards if c["key"] == unit]
    if want_q:
        # a pool name may be indexed (local1, local2); an unindexed request must resolve to
        # exactly one of them or it is ambiguous, never a guess
        pools = [c for c in same_key if str(c.get("queue") or "").lower() == want_q]
        if not pools:
            pools = [c for c in same_key
                     if str(c.get("queue") or "").lower().rstrip("0123456789") == want_q]
        if len(pools) > 1:
            sys.stderr.write("click_actions: %r matches %d pools %s -- ambiguous, refusing\n"
                             % (want_q, len(pools), [c.get("queue") for c in pools]))
            return False
        card = pools[0] if pools else None
        if card is None and same_key:
            sys.stderr.write("click_actions: %s offered in %s but %r was asked for\n"
                             % (unit, [c.get("queue") for c in same_key], want_q))
            return False
    elif len(same_key) > 1:
        sys.stderr.write("click_actions: %s is in %d pools %s and the pick names no queue -- "
                         "refusing to guess which one\n"
                         % (unit, len(same_key), [c.get("queue") for c in same_key]))
        return False
    else:
        card = same_key[0] if same_key else None
    if card is None:
        sys.stderr.write("click_actions: unit %r not among recruitable cards\n" % pick["key"])
        return False
    return _click(bus, card["path"])


def _recruit_confirm(bus, ctx, pick, before):
    """True when the requested unit appears in the force's recruitment queue."""
    t = _treasury(bus)
    after = str(_pending_recruits(bus, ctx["entity_id"]) or "")
    prior = str(before.get("pending") or "")
    want = split_key(pick)[0]
    queued = after.count(want) > prior.count(want)
    dropped = (t is not None and before.get("treasury") is not None and t < before["treasury"])
    return queued, {"treasury": t, "pending": after, "pending_before": prior,
                    "treasury_dropped": dropped}


register("recruit_unit", {
    "layer": "click", "signal": "unit_key_in_recruitment_items",
    "snapshot": _recruit_snapshot, "gates": [_recruit_gate],
    "execute": _recruit_execute, "confirm": _recruit_confirm,
    "timeout_s": 2.0, "poll_s": 1.0, "spends_gold": True,
})


def _character_count(bus):
    """Number of characters in the faction, or None when unreadable."""
    for attempt in range(1):
        v = _ev(bus, "local f=cm:get_local_faction(true) return f:character_list():num_items()",
                timeout=20.0)
        try:
            return int(float(v))
        except (TypeError, ValueError):
            if attempt < 2:
                time.sleep(1.0)
    sys.stderr.write("click_actions: character count UNREADABLE after 3 tries -- "
                     "reporting unknown, NOT zero-growth" + chr(10))
    return None


def _character_cqis(bus):
    raw = _ev(bus, "local f=cm:get_local_faction(true); local cl=f:character_list(); local o={} "
                   "for i=0,cl:num_items()-1 do o[#o+1]=cl:item_at(i):command_queue_index() end "
                   "return table.concat(o,',')", timeout=12.0)
    return set(x for x in str(raw or "").split(",") if x.strip())


def lord_types(bus):
    """Lord-type button ids in the raise-army panel."""
    _res, kids = _find(bus, LORD_TYPE_LIST)
    return [k for k in kids if not k.startswith("button_template")]


def lore_tabs(bus):
    """[(lore_id, path)] of the magic lores offered for the selected type, in panel order.

    A caster type has a lore level between the type and its candidates: each lore keeps its
    own roster, so the lore decides WHO can be recruited, not just what they cast."""
    try:
        t = bus.send("tree", "%s 6 4000" % LORE_LIST, timeout=20) or {}
    except Exception as e:
        sys.stderr.write("click_actions: lore tree -> %s\n" % repr(e)[:80])
        return []
    out = []
    for n in (t.get("nodes") or []):
        i = str(n.get("id") or "")
        if "lore" in i and n.get("visible") and str(n.get("path") or "").endswith(i):
            if i not in ("lore_type_list",):
                out.append((i, str(n.get("path"))))
    return out


def split_lord_key(bus, key, types):
    """(type_id, lore_hint) for a recruit_lord key against the panel's real type ids.

    Longest matching type id wins and whatever remains is the lore hint, so both key shapes
    work: an explicit `type@lore`, and the engine subtypes that bake the lore into the name
    (wh3_dlc23_chd_sorcerer_prophet_fire -> chd_sorcerer_prophet + fire)."""
    want, _, explicit = str(key).partition("@")
    hits = [t for t in types if want == t or want.endswith("_" + t) or ("_" + t + "_") in want]
    if not hits:
        return None, (explicit or None)
    t = max(hits, key=len)
    tail = want.split(t, 1)[1].strip("_") if t in want else ""
    return t, (explicit or tail or None)


def lord_candidates(bus):
    """`general_candidate_<n>_` rows of the SELECTED type only, top row first.

    Every type's roster lives in the tree at once, stacked on the same coordinates; picking
    a type only changes which rows are visible. Child ids alone therefore span all types."""
    try:
        t = bus.send("tree", "%s 6 4000" % CANDIDATE_LIST, timeout=20) or {}
    except Exception as e:
        sys.stderr.write("click_actions: candidate tree -> %s\n" % repr(e)[:80])
        return []
    rows = [n for n in (t.get("nodes") or [])
            if str(n.get("id") or "").startswith("general_candidate") and n.get("visible")
            # state, not position: an id-click selects a row scrolled out of the viewport and
            # the list scrolls itself to it (measured), while an inactive row ignores the click
            and str(n.get("state")) in ("active", "selected")]
    rows.sort(key=lambda n: (n.get("y") or 0, n.get("x") or 0))
    return [str(n.get("id")) for n in rows]


def _lord_snapshot(bus, ctx, pick):
    n = _character_count(bus)
    if n is None:
        return None
    return {"treasury": _treasury(bus), "n_chars": n}


def _lord_execute(bus, ctx, pick, before):
    """Run the raise-army sequence, always closing the panel afterwards."""
    try:
        return _lord_execute_inner(bus, ctx, pick, before)
    finally:
        clear_screen(bus)


def _lord_execute_inner(bus, ctx, pick, before):
    ok, why = prepare(bus, "settlement", ctx["entity_id"], expect_root="settlement_panel")
    if not ok:
        sys.stderr.write("click_actions: recruit_lord refused, not a known state -> %s" % why + chr(10))
        return False
    r = _roots(bus)
    if not (r and "character_panel" in r):
        if not _click(bus, CREATE_ARMY_BTN):
            return False
        _until(lambda: "character_panel" in (_roots(bus) or []), 1.8)
    want = str(pick["key"])

    lord_list = [k for k in _find(bus, LORD_TYPE_LIST)[1] if not k.startswith("button_template")]
    agent_list = [k for k in _find(bus, AGENT_TYPE_LIST)[1] if not k.startswith("button_template")]
    btn, lore_hint = split_lord_key(bus, want, lord_list)
    type_path, commit_id = LORD_TYPE_LIST, "button_raise"
    if btn is None:
        btn, lore_hint = split_lord_key(bus, want, agent_list)
        type_path, commit_id = AGENT_TYPE_LIST, "button_confirm"
    if btn is None:
        sys.stderr.write("click_actions: %r is neither a lord type %s nor a hero type %s"
                         % (want, lord_list, agent_list) + chr(10))
        return False
    if not _click(bus, "%s|%s" % (type_path, btn)):
        return False
    _until(lambda: bool(lord_candidates(bus)) or bool(lore_tabs(bus)), 1.5)
    lores = lore_tabs(bus)
    if lores:
        if not lore_hint:
            sys.stderr.write("click_actions: %s offers %d lores %s and the pick names none -- "
                             "refusing to accept whichever tab happens to be open\n"
                             % (btn, len(lores), [i for i, _p in lores]))
            return False
        hit = [(i, pth) for i, pth in lores if lore_hint.lower() in i.lower()]
        if len(hit) != 1:
            sys.stderr.write("click_actions: lore %r matches %d of %s -- refusing\n"
                             % (lore_hint, len(hit), [i for i, _p in lores]))
            return False
        if not _click(bus, hit[0][1]):
            return False
        _until(lambda: bool(lord_candidates(bus)), 2.0)
    cands = lord_candidates(bus)
    if not cands:
        sys.stderr.write("click_actions: no general_candidate rows for %r\n" % pick["key"])
        return False
    idx = int((pick.get("params") or {}).get("candidate_index", 0))
    if not engine_click(bus, cands[min(idx, len(cands) - 1)]):
        return False
    res, _ = _find(bus, BUTTON_RAISE)
    if res.get("state") != "active":
        sys.stderr.write("click_actions: button_raise not active after candidate select (state=%s)\n"
                         % res.get("state"))
        return False
    return engine_click(bus, commit_id)


def _lord_confirm(bus, ctx, pick, before):
    """True when the faction's character count grew."""
    n = _character_count(bus)
    t = _treasury(bus)
    before_n = before.get("n_chars")
    if n is None:
        return False, {"n_chars": None, "n_chars_before": before_n, "unreadable": True}
    grew = (before_n is not None and n > before_n)
    return grew, {"n_chars": n, "n_chars_before": before_n, "treasury": t,
                  "treasury_dropped": (t is not None and before.get("treasury") is not None
                                       and t < before["treasury"])}


register("recruit_lord", {
    "layer": "click", "signal": "new_character_cqi_and_treasury_drop",
    "snapshot": _lord_snapshot,
    "execute": _lord_execute, "confirm": _lord_confirm,
    "timeout_s": 6.0, "poll_s": 2.0, "spends_gold": True,
})
