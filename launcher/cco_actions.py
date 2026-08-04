r"""cco_actions.py -- confirmed-action engine, action REGISTRY, and the cco-layer executors."""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

TREASURY_FLOOR = 500
try:
    import config as _cfg
    TREASURY_FLOOR = getattr(_cfg, "TREASURY_FLOOR", TREASURY_FLOOR)
except Exception:
    pass

FORBIDDEN_KEYS = frozenset({"button_attack", "button_spectate"})

_G = ("local function g(c,p) local ok,v=pcall(function() return c:Call(p) end);"
      "if ok and v~=nil then return v end return nil end "
      "local function ts(v) return tostring(v) end ")

_LUA_TREASURY = "return cm:get_faction(cm:get_local_faction_name(true)):treasury()"

_LUA_STANCE_STATE = (_G +
    "local mf=cco('CcoCampaignCharacter','%(cqi)s'):Call('MilitaryForceContext');"
    "if not mf then return 'NO-FORCE' end local l=mf:Call('StanceList');"
    "if type(l)~='table' then return 'NO-LIST' end local o={} "
    "for i,v in ipairs(l) do o[#o+1]=ts(g(v,'Key'))..'~'..ts(g(v,'IsActive'))"
    "..'~'..ts(g(v,'CanBeActivated'))..'~'..ts(g(v,'CanAfford')) end "
    "return table.concat(o,'|')")

_LUA_STANCE_ACTIVATE = (_G +
    "local mf=cco('CcoCampaignCharacter','%(cqi)s'):Call('MilitaryForceContext');"
    "if not mf then return 'NO-FORCE' end local l=mf:Call('StanceList');"
    "for i,v in ipairs(l) do if ts(g(v,'Key'))=='%(key)s' then "
    "pcall(function() v:Call('Activate') end); return 'called' end end return 'NOT-IN-LIST'")

_LUA_SLOT_STATE = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(region)s');"
    "if not s then return 'NO-CTX' end local slots=s:Call('BuildingSlotList');"
    "if type(slots)~='table' then return 'NO-SLOTLIST' end "
    "for i,x in ipairs(slots) do if g(x,'Index')==%(slot)d then "
    "return ts(g(x,'IsBuildingNew')) end end return 'NO-SLOT'")

_LUA_SLOT_QUEUED = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(region)s');"
    "if not s then return 'NO-CTX' end local slots=s:Call('BuildingSlotList');"
    "if type(slots)~='table' then return 'NO-SLOTLIST' end "
    "for i,x in ipairs(slots) do if g(x,'Index')==%(slot)d then "
    "local ci=g(x,'ConstructionItemContext') if not ci then return 'NONE' end "
    "local cl=g(ci,'BuildingLevelRecordContext') "
    "return ts(cl and g(cl,'Key') or 'NONE') end end return 'NO-SLOT'")

_LUA_CONSTRUCT = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(region)s');"
    "if not s then return 'NO-CTX' end local slots=s:Call('BuildingSlotList');"
    "for i,x in ipairs(slots) do if g(x,'Index')==%(slot)d then "
    "  local p=g(x,'PossibleUpgradeWithoutConversionsList');"
    "  if type(p)~='table' then return 'NO-POSSIBLES' end "
    "  for j=0,#p-1 do "
    "    if ts(g(x,'PossibleUpgradeWithoutConversionsList['..j..'].Key'))=='%(key)s' then "
    "      if g(x,'BuildingRequirementsMet(PossibleUpgradeWithoutConversionsList['..j..'])')~=true then "
    "        return 'REQ-NOT-MET' end "
    "      pcall(function() x:Call('Construct(PossibleUpgradeWithoutConversionsList['..j..'])') end);"
    "      return 'called' end end return 'KEY-NOT-IN-SLOT' end end return 'NO-SLOT'")


def _ev(bus, lua, timeout=15.0):
    """One eval; returns the result, or None on any failure."""
    try:
        r = bus.send("eval", lua, timeout=timeout) or {}
    except Exception as e:
        sys.stderr.write("cco_actions: bus eval failed -> %s\n" % repr(e)[:120])
        return None
    if r.get("error"):
        sys.stderr.write("cco_actions: lua error -> %s\n" % str(r["error"])[:160])
        return None
    return r.get("result")


def _treasury(bus):
    return _ev(bus, _LUA_TREASURY, timeout=8.0)


REGISTRY = {}


def register(action_type, spec):
    spec.setdefault("timeout_s", 6.0)
    spec.setdefault("poll_s", 1.2)
    spec.setdefault("retryable", True)
    spec.setdefault("spends_gold", False)
    spec.setdefault("gates", [])
    REGISTRY[action_type] = spec
    return spec


def execute_confirmed(bus, ctx, pick):
    """Run one action through snapshot -> gates -> execute -> poll-confirm; returns a record dict."""
    atype = pick.get("action_type")
    rec = {"ts": time.time(), "context_kind": ctx.get("context_kind"),
           "entity_id": str(ctx.get("entity_id")), "action_type": atype,
           "key": pick.get("key"), "params": pick.get("params") or {},
           "policy": pick.get("policy"), "layer": None, "attempt": pick.get("attempt", 1),
           "executed": False, "confirmed": False, "counted": False, "refusal": None,
           "confirm": None, "gates": {"passed": True, "failed_gate": None}}
    spec = REGISTRY.get(atype)
    if spec is None:
        rec["refusal"] = "unknown_action_type"
        return rec
    rec["layer"] = spec["layer"]
    if pick.get("key") in FORBIDDEN_KEYS:
        rec["refusal"] = "forbidden_key"
        return rec
    if atype == "noop":
        rec.update(executed=True, confirmed=True, counted=True,
                   confirm={"signal": "none", "before": {}, "after": {}, "latency_ms": 0})
        return rec
    _t_start = time.time()
    _t = {}
    _ts = time.time()
    try:
        before = spec["snapshot"](bus, ctx, pick)
    except Exception as e:
        rec["refusal"] = "snapshot_failed"
        rec["confirm"] = {"signal": None, "error": repr(e)[:160]}
        rec["timing"] = {"snapshot_ms": int((time.time() - _ts) * 1000), "total_ms":
                         int((time.time() - _t_start) * 1000)}
        return rec
    _t["snapshot_ms"] = int((time.time() - _ts) * 1000)
    if before is None:
        rec["refusal"] = "snapshot_failed"
        rec["timing"] = dict(_t, total_ms=int((time.time() - _t_start) * 1000))
        return rec
    _tg = time.time()
    if spec["spends_gold"]:
        cost = (pick.get("params") or {}).get("cost") or 0
        t = before.get("treasury")
        if t is not None and t - cost < TREASURY_FLOOR:
            rec["gates"] = {"passed": False, "failed_gate": "treasury_floor"}
            rec["refusal"] = "pre_check_refused"
            rec["timing"] = dict(_t, gates_ms=int((time.time() - _tg) * 1000),
                                 total_ms=int((time.time() - _t_start) * 1000))
            return rec
    for gate in spec["gates"]:
        ok, reason = gate(bus, ctx, pick, before)
        if not ok:
            rec["gates"] = {"passed": False, "failed_gate": reason}
            rec["refusal"] = "pre_check_refused"
            rec["timing"] = dict(_t, gates_ms=int((time.time() - _tg) * 1000),
                                 total_ms=int((time.time() - _t_start) * 1000))
            return rec
    _t["gates_ms"] = int((time.time() - _tg) * 1000)
    t0 = time.time()
    try:
        rec["executed"] = bool(spec["execute"](bus, ctx, pick, before))
    except Exception as e:
        rec["executed"] = False
        rec["execute_error"] = repr(e)[:160]
    _t["execute_ms"] = int((time.time() - t0) * 1000)
    _tc = time.time()
    deadline = time.time() + spec["timeout_s"]
    confirmed, after, polls = False, {}, 0
    doomed = spec.get("doomed")
    while True:
        polls += 1
        try:
            confirmed, after = spec["confirm"](bus, ctx, pick, before)
        except Exception as e:
            after = {"error": repr(e)[:160]}
            confirmed = False
        if confirmed or time.time() >= deadline:
            break
        if not rec.get("executed"):
            break
        if doomed is not None:
            try:
                why = doomed(bus, ctx, pick, before, after)
            except Exception:
                why = None
            if why:
                rec["doomed"] = why
                sys.stderr.write("cco_actions: %s confirm unreachable (%s) -- stopped at %.1fs of "
                                 "%.1fs\n" % (pick.get("action_type"), why, time.time() - _tc,
                                              spec["timeout_s"]))
                break
        time.sleep(spec["poll_s"])
    rec["confirmed"] = bool(confirmed)
    rec["counted"] = rec["confirmed"]
    rec["confirm"] = {"signal": spec.get("signal"), "before": before, "after": after,
                     "latency_ms": int((time.time() - t0) * 1000), "polls": polls,
                     "timeout_s": spec["timeout_s"]}
    _t["confirm_ms"] = int((time.time() - _tc) * 1000)
    _t["polls"] = polls
    _t["confirm_wasted_ms"] = 0 if confirmed else _t["confirm_ms"]
    _t["total_ms"] = int((time.time() - _t_start) * 1000)
    rec["timing"] = _t
    if not rec["counted"]:
        changed = (any(after.get(k) != before.get(k) for k in after if k in before)
                   if isinstance(after, dict) and isinstance(before, dict) else False)
        rec["refusal"] = ("executed_unconfirmed" if changed else
                          "command_silently_refused" if rec["executed"] else "execute_failed")
    return rec


def _stances(bus, cqi):
    raw = _ev(bus, _LUA_STANCE_STATE % {"cqi": cqi}, timeout=15.0)
    if raw in (None, "NO-FORCE", "NO-LIST"):
        return None
    out = {}
    for rec in str(raw).split("|"):
        p = rec.split("~")
        if len(p) >= 4:
            out[p[0]] = {"active": p[1] == "true", "can_activate": p[2] == "true",
                         "can_afford": p[3] == "true"}
    return out or None


def _stance_snapshot(bus, ctx, pick):
    st = _stances(bus, ctx["entity_id"])
    if st is None:
        return None
    active = next((k for k, v in st.items() if v["active"]), None)
    tgt = st.get(pick["key"]) or {}
    return {"active_stance": active, "target_in_list": pick["key"] in st,
            "can_activate": tgt.get("can_activate"), "can_afford": tgt.get("can_afford")}


_STANCE_STACK = "hud_campaign|BL_parent|land_stance_button_stack|clip_parent|stack_background"


def _legal_stances(bus):
    try:
        r = bus.send("find", _STANCE_STACK, timeout=12.0) or {}
    except Exception as e:
        sys.stderr.write("cco_actions: stance stack find -> %s\n" % repr(e)[:90])
        return set()
    out = set()
    for k in (r.get("child_ids") or []):
        if not k.startswith("button_") or k == "button_default":
            continue
        try:
            b = bus.send("find", "%s|%s" % (_STANCE_STACK, k), timeout=8.0) or {}
        except Exception:
            continue
        if (b.get("result") or {}).get("state") != "inactive":
            out.add(k[len("button_"):])
    return out


def _stance_gate_whitelist(bus, ctx, pick, before):
    wl = _legal_stances(bus)
    if not wl:
        return False, "stance_legality_unreadable"
    if pick["key"] not in wl:
        return False, "stance_not_in_legality_whitelist"
    return True, None


def _stance_gate_state(bus, ctx, pick, before):
    if not before.get("target_in_list"):
        return False, "stance_not_in_list"
    if before.get("active_stance") == pick["key"]:
        return False, "already_active"
    if not (before.get("can_activate") and before.get("can_afford")):
        return False, "can_activate_or_afford_false"
    return True, None


def _stance_execute(bus, ctx, pick, before):
    return _ev(bus, _LUA_STANCE_ACTIVATE % {"cqi": ctx["entity_id"], "key": pick["key"]}) == "called"


def _stance_confirm(bus, ctx, pick, before):
    st = _stances(bus, ctx["entity_id"]) or {}
    active = next((k for k, v in st.items() if v["active"]), None)
    return active == pick["key"], {"active_stance": active}


register("stance", {
    "layer": "cco", "signal": "is_active_flip",
    "snapshot": _stance_snapshot,
    "gates": [_stance_gate_whitelist, _stance_gate_state],
    "execute": _stance_execute, "confirm": _stance_confirm,
    "timeout_s": 5.0, "poll_s": 1.2,
})


def _build_cost(key):
    try:
        sys.path.insert(0, r"D:\tw_stack\advisor\reference")
        import features_db as _DB
        return _DB.building_features(key).get("create_cost")
    except Exception as e:
        sys.stderr.write("cco_actions: build cost lookup %r -> %s\n" % (key, repr(e)[:90]))
        return None


def _building_snapshot(bus, ctx, pick):
    t = _treasury(bus)
    if t is None:
        return None
    slot = (pick.get("params") or {}).get("slot_index")
    if slot is None:
        return None
    is_new = _ev(bus, _LUA_SLOT_STATE % {"region": ctx["entity_id"], "slot": int(slot)})
    cost = (pick.get("params") or {}).get("cost")
    if cost is None:
        cost = _build_cost(pick["key"])
        pick.setdefault("params", {})["cost"] = cost
    return {"treasury": t, "slot_is_building_new": is_new == "true", "cost": cost}


def _building_gate_slot(bus, ctx, pick, before):
    if before.get("slot_is_building_new"):
        return False, "slot_already_building"
    return True, None


def _building_execute(bus, ctx, pick, before):
    slot = int((pick.get("params") or {}).get("slot_index"))
    res = _ev(bus, _LUA_CONSTRUCT % {"region": ctx["entity_id"], "slot": slot,
                                     "key": pick["key"]}, timeout=25.0)
    if res != "called":
        sys.stderr.write("cco_actions: construct %s slot %s %r -> %s\n"
                         % (ctx["entity_id"], slot, pick["key"], res))
        return False
    return True


def _building_confirm(bus, ctx, pick, before):
    """True when the slot is queued with the requested building key."""
    slot = int((pick.get("params") or {}).get("slot_index"))
    t1 = _treasury(bus)
    queued = _ev(bus, _LUA_SLOT_QUEUED % {"region": ctx["entity_id"], "slot": slot})
    after = {"treasury": t1, "queued": queued,
             "spent": (None if (t1 is None or before.get("treasury") is None)
                       else round(before["treasury"] - t1, 2))}
    if queued is None or str(queued).startswith("NO-"):
        return False, dict(after, unreadable=True)
    return (str(queued) == str(pick["key"])), after


register("building", {
    "layer": "cco", "signal": "slot_queued_with_requested_building",
    "snapshot": _building_snapshot,
    "gates": [_building_gate_slot],
    "execute": _building_execute, "confirm": _building_confirm,
    "timeout_s": 6.0, "poll_s": 2.0, "spends_gold": True,
})


_LUA_TECH = (_G +
    "local f=cco('CcoCampaignFaction','%(fac)s'); local m=g(f,'TechnologyManagerContext');"
    "if not m then return 'NO-MGR' end local l=g(m,'TechnologyList');"
    "if type(l)~='table' then return 'NO-LIST' end ")


def _faction_cqi(bus):
    return _ev(bus, "return cm:get_local_faction(true):command_queue_index()", timeout=8.0)


def _researching(bus):
    return _ev(bus, "return tostring(cm:get_local_faction(true):is_currently_researching())", timeout=8.0)


def _current_tech(bus, fac):
    return _ev(bus, _G + "local f=cco('CcoCampaignFaction','%s'); local m=g(f,'TechnologyManagerContext');"
                         "local c=m and g(m,'CurrentResearchingTechnologyContext');"
                         "if c then return ts(g(c,'NodeKey')) end return 'none'" % fac)


def _research_snapshot(bus, ctx, pick):
    fac = _faction_cqi(bus)
    if fac is None:
        return None
    return {"faction": str(fac), "researching": _researching(bus), "current": _current_tech(bus, fac)}


def _research_gate(bus, ctx, pick, before):
    if before.get("researching") == "true":
        return False, "already_researching"
    return True, None


def _research_execute(bus, ctx, pick, before):
    res = _ev(bus, (_LUA_TECH % {"fac": before["faction"]}) +
              "for i=1,#l do local t=l[i]; if ts(g(t,'NodeKey'))=='%s' then "
              "pcall(function() t:Call('StartResearching') end); return 'called' end end return 'NOT-IN-LIST'"
              % pick["key"], timeout=25.0)
    if res != "called":
        sys.stderr.write("cco_actions: research %r -> %s\n" % (pick["key"], res))
        return False
    return True


def _research_confirm(bus, ctx, pick, before):
    """True when research is underway and the active tech changed."""
    cur = _current_tech(bus, before["faction"])
    researching = _researching(bus)
    started = (researching == "true"
               and (cur != before.get("current") or before.get("researching") != "true"))
    return started, {"researching": researching, "current": cur, "actual": cur,
                     "exact_match": cur == pick["key"]}


register("research", {
    "layer": "cco", "signal": "is_researching_and_current_tech",
    "snapshot": _research_snapshot, "gates": [_research_gate],
    "execute": _research_execute, "confirm": _research_confirm,
    "timeout_s": 6.0, "poll_s": 1.5,
})


_LUA_SKILLS = (_G + "local c=cco('CcoCampaignCharacter','%(cqi)s'); local l=g(c,'SkillList');"
                    "if type(l)~='table' then return 'NO-LIST' end ")


def _has_skill(bus, cqi, key):
    return _ev(bus, "local c=cm:get_character_by_cqi(%s); if c and not c:is_null_interface() "
                    "then return tostring(c:has_skill('%s')) end return 'no-char'" % (cqi, key), timeout=8.0)


def _skills_snapshot(bus, ctx, pick):
    cqi = ctx["entity_id"]
    return {
        "has_skill": _has_skill(bus, cqi, pick["key"]),
        "points": _ev(bus, _G + "return ts(g(cco('CcoCampaignCharacter','%s'),'SkillPointsAvailable'))" % cqi),
        "status": _ev(bus, (_LUA_SKILLS % {"cqi": cqi}) +
                      "for i=1,#l do if ts(g(l[i],'Key'))=='%s' then return ts(g(l[i],'Status')) end end "
                      "return 'NOT-IN-LIST'" % pick["key"], timeout=20.0),
    }


def _skills_gate(bus, ctx, pick, before):
    if before.get("has_skill") == "true":
        return False, "already_has_skill"
    try:
        if float(before.get("points") or 0) < 1:
            return False, "no_skill_points"
    except (TypeError, ValueError):
        return False, "skill_points_unreadable"
    if before.get("status") != "active":
        return False, "skill_status_%s" % before.get("status")
    return True, None


def _skills_execute(bus, ctx, pick, before):
    cqi = ctx["entity_id"]
    res = _ev(bus, (_LUA_SKILLS % {"cqi": cqi}) +
              "for i=1,#l do local s=l[i]; if ts(g(s,'Key'))=='%s' then "
              "pcall(function() s:Call('AddPoint') end); return 'called' end end return 'NOT-IN-LIST'"
              % pick["key"], timeout=25.0)
    if res != "called":
        sys.stderr.write("cco_actions: skills AddPoint %r -> %s\n" % (pick["key"], res))
        return False
    time.sleep(0.8)
    _ev(bus, _G + "local c=cco('CcoCampaignCharacter','%s'); "
                  "pcall(function() c:Call('CommitSkillChoices') end); return 'called'" % cqi)
    return True


def _skills_confirm(bus, ctx, pick, before):
    cqi = ctx["entity_id"]
    has = _has_skill(bus, cqi, pick["key"])
    uncommitted = _ev(bus, _G + "return ts(g(cco('CcoCampaignCharacter','%s'),'HasUncommitedSkills'))" % cqi)
    return (has == "true" and uncommitted == "false"), {"has_skill": has, "uncommitted": uncommitted}


register("skills", {
    "layer": "cco", "signal": "has_skill_flip_and_committed",
    "snapshot": _skills_snapshot, "gates": [_skills_gate],
    "execute": _skills_execute, "confirm": _skills_confirm,
    "timeout_s": 6.0, "poll_s": 1.2,
})


_LUA_POOL = (_G + "local f=cco('CcoCampaignFaction','%(fac)s'); local l=g(f,'AncillaryList');"
                  "if type(l)~='table' then return 'NO-LIST' end ")


def _equipped_names(bus, cqi):
    return _ev(bus, _G + "local c=cco('CcoCampaignCharacter','%s'); local l=g(c,'AncillaryList');"
                         "if type(l)~='table' then return 'NO-LIST' end local o={} "
                         "for i=1,#l do o[#o+1]=ts(g(l[i],'Name')) end "
                         "return #l..'|'..table.concat(o,',')" % cqi, timeout=20.0)


def _select_character(bus, cqi):
    _ev(bus, _G + "local c=cco('CcoCampaignCharacter','%s'); pcall(function() c:Call('Select') end); "
                  "return 'ok'" % cqi)
    time.sleep(1.0)


def _items_snapshot(bus, ctx, pick):
    cqi = ctx["entity_id"]
    fac = _faction_cqi(bus)
    if fac is None:
        return None
    _select_character(bus, cqi)
    idx = pick.get("params", {}).get("pool_index")
    can = None
    if idx is not None:
        can = _ev(bus, (_LUA_POOL % {"fac": fac}) +
                  "local a=l[%d]; if not a then return 'NO-ITEM' end "
                  "return ts(g(a,'CanCharacterEquip(SelectedCharacter())'))" % int(idx), timeout=20.0)
    return {"faction": str(fac), "equipped": _equipped_names(bus, cqi), "can_equip": can}


def _items_gate(bus, ctx, pick, before):
    if pick.get("params", {}).get("pool_index") is None:
        return False, "no_pool_index"
    if before.get("can_equip") != "true":
        return False, "can_character_equip_%s" % before.get("can_equip")
    return True, None


def _items_execute(bus, ctx, pick, before):
    idx = int(pick["params"]["pool_index"])
    _select_character(bus, ctx["entity_id"])
    res = _ev(bus, (_LUA_POOL % {"fac": before["faction"]}) +
              "local a=l[%d]; if not a then return 'NO-ITEM' end "
              "pcall(function() a:Call('EquipToCharacter(SelectedCharacter(), NullContext())') end); "
              "return 'called'" % idx, timeout=25.0)
    return res == "called"


def _items_confirm(bus, ctx, pick, before):
    now = _equipped_names(bus, ctx["entity_id"])
    def n(v):
        try:
            return int(str(v).split("|", 1)[0])
        except (TypeError, ValueError):
            return -1
    return (n(now) > n(before.get("equipped"))), {"equipped": now}


register("items", {
    "layer": "cco", "signal": "equipped_count_increase",
    "snapshot": _items_snapshot, "gates": [_items_gate],
    "execute": _items_execute, "confirm": _items_confirm,
    "timeout_s": 6.0, "poll_s": 1.2,
})


def _unequip_snapshot(bus, ctx, pick):
    return {"equipped": _equipped_names(bus, ctx["entity_id"])}


def _unequip_execute(bus, ctx, pick, before):
    idx = int(pick.get("params", {}).get("equipped_index", 1))
    res = _ev(bus, _G + "local c=cco('CcoCampaignCharacter','%s'); local l=g(c,'AncillaryList');"
                        "local a=l[%d]; if not a then return 'NO-ITEM' end "
                        "pcall(function() a:Call('RemoveAncillary') end); return 'called'"
              % (ctx["entity_id"], idx), timeout=20.0)
    return res == "called"


def _unequip_confirm(bus, ctx, pick, before):
    now = _equipped_names(bus, ctx["entity_id"])
    def n(v):
        try:
            return int(str(v).split("|", 1)[0])
        except (TypeError, ValueError):
            return -1
    return (n(now) < n(before.get("equipped"))), {"equipped": now}


register("item_unequip", {
    "layer": "cco", "signal": "equipped_count_decrease",
    "snapshot": _unequip_snapshot, "execute": _unequip_execute, "confirm": _unequip_confirm,
    "timeout_s": 5.0, "poll_s": 1.2,
})


_LUA_RITES = (_G + "local f=cco('CcoCampaignFaction','%(fac)s'); local l=g(f,'AvailableRitualList');"
                   "if type(l)~='table' then return 'NO-LIST' end ")


def _rite_flags(bus, fac, idx):
    return _ev(bus, (_LUA_RITES % {"fac": fac}) +
               "local r=l[%d]; if not r then return 'NO-RITE' end "
               "return ts(g(r,'CanPerformRitual'))..'/'..ts(g(r,'IsComplete'))" % int(idx), timeout=20.0)


def _rites_snapshot(bus, ctx, pick):
    fac = _faction_cqi(bus)
    idx = pick.get("params", {}).get("rite_index")
    if fac is None or idx is None:
        return None
    return {"faction": str(fac), "flags": _rite_flags(bus, fac, idx)}


def _rites_gate(bus, ctx, pick, before):
    if not str(before.get("flags", "")).startswith("true/"):
        return False, "cannot_perform_%s" % before.get("flags")
    return True, None


def _rites_execute(bus, ctx, pick, before):
    idx = int(pick["params"]["rite_index"])
    res = _ev(bus, (_LUA_RITES % {"fac": before["faction"]}) +
              "local r=l[%d]; if not r then return 'NO-RITE' end "
              "pcall(function() r:Call('Perform') end); return 'called'" % idx, timeout=25.0)
    return res == "called"


def _rites_confirm(bus, ctx, pick, before):
    flags = _rite_flags(bus, before["faction"], pick["params"]["rite_index"])
    return (flags != before.get("flags") and not str(flags).startswith("true/")), {"flags": flags}


register("rites", {
    "layer": "cco", "signal": "can_perform_false_and_complete",
    "snapshot": _rites_snapshot, "gates": [_rites_gate],
    "execute": _rites_execute, "confirm": _rites_confirm,
    "timeout_s": 8.0, "poll_s": 1.5,
})


_LUA_SLOT_OP = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(region)s') "
    "if not s then return 'NO-CTX' end local slots=g(s,'BuildingSlotList') "
    "if type(slots)~='table' then return 'NO-SLOTLIST' end "
    "for i,x in ipairs(slots) do if g(x,'Index')==%(slot)d then "
    "  if g(x,'%(guard)s')~=true then return 'REFUSED-'..ts(g(x,'%(guard)s')) end "
    "  pcall(function() x:Call('%(cmd)s') end) return 'called' end end return 'NO-SLOT'")

_LUA_SLOT_FLAGS = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(region)s') "
    "if not s then return 'NO-CTX' end local slots=g(s,'BuildingSlotList') "
    "if type(slots)~='table' then return 'NO-SLOTLIST' end "
    "for i,x in ipairs(slots) do if g(x,'Index')==%(slot)d then "
    "  local ci=g(x,'ConstructionItemContext') "
    "  return ts(g(x,'IsDamaged'))..'~'..ts(g(x,'IsRepairing'))..'~'..ts(ci~=nil)"
    "..'~'..ts(g(x,'IsEmpty')) end end return 'NO-SLOT'")


def _slot_flags(bus, region, slot):
    raw = _ev(bus, _LUA_SLOT_FLAGS % {"region": region, "slot": int(slot)}, timeout=8.0)
    p = str(raw or "").split("~")
    if len(p) != 4:
        return None
    return {"damaged": p[0] == "true", "repairing": p[1] == "true",
            "queued": p[2] == "true", "empty": p[3] == "true"}


def _slot_snapshot(bus, ctx, pick):
    p = pick.get("params") or {}
    region, slot = p.get("region"), p.get("slot_index")
    if region is None or slot is None:
        return None
    f = _slot_flags(bus, region, slot)
    if f is None:
        return None
    f.update(region=region, slot=slot, treasury=_treasury(bus))
    return f


def _slot_exec(cmd, guard):
    def run(bus, ctx, pick, before):
        res = _ev(bus, _LUA_SLOT_OP % {"region": before["region"], "slot": int(before["slot"]),
                                       "cmd": cmd, "guard": guard}, timeout=8.0)
        if res != "called":
            sys.stderr.write("cco_actions: %s slot %s/%s -> %s\n"
                             % (cmd, before["region"], before["slot"], res))
        return res == "called"
    return run


def _slot_confirm(field, want):
    """Confirm on the engine's own flag flipping to `want`."""
    def run(bus, ctx, pick, before):
        now = _slot_flags(bus, before["region"], before["slot"])
        after = dict(now or {}, treasury=_treasury(bus))
        if now is None:
            return False, after
        return (now.get(field) is want and before.get(field) is not want), after
    return run


register("building_repair", {
    "layer": "cco", "signal": "is_repairing_flip",
    "snapshot": _slot_snapshot,
    "execute": _slot_exec("Repair", "CanRepair"),
    "confirm": _slot_confirm("repairing", True),
    "timeout_s": 6.0, "poll_s": 1.0, "spends_gold": True,
})

register("building_cancel", {
    "layer": "cco", "signal": "construction_item_cleared",
    "snapshot": _slot_snapshot,
    "execute": _slot_exec("CancelConstruction", "CanBeCancelled"),
    "confirm": _slot_confirm("queued", False),
    "timeout_s": 6.0, "poll_s": 1.0,
})

register("building_dismantle", {
    "layer": "cco", "signal": "slot_empty_flip",
    "snapshot": _slot_snapshot,
    "execute": _slot_exec("Dismantle", "CanDismantle"),
    "confirm": _slot_confirm("empty", True),
    "timeout_s": 6.0, "poll_s": 1.0,
})


_MARKER_ANCHORS = ("icon_harmony", "mission_icon", "status_bar", "dy_mp_player_name")

_HERO_PANEL = "agent_options"
_HERO_NAME_NODE = "dy_method_name"

_LUA_HERO_SETT_STATE = (_G +
    "local c=cm:get_character_by_cqi(%(cqi)s) if not c then return 'NO-CHAR' end "
    "local s=cco('CcoCampaignSettlement','settlement:%(tgt)s') "
    "return ts(c:performed_action_this_turn())..'~'..ts(c:logical_position_x())"
    "..'~'..ts(c:logical_position_y())..'~'..ts(g(s,'IsAbandoned'))..'~'..ts(g(s,'IsShrouded'))")

_LUA_HERO_CHAR_STATE = (_G +
    "local c=cm:get_character_by_cqi(%(cqi)s) if not c then return 'NO-CHAR' end "
    "local t=cm:get_character_by_cqi(%(tgt)s) "
    "return ts(c:performed_action_this_turn())..'~'..ts(c:logical_position_x())"
    "..'~'..ts(c:logical_position_y())..'~'..ts(t~=nil)..'~'..ts(t and t:is_null_interface()==false)")

_LUA_SETT_DISPLAY = ("local r=cm:get_region('%(tgt)s') if not r then return 'NO-REGION' end "
                     "local s=r:settlement() if not s then return 'NO-SETT' end "
                     "return tostring(s:display_position_x())..'~'..tostring(s:display_position_y())")

_LUA_CHAR_DISPLAY = ("local c=cm:get_character_by_cqi(%(tgt)s) if not c then return 'NO-CHAR' end "
                     "return tostring(c:display_position_x())..'~'..tostring(c:display_position_y())")

_LUA_CAM = ("local a,b,c,d,e = cm:get_camera_position() "
            "return tostring(a)..'~'..tostring(b)..'~'..tostring(c)..'~'..tostring(d)..'~'..tostring(e)")



def _collect_mod():
    """The recorder's collect module -- the single source of the target catalogue and reach check."""
    sys.path.insert(0, r"D:\tw_stack\decisions")
    import collect as _C
    return _C


def _hero_action_method_name(action):
    """Localised method label for a catalogue hero action, via the reference DB."""
    spec = _collect_mod().HERO_ACTIONS.get(action)
    if not spec:
        return None
    sys.path.insert(0, r"D:\tw_stack\advisor\reference")
    import features_db as _DB
    for key in _DB.agent_action_keys(spec["loc_suffix"]):
        name = _DB.agent_action_label(key)
        if name:
            return name
    return None


def _parse_hero_state(raw):
    p = str(raw or "").split("~")
    if len(p) < 5:
        return None
    return {"acted": p[0] == "true", "x": _numf(p[1]), "y": _numf(p[2]),
            "is_abandoned": p[3], "is_shrouded": p[4]}


def _numf(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hero_target(pick):
    """(kind, id) of the offer's target: ('character', cqi) or ('settlement', region)."""
    p = pick.get("params") or {}
    if p.get("target_kind") == "character" and p.get("target_cqi") is not None:
        return "character", str(p["target_cqi"])
    if p.get("region"):
        return "settlement", str(p["region"])
    return None, None


def _hero_action_snapshot(bus, ctx, pick):
    kind, tid = _hero_target(pick)
    if tid is None:
        return None
    lua = _LUA_HERO_CHAR_STATE if kind == "character" else _LUA_HERO_SETT_STATE
    st = _parse_hero_state(_ev(bus, lua % {"cqi": ctx["entity_id"], "tgt": tid}, timeout=8.0))
    if st is None:
        return None
    st["target_kind"], st["target_id"] = kind, tid
    st["region"] = tid if kind == "settlement" else None
    st["stream_off"] = bus._out_size()
    return st


def _hero_action_gate_target(bus, ctx, pick, before):
    """Target still valid. Only the ruins-seeking action demands the looks-like-ruins predicate."""
    if before.get("target_kind") == "character":
        if before.get("is_abandoned") != "true":
            return False, "target_character_gone"
        return True, None
    if before.get("is_shrouded") != "false":
        return False, "target_shrouded_%s" % before.get("is_shrouded")
    if (pick.get("params") or {}).get("action") == "scout_ruins":
        if before.get("is_abandoned") != "true":
            return False, "target_not_ruins_%s" % before.get("is_abandoned")
    return True, None


def _hero_action_gate_reach(bus, ctx, pick, before):
    """Movement range via the recorder's own AP-aware reach sweep -- no second implementation."""
    is_char = before.get("target_kind") == "character"
    tid = before["target_id"]
    reach_c, reach_s = _collect_mod()._reach(
        bus, ctx["entity_id"], [tid] if is_char else [], [] if is_char else [tid])
    if not (reach_c if is_char else reach_s).get(str(tid)):
        return False, "cannot_reach"
    return True, None


def _target_already_on_screen(bus, kind, tid):
    """True when the target already has an overlay marker -- no camera move needed.

    Moving the camera is not free: set_camera_position onto an inaccessible point has been
    observed to break the campaign HUD (ui_hiding=true, hud_campaign gone), so it is only worth
    doing when the target genuinely is not visible."""
    want = ("CcoCampaignCharacter:%s" % tid if kind == "character"
            else "label_settlement:%s" % tid)
    tr = bus.send("tree", "3d_ui_parent 12 6000", timeout=8.0) or {}
    for n in (tr.get("nodes") or []):
        hit = str(n.get("context")) if kind == "character" else str(n.get("id"))
        if hit == want and n.get("x") is not None:
            return True
    return False


def _centre_camera_on_target(bus, kind, tid):
    """Bring the target on screen, preserving the current zoom/bearing/height.

    The camera x,y is NOT the ground point that lands at screen centre, so this only guarantees
    the target is visible; the click point comes from the target's own overlay rect."""
    lua = _LUA_CHAR_DISPLAY if kind == "character" else _LUA_SETT_DISPLAY
    disp = str(_ev(bus, lua % {"tgt": tid}, timeout=6.0) or "")
    cam = str(_ev(bus, _LUA_CAM, timeout=6.0) or "")
    if "~" not in disp or cam.count("~") < 4:
        sys.stderr.write("cco_actions: no display position for %s %s (%r)\n" % (kind, tid, disp))
        return False
    tx, ty = [_numf(v) for v in disp.split("~")[:2]]
    d, b, h = [_numf(v) for v in cam.split("~")[2:5]]
    if None in (tx, ty, d, b, h):
        return False
    _ev(bus, "cm:set_camera_position(%f, %f, %f, %f, %f) return 'called'" % (tx, ty, d, b, h),
        timeout=15.0)
    now = str(_ev(bus, _LUA_CAM, timeout=6.0) or "")
    if now.count("~") < 4:
        return False
    cx, cy = [_numf(v) for v in now.split("~")[:2]]
    if cx is None or cy is None or abs(cx - tx) > 0.75 or abs(cy - ty) > 0.75:
        sys.stderr.write("cco_actions: camera did not reach %s %s (asked %.2f,%.2f got %s,%s)\n"
                         % (kind, tid, tx, ty, cx, cy))
        return False
    return True


def _hero_target_point(bus, kind, tid):
    """Screen pixel to right-click for this target, read from the UI. None when unresolvable.

    The banner is taken from the overlay, never estimated. A 20-level zoom sweep over a hero and
    two lords showed the vertical offset is not a usable function: it follows K/cam_dist with a
    per-character K (-4159 vs -5583 for two lords, ~2 icon heights apart when pooled), and below
    d~7 the engine clamps camera height and the offset reverses entirely. The overlay rect is
    exact at every zoom and distinguishes hero banners (few nodes) from lord banners (the full
    army strip) for free. Reading it costs ~2ms over the bus round-trip.

    No zoom is ever changed here; when the marker is absent this refuses rather than adjusting
    the camera."""
    import nav
    if kind == "character":
        tr = bus.send("tree", "3d_ui_parent 12 6000", timeout=8.0) or {}
        want = "CcoCampaignCharacter:%s" % tid
        hits = [n for n in (tr.get("nodes") or [])
                if str(n.get("context")) == want
                and n.get("x") is not None and n.get("y") is not None]
        if not hits:
            sys.stderr.write("cco_actions: character %s has no overlay marker -- refusing\n" % tid)
            return None
        byid = {}
        for n in hits:
            byid.setdefault(str(n.get("id")), []).append(n)
        node = None
        for want in _MARKER_ANCHORS:
            if want in byid:
                node = byid[want][0]
                break
        if node is None:
            node = byid[sorted(byid)[0]][0]
        return nav.ui_to_screen(float(node["x"]) + (node.get("w") or 0) / 2.0,
                                float(node["y"]) + (node.get("h") or 0) / 2.0)
    tr = bus.send("tree", "3d_ui_parent 12 6000", timeout=8.0) or {}
    nodes = tr.get("nodes") or []
    want = "label_settlement:%s" % tid
    hits = [n for n in nodes if str(n.get("id")) == want
            and n.get("x") is not None and n.get("y") is not None]
    if hits:
        n = hits[0]
        return nav.ui_to_screen(float(n["x"]) + (n.get("w") or 0) / 2.0,
                                float(n["y"]) + (n.get("h") or 0) / 2.0)
    sys.stderr.write("cco_actions: settlement %s has no on-screen label (%d overlay nodes)\n"
                     % (tid, len(nodes)))
    return None


def _hero_action_button(bus, name, attribute=None):
    """Resolve the method button by its displayed name -- never by index.

    Labels are not unique (increase_mobility and enhance_mobility are both 'Increase Mobility'),
    so when a name matches more than one row the approach attribute breaks the tie; an ambiguity
    that survives that is refused loudly rather than guessed."""
    tr = bus.send("tree", "%s 25 8000" % _HERO_PANEL, timeout=8.0) or {}
    nodes = tr.get("nodes") or []
    hits = []
    for n in nodes:
        if n.get("id") == _HERO_NAME_NODE and str(n.get("text")).strip() == name:
            hits.append(str(n.get("path")).rsplit("|" + _HERO_NAME_NODE, 1)[0])
    if len(hits) > 1 and attribute:
        narrowed = []
        for btn in hits:
            tip = next((str(m.get("tooltip") or "") for m in nodes
                        if m.get("id") == "icon_method" and str(m.get("path")).startswith(btn)), "")
            if str(attribute).lower() in tip.lower():
                narrowed.append(btn)
        if narrowed:
            hits = narrowed
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        sys.stderr.write("cco_actions: hero_action %r ambiguous (%d rows, attribute=%s) -- refusing\n"
                         % (name, len(hits), attribute))
        return None
    seen = [str(n.get("text")) for n in nodes if n.get("id") == _HERO_NAME_NODE]
    sys.stderr.write("cco_actions: hero_action %r not offered; panel shows %s\n" % (name, seen))
    return None


def _hero_action_execute(bus, ctx, pick, before):
    import click_actions
    import nav
    action = (pick.get("params") or {}).get("action")
    kind, tid = before["target_kind"], before["target_id"]
    where = "hero=%s action=%s target=%s:%s" % (ctx.get("entity_id"), action, kind, tid)

    def fail(step, detail=""):
        sys.stderr.write("cco_actions: hero_action FAILED at %s -- %s%s\n"
                         % (step, where, (" -- " + detail) if detail else ""))
        return False

    name = _hero_action_method_name(action)
    if not name:
        return fail("method_name", "no method name maps to action %r" % action)
    ok, why = click_actions.prepare(bus, "lord", ctx["entity_id"], expect_root="units_panel")
    if not ok:
        return fail("prepare", str(why))
    if not _centre_camera_on_target(bus, kind, tid):
        return fail("centre_camera", "shrouded=%s abandoned=%s x=%s y=%s"
                    % (before.get("is_shrouded"), before.get("is_abandoned"),
                       before.get("x"), before.get("y")))
    nav.close_popups(bus)
    pt = _hero_target_point(bus, kind, tid)
    if pt is None:
        return fail("target_point", "no screen point for the target after centring")
    off = bus._out_size()
    nav.mouse("rclick", *pt)
    row, _ = bus.wait_row(("panel",), timeout=6.0, offset=off,
                          pred=lambda r: bool(r.get("opened")) and r.get("name") == _HERO_PANEL)
    if row is None:
        return fail("open_panel", "right-click at %s did not open %s" % (pt, _HERO_PANEL))
    btn = _hero_action_button(bus, name, (pick.get("params") or {}).get("attribute"))
    if btn is None:
        return fail("find_button", "no button for method %r attribute %r in %s"
                    % (name, (pick.get("params") or {}).get("attribute"), _HERO_PANEL))
    r = bus.send("click", btn, timeout=8.0) or {}
    if not r.get("changed"):
        return fail("click", "clicked %s but state did not change (found=%s clicked=%s)"
                    % (btn, r.get("found"), r.get("clicked")))
    return True


def _hero_action_confirm(bus, ctx, pick, before):
    """The engine's own agent-action event is the signal; state deltas ride along as evidence.

    wait_row blocks until the row lands, so this returns the moment the engine reports the
    action -- no sleep granularity, and no state read at all on the miss path."""
    row, _ = bus.wait_row(("agent_action",), timeout=0.75, offset=before["stream_off"],
                          poll=0.05,
                          pred=lambda r: str(r.get("cqi")) == str(ctx["entity_id"]))
    if row is None:
        return False, {}
    lua = (_LUA_HERO_CHAR_STATE if before.get("target_kind") == "character"
           else _LUA_HERO_SETT_STATE)
    st = _parse_hero_state(_ev(bus, lua % {"cqi": ctx["entity_id"], "tgt": before["target_id"]},
                               timeout=20.0))
    after = dict(st or {})
    after["event"] = row
    after["success"] = row.get("success")
    if st:
        after["moved"] = st.get("x") != before.get("x") or st.get("y") != before.get("y")
        after["acted_flip"] = bool(not before.get("acted") and st.get("acted"))
    return True, after


register("hero_action", {
    "layer": "click", "signal": "agent_action_event",
    "snapshot": _hero_action_snapshot,
    "gates": [_hero_action_gate_target, _hero_action_gate_reach],
    "execute": _hero_action_execute, "confirm": _hero_action_confirm,
    "timeout_s": 8.0, "poll_s": 0.05,
})


def _endturn_snapshot(bus, ctx, pick):
    t = _ev(bus, "return cm:model():turn_number()", timeout=8.0)
    return None if t is None else {"turn": t}


def _endturn_execute(bus, ctx, pick, before):
    """Clear the screen, then issue EndTurn. Failure to clear is not fatal."""
    try:
        import click_actions
        click_actions.clear_screen(bus)
    except Exception as e:
        sys.stderr.write("cco_actions: end_turn clear_screen -> %s\n" % repr(e)[:100])
    return _ev(bus, _G + "local r=cco('CcoCampaignRoot',''); "
                         "pcall(function() r:Call('EndTurn') end); return 'called'") == "called"


_LUA_OUR_TURN = ("local me=cm:get_local_faction_name(true) "
                 "local ok,l=pcall(function() return cm:model():world():whose_turn_is_it() end) "
                 "if not ok or not l then return 'unknown' end "
                 "local ok2,n=pcall(function() return l:num_items() end) "
                 "if not ok2 then return 'unknown' end "
                 "for i=0,n-1 do if l:item_at(i):name()==me then return 'true' end end return 'false'")


def is_our_turn(bus):
    """True / False / None when unreadable."""
    v = _ev(bus, _LUA_OUR_TURN, timeout=10.0)
    return None if v not in ("true", "false") else (v == "true")


def _endturn_confirm(bus, ctx, pick, before):
    """Confirm the end-turn order landed, clearing interrupts between polls."""
    def _took_effect():
        """True when the turn number moved or the turn is no longer ours."""
        tt = _ev(bus, "return cm:model():turn_number()", timeout=8.0)
        try:
            moved = (tt is not None and float(tt) > float(before["turn"]))
        except (TypeError, ValueError):
            moved = False
        ours = is_our_turn(bus)
        return (moved or ours is False), tt

    def _back_to_us():
        """True when the turn advanced AND the turn is ours again."""
        tt = _ev(bus, "return cm:model():turn_number()", timeout=8.0)
        try:
            moved = (tt is not None and float(tt) > float(before["turn"]))
        except (TypeError, ValueError):
            moved = False
        return (moved and is_our_turn(bus) is True), tt

    ok, t = _took_effect()
    steps = []
    if not ok:
        try:
            import interrupts
            steps = interrupts.resolve(bus)
            if steps:
                ok, t = _took_effect()
        except Exception as e:
            sys.stderr.write("cco_actions: end_turn interrupt sweep -> %s\n" % repr(e)[:120])
    return ok, {"turn": t, "our_turn": is_our_turn(bus), "interrupts": steps}


register("end_turn", {
    "layer": "cco", "signal": "turn_number_increment",
    "snapshot": _endturn_snapshot, "execute": _endturn_execute, "confirm": _endturn_confirm,
    "timeout_s": 45.0, "poll_s": 3.0, "retryable": False,
})


register("noop", {"layer": "cco", "signal": "none",
                  "snapshot": lambda bus, ctx, pick: {},
                  "execute": lambda bus, ctx, pick, before: True,
                  "confirm": lambda bus, ctx, pick, before: (True, {})})


if __name__ == "__main__":
    from bus import Bus
    b = Bus()
    cqi = _ev(b, "local f=cm:get_local_faction(true); return f:faction_leader():command_queue_index()")
    ctx = {"context_kind": "lord", "entity_id": str(int(cqi)),
           "stance_whitelist": {"MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH",
                                "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT"}}
    r1 = execute_confirmed(b, ctx, {"action_type": "stance",
                                    "key": "MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH"})
    print("MARCH:", json.dumps({k: r1[k] for k in ("executed", "confirmed", "counted", "refusal")}))
    r2 = execute_confirmed(b, ctx, {"action_type": "stance",
                                    "key": "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT"})
    print("DEFAULT:", json.dumps({k: r2[k] for k in ("executed", "confirmed", "counted", "refusal")}))
    r3 = execute_confirmed(b, ctx, {"action_type": "stance",
                                    "key": "MILITARY_FORCE_ACTIVE_STANCE_TYPE_TUNNELING"})
    print("TUNNELING (must refuse):", json.dumps({k: r3[k] for k in ("counted", "refusal")}))
