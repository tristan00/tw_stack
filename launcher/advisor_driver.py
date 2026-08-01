r"""advisor_driver.py -- drives menus from the advisor service's /api/advise picks."""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

RUNS_ROOT = r"D:\twdata\runs\human"
ADVISE_URL = "http://127.0.0.1:8770/api/advise"

FORBIDDEN_KEYS = frozenset({"button_attack", "button_spectate"})


def advice_endpoint(menu_type, run_id=None, timeout=30.0):
    """Top-available pick {key,name,combined,...,x,y,w,h} for `menu_type`, or None."""
    q = {"type": menu_type}
    if run_id:
        q["run"] = run_id
    url = ADVISE_URL + "?" + urllib.parse.urlencode(q)
    try:
        d = json.load(urllib.request.urlopen(url, timeout=timeout))
    except Exception as e:
        sys.stderr.write("advisor_driver: /api/advise(%s) -> %s\n" % (menu_type, repr(e)[:90]))
        return None
    if d.get("error") or not d.get("options"):
        sys.stderr.write("advisor_driver: advise(%s) no options (%s)\n"
                         % (menu_type, d.get("error") or d.get("note")))
        return None
    pick = _top_available(d["options"])
    if not pick:
        return None
    return {"key": pick.get("key"), "name": pick.get("name"), "combined": pick.get("combined"),
            "exploit": pick.get("exploit"), "explore": pick.get("explore"),
            "available": pick.get("available"), "n_options": d.get("n"),
            "x": pick.get("x"), "y": pick.get("y"), "w": pick.get("w"), "h": pick.get("h")}


def freshest_run():
    """The run dir with the most-recently-written data files."""
    best, best_m = None, -1.0
    try:
        for name in os.listdir(RUNS_ROOT):
            p = os.path.join(RUNS_ROOT, name)
            for f in ("ui_components.jsonl", "events.jsonl"):
                fp = os.path.join(p, f)
                try:
                    m = os.path.getmtime(fp)
                except OSError:
                    continue
                if m > best_m:
                    best, best_m = p, m
    except OSError as e:
        sys.stderr.write("advisor_driver: cannot list runs -> %s\n" % e)
    return best


def _top_available(options):
    """Highest-`combined` option whose `available` is not False and whose key is allowed, else None."""
    typed = [o for o in sorted(options, key=lambda o: -(o.get("combined") or 0.0))
             if (o.get("key") or o.get("id")) not in FORBIDDEN_KEYS]
    for o in typed:
        if o.get("available") is not False:
            return o
    sys.stderr.write("advisor_driver: no AVAILABLE option among %d (none picked)\n" % len(typed))
    return None


def drive_menu(bus, run_dir, menu_type, opener=None, verifier=None, find_verb=None, timeout=35.0):
    """opener -> advice_endpoint -> execute (find_verb or coord click) -> verifier.

    Returns a result dict (menu, pick, executed, verify, note).
    """
    if opener:
        try:
            opener(bus)
        except Exception as exc:
            return {"menu": menu_type, "note": "opener failed: %s" % repr(exc)[:80]}
    run_id = os.path.basename(str(run_dir).rstrip("/\\")) if run_dir else None
    pick = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        pick = advice_endpoint(menu_type, run_id)
        if pick:
            break
        time.sleep(1.5)
    if not pick:
        return {"menu": menu_type, "note": "no advice from /api/advise within %ss" % timeout}
    # exactly one execution path per pick, no chaining
    if find_verb is not None:
        executed = bool(find_verb(bus, pick.get("key")))
    elif menu_type == "skills" or (pick.get("x") is not None and pick.get("y") is not None):
        executed = execute(bus, menu_type, pick)
    else:
        return {"menu": menu_type, "pick": pick,
                "note": "pick lacks full coords and no find_verb given (needs a targeted action verb)"}
    verify = verifier(bus) if verifier else None
    return {"menu": menu_type, "pick": {"key": pick.get("key"), "name": pick.get("name"),
                                        "combined": pick.get("combined")},
            "executed": executed, "verify": verify}


def execute(bus, menu_type, pick):
    """Click the pick's captured coords (or, for skills, its node's |card). True if clicked."""
    import nav
    key = (pick or {}).get("key")
    if key in FORBIDDEN_KEYS:
        sys.stderr.write("advisor_driver: REFUSING forbidden key %r (battle UI is out of scope)\n" % key)
        return False
    if menu_type == "skills":
        n = nav.find_rect(bus, "character_details_panel", key)
        if not n or not n.get("path"):
            sys.stderr.write("advisor_driver: skills node %r not found\n" % key)
            return False
        nav.bus_click(bus, n["path"] + "|card")
        time.sleep(1.0)
        return True
    if not pick or pick.get("x") is None or pick.get("y") is None:
        sys.stderr.write("advisor_driver: %s pick %r lacks full coords -> needs a find-based action verb\n"
                         % (menu_type, key))
        return False
    sx, sy = nav.ui_to_screen(pick["x"] + (pick.get("w") or 0) / 2.0,
                              pick["y"] + (pick.get("h") or 0) / 2.0)
    nav.mouse("move", sx, sy)
    time.sleep(0.3)
    nav.mouse("click", sx, sy)
    time.sleep(1.0)
    if menu_type == "research":
        nav.bus_click(bus, "technology_panel|button_ok_holder|button_ok")
        time.sleep(1.0)
    return True


LORD_CQI = 56
SHRINE = "wh3_main_combi_region_shrine_of_ladrielle"


def _open_research(bus):
    import turn1, nav
    turn1.clear_intro(bus)
    nav.bus_click(bus, turn1._TECH_BTN)
    time.sleep(1.8)


def _open_recruit(bus):
    import turn1, nav
    turn1.clear_intro(bus)
    if not turn1._select_army(bus, LORD_CQI):
        raise RuntimeError("could not select lord %d's army" % LORD_CQI)
    if "units_panel" not in nav.visible_roots(bus):
        raise RuntimeError("units_panel not open -- refusing to toggle recruitment")
    nav.bus_click(bus, turn1._RECRUIT_BTN)
    time.sleep(1.4)


def _open_skills(bus):
    import turn1, nav
    turn1.clear_intro(bus)
    if not turn1._open_details(bus, LORD_CQI):
        raise RuntimeError("character_details_panel did not open")
    nav.bus_click(bus, turn1._CD_TAB_SKILLS)
    time.sleep(1.2)


def _open_items(bus):
    import turn1, nav
    turn1.clear_intro(bus)
    if not turn1._open_details(bus, LORD_CQI):
        raise RuntimeError("character_details_panel did not open")
    nav.bus_click(bus, turn1._CD_TAB_DETAILS)
    time.sleep(1.2)


def _wait_root(bus, root, tries=20, pause=1.0):
    import nav
    for _ in range(tries):
        if root in nav.visible_roots(bus):
            return True
        time.sleep(pause)
    return False


def _attack(bus, dx, dy, what):
    """Select the lord's army and right-click map position (dx,dy); waits for popup_pre_battle."""
    import turn1
    if not turn1._select_army(bus, LORD_CQI):
        raise RuntimeError("could not select lord %d's army (attacking %s)" % (LORD_CQI, what))
    turn1._rclick_center_on(bus, dx, dy)
    if not _wait_root(bus, "popup_pre_battle", tries=10, pause=0.7):
        raise RuntimeError("attack on %s opened no pre-battle" % what)


def _log_advice(mtype, run_id):
    """Request and print the advisor's pick for a menu without acting on it."""
    a = advice_endpoint(mtype, run_id, timeout=20.0)
    print("  advisor(%s) pick [LOGGED ONLY]: %s" % (mtype, a), flush=True)
    return a


def _force_autoresolve(bus):
    """Click the pre_battle autoresolve button and wait for popup_battle_results."""
    import nav
    n = nav.find_rect(bus, "popup_pre_battle", "button_autoresolve")
    if not n or n.get("x") is None:
        raise RuntimeError("button_autoresolve not found in popup_pre_battle")
    sx, sy = nav.ui_to_screen(n["x"] + n["w"] / 2.0, n["y"] + n["h"] / 2.0)
    nav.mouse("move", sx, sy)
    time.sleep(0.3)
    nav.mouse("click", sx, sy)
    if not _wait_root(bus, "popup_battle_results", tries=25, pause=1.0):
        raise RuntimeError("no popup_battle_results after autoresolve click")


def _occupation_verb(bus, key):
    """Commit the occupation option whose node id is `key`, via its |option_button."""
    import turn1, nav
    n = turn1._find_node(bus, "settlement_captured", lambda x: str(x.get("id")) == str(key))
    if not n:
        sys.stderr.write("advisor_driver: occupation option %r not in settlement_captured\n" % key)
        return False
    nav.bus_click(bus, n["path"] + "|option_button")
    time.sleep(2.0)
    return True


def run_turn(bus, run_dir):
    """Run the scripted turn (battles, then economy); returns the per-menu result dicts."""
    import turn1, nav
    run_id = os.path.basename(str(run_dir).rstrip("/\\")) if run_dir else None
    results = []

    def record(mtype, res, verify=None):
        if verify is not None:
            res["verify"] = verify
        ok = bool(res.get("executed")) and (res.get("verify") is None
                                            or any(v for v in res["verify"].values()))
        print("  pick=%s executed=%s verify=%s -> %s"
              % (res.get("pick"), res.get("executed"), res.get("verify"),
                 "PASS" if ok else "FAIL (%s)" % res.get("note", "")), flush=True)
        results.append(res)
        return res

    print("== battle 1: nearest enemy army ==", flush=True)
    turn1.clear_intro(bus)
    e = turn1._nearest_enemy_army(bus)
    if not e:
        raise RuntimeError("no enemy army to attack")
    ex = turn1._ev(bus, "cm:get_character_by_cqi(%d):display_position_x()" % e["cqi"])
    ey = turn1._ev(bus, "cm:get_character_by_cqi(%d):display_position_y()" % e["cqi"])
    _attack(bus, ex, ey, "army cqi %s" % e["cqi"])
    time.sleep(3.0)
    _log_advice("pre_battle", run_id)
    _force_autoresolve(bus)
    print("== captives (advisor-driven) ==", flush=True)
    res = drive_menu(bus, run_dir, "captives")
    nav.close_popups(bus)
    time.sleep(0.8)
    dead = turn1._ev(bus, "not cm:get_character_by_cqi(%d)" % e["cqi"])
    record("captives", res, {"target_dead": bool(dead)})

    print("== battle 2: %s ==" % SHRINE, flush=True)
    turn1.clear_intro(bus)
    dx = turn1._ev(bus, "cm:get_region('%s'):settlement():display_position_x()" % SHRINE)
    dy = turn1._ev(bus, "cm:get_region('%s'):settlement():display_position_y()" % SHRINE)
    _attack(bus, dx, dy, SHRINE)
    time.sleep(3.0)
    _log_advice("pre_battle", run_id)
    _force_autoresolve(bus)
    chk = turn1._find_node(bus, "popup_battle_results", lambda n: (
        n.get("id") == "button_accept" and "settlement_captured" in str(n.get("path", ""))
        and n.get("visible")))
    if not chk:
        raise RuntimeError("no settlement_captured checkmark in battle results")
    nav.bus_click(bus, chk["path"])
    time.sleep(2.5)
    print("== occupation (advisor-driven) ==", flush=True)
    res = drive_menu(bus, run_dir, "occupation", find_verb=_occupation_verb)
    nav.close_popups(bus)
    time.sleep(0.8)
    short = SHRINE.split("region_")[-1]
    mine = [s.get("region") for s in (bus.send("setts", "", timeout=6) or {}).get("setts") or []]
    record("occupation", res, {"own_region": any(short in (m or "") for m in mine)})

    for mtype, opener, find_verb, close_details in (
            ("recruit", _open_recruit, None, False),
            ("research", _open_research, None, False),
            ("skills", _open_skills, None, True),
            ("items", _open_items, None, True)):
        print("== %s (advisor-driven) ==" % mtype, flush=True)
        t0 = turn1.treasury(bus)
        res = drive_menu(bus, run_dir, mtype, opener=opener, find_verb=find_verb)
        key = (res.get("pick") or {}).get("key")
        t1 = turn1.treasury(bus)
        verify = None
        if mtype in ("recruit", "building"):
            verify = {"treasury": "%s -> %s" % (t0, t1),
                      "dropped": t0 is not None and t1 is not None and t1 < t0}
        elif mtype == "research":
            verify = {"is_researching": turn1._is_researching(bus)}
        elif mtype == "skills" and key:
            verify = {"has_skill": turn1._has_skill(bus, LORD_CQI, key)}
        if close_details:
            nav.bus_click(bus, turn1._CD_OK)
            time.sleep(0.8)
        nav.close_popups(bus)
        nav.deselect(bus)
        time.sleep(0.6)
        record(mtype, res, verify)
    return results


API = "http://127.0.0.1:8770"


def _get_json(url, timeout=45.0):
    try:
        return json.load(urllib.request.urlopen(url, timeout=timeout))
    except Exception as e:
        sys.stderr.write("loop: GET %s -> %s\n" % (url.split("?")[0], repr(e)[:90]))
        return None


def _post_json(url, body, timeout=20.0):
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as e:
        sys.stderr.write("loop: POST %s -> %s\n" % (url.split("?")[0], repr(e)[:90]))
        return None


def _actions_row(run_id, entity_kind, entity_id, action_type):
    d = _get_json("%s/api/actions?%s" % (API, urllib.parse.urlencode(
        {"run": run_id, "entity_kind": entity_kind, "entity_id": entity_id,
         "type": action_type})))
    rows = (d or {}).get("rows") or []
    return rows[0] if rows else None


def _wait_actions_turn(run_id, turn, timeout=90.0):
    """Block until the actions stream's entities row reaches `turn`; returns its payload."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = _actions_row(run_id, "campaign", "entities", "entities")
        if row and row.get("turn") == turn:
            return row["payload"]
        time.sleep(2.0)
    raise RuntimeError("actions sweep never reached turn %s within %ss" % (turn, timeout))


def _advise_entity(run_id, mtype, entity_kind, entity_id):
    return _get_json("%s/api/advise?%s" % (API, urllib.parse.urlencode(
        {"type": mtype, "run": run_id, "entity_kind": entity_kind, "entity_id": entity_id})),
        timeout=90.0)


def _bootstrap_stance_whitelist(bus, run_id, cqi):
    """Stance advice for `cqi`; selects the army once first if no option is marked legal yet."""
    import turn1, nav
    d = _advise_entity(run_id, "stance", "lord", cqi)
    if d and any(o.get("legal") for o in d.get("options") or []):
        return d
    print("  [whitelist] no army_stances capture yet -> selecting lord %s once" % cqi, flush=True)
    if not turn1._select_army(bus, int(cqi)):
        sys.stderr.write("loop: whitelist bootstrap could not select lord %s\n" % cqi)
        return d
    time.sleep(6.0)
    nav.deselect(bus)
    time.sleep(1.0)
    return _advise_entity(run_id, "stance", "lord", cqi)


def run_campaign_loop(bus, run_dir, turns=3):
    """lords -> settlements -> campaign menus -> end turn, xN; appends to <run_dir>/loop_report.jsonl."""
    import turn1, nav
    import actions as ACT
    run_id = os.path.basename(str(run_dir).rstrip("/\\"))
    report_path = os.path.join(run_dir, "loop_report.jsonl")

    def report(row):
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    turn0 = turn1._ev(bus, "return cm:model():turn_number()")
    for tix in range(turns):
        turn = turn1._ev(bus, "return cm:model():turn_number()")
        print("== LOOP turn %s (%d/%d) ==" % (turn, tix + 1, turns), flush=True)
        turn1.clear_intro(bus)
        acted, failures = [], []
        ents = _wait_actions_turn(run_id, turn)

        for lord in ents.get("lords") or []:
            cqi = str(lord["cqi"])
            _post_json("%s/api/actions/refresh" % API,
                       {"entity_kind": "lord", "entity_id": cqi, "run": run_id})
            time.sleep(3.0)
            d = _bootstrap_stance_whitelist(bus, run_id, cqi)
            pick = _top_available((d or {}).get("options") or [])
            if pick and not pick.get("active"):
                legal = {o["key"] for o in d.get("options") or [] if o.get("legal")}
                ok = ACT.cco_activate_stance(bus, cqi, pick["key"], legal)
                (acted if ok else failures).append(
                    {"phase": "lord", "entity": cqi, "type": "stance",
                     "key": pick["key"], "ok": ok})
                print("  lord %s stance -> %s : %s" % (cqi, pick["key"], "OK" if ok else "FAIL"),
                      flush=True)
            else:
                print("  lord %s stance: no available non-active pick" % cqi, flush=True)
            try:
                res = drive_menu(bus, run_dir, "recruit", opener=_open_recruit)
                ok = bool(res.get("executed"))
                (acted if ok else failures).append(
                    {"phase": "lord", "entity": cqi, "type": "recruit",
                     "key": (res.get("pick") or {}).get("key"), "ok": ok,
                     "note": res.get("note")})
                print("  lord %s recruit -> %s" % (cqi, res.get("pick") or res.get("note")),
                      flush=True)
            except Exception as e:
                failures.append({"phase": "lord", "entity": cqi, "type": "recruit",
                                 "ok": False, "note": repr(e)[:120]})
            nav.close_popups(bus)
            nav.deselect(bus)

        for region in ents.get("regions") or []:
            _post_json("%s/api/actions/refresh" % API,
                       {"entity_kind": "settlement", "entity_id": region, "run": run_id})
            time.sleep(3.0)
            d = _advise_entity(run_id, "building", "settlement", region)
            pick = _top_available((d or {}).get("options") or [])
            if pick:
                ok = ACT.cco_construct(bus, region, pick.get("slot_index"), pick["key"],
                                       expected_cost=pick.get("cost"))
                (acted if ok else failures).append(
                    {"phase": "settlement", "entity": region, "type": "building",
                     "key": pick["key"], "slot": pick.get("slot_index"), "ok": ok})
                print("  %s building -> %s : %s" % (region.split("region_")[-1], pick["key"],
                                                    "OK" if ok else "FAIL"), flush=True)
            else:
                print("  %s building: nothing buildable" % region.split("region_")[-1], flush=True)
            de = _advise_entity(run_id, "edict", "settlement", region)
            epick = _top_available((de or {}).get("options") or [])
            if epick:
                failures.append({"phase": "settlement", "entity": region, "type": "edict",
                                 "key": epick["key"], "ok": False,
                                 "note": "edict EXECUTION not implemented (needs complete-province "
                                         "verification of the UI stack click)"})
                print("  %s edict advice: %s (execution not implemented)" %
                      (region.split("region_")[-1], epick["key"]), flush=True)

        if turn1._is_researching(bus) is not True:
            try:
                res = drive_menu(bus, run_dir, "research", opener=_open_research)
                ok = bool(res.get("executed"))
                (acted if ok else failures).append(
                    {"phase": "campaign", "type": "research",
                     "key": (res.get("pick") or {}).get("key"), "ok": ok, "note": res.get("note")})
                print("  research -> %s" % (res.get("pick") or res.get("note")), flush=True)
            except Exception as e:
                failures.append({"phase": "campaign", "type": "research", "ok": False,
                                 "note": repr(e)[:120]})
        nav.close_popups(bus)

        report({"turn": turn, "acted": acted, "failures": failures, "ts": time.time()})

        if tix == turns - 1:
            print("== loop complete (turn %s; %d turns from %s) ==" % (turn, turns, turn0),
                  flush=True)
            break
        print("  ending turn %s ..." % turn, flush=True)
        bus.send("eval", "local q=cco('CcoCampaignPendingActionNotificationQueue','');"
                         "if q then pcall(function() q:Call('SetAllNotificationsSuppressed(true)') end) end "
                         "return 'ok'", timeout=10)
        r = bus.send("end_turn", "", timeout=15) or {}
        btn = r.get("button") or {}
        if btn.get("x") is None:
            raise RuntimeError("end_turn button rect unavailable: %s" % r)
        sx, sy = nav.ui_to_screen(btn["x"] + btn.get("w", 95) / 2.0,
                                  btn["y"] + btn.get("h", 95) / 2.0)
        nav.mouse("move", sx, sy)
        time.sleep(0.3)
        nav.mouse("click", sx, sy)
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(3.0)
            roots = nav.visible_roots(bus)
            if "popup_pre_battle" in roots:
                print("  defensive battle -> forced autoresolve", flush=True)
                _log_advice("pre_battle", run_id)
                _force_autoresolve(bus)
                res = drive_menu(bus, run_dir, "captives")
                nav.close_popups(bus)
                report({"turn": turn, "interrupt": "defensive_battle",
                        "captives": res.get("pick"), "ts": time.time()})
                continue
            nt = turn1._ev(bus, "return cm:model():turn_number()")
            if nt is not None and nt > turn:
                print("  turn advanced %s -> %s" % (turn, nt), flush=True)
                break
        else:
            raise RuntimeError("turn never advanced after end_turn (turn %s)" % turn)
        turn1.clear_intro(bus)
    return True


if __name__ == "__main__":
    # usage: advisor_driver.py run [run_dir] | loop [N] [run_dir] | [menu_type] [run_dir]
    if len(sys.argv) > 1 and sys.argv[1] == "loop":
        from bus import Bus
        n_turns = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 3
        run = (sys.argv[3] if len(sys.argv) > 3 and os.path.isdir(sys.argv[3]) else freshest_run())
        print("run dir:", run, "| turns:", n_turns)
        run_campaign_loop(Bus(), run, turns=n_turns)
    elif len(sys.argv) > 1 and sys.argv[1] == "run":
        from bus import Bus
        run = (sys.argv[2] if len(sys.argv) > 2 and os.path.isdir(sys.argv[2]) else freshest_run())
        print("run dir:", run)
        out = run_turn(Bus(), run)
        n_ok = sum(1 for r in out if r.get("executed"))
        print("RESULT: %d/%d executed" % (n_ok, len(out)))
        if n_ok < len(out):
            sys.exit(1)
    else:
        mtype = sys.argv[1] if len(sys.argv) > 1 else "research"
        run = sys.argv[2] if len(sys.argv) > 2 else freshest_run()
        print("run:", run)
        rid = os.path.basename(str(run).rstrip("/\\")) if run else None
        a = advice_endpoint(mtype, rid, timeout=8)
        if a:
            print("ADVISOR PICK for %s: key=%s name=%r combined=%s available=%s (of %s options)"
                  % (mtype, a["key"], a["name"], a["combined"], a["available"], a["n_options"]))
        else:
            print("no %s advice from %s for run %s" % (mtype, ADVISE_URL, rid))
