from __future__ import annotations

import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)

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
    spec.setdefault("poll_s", 0.25)
    spec.setdefault("retryable", True)
    spec.setdefault("spends_gold", False)
    spec.setdefault("prechecks", [])
    REGISTRY[action_type] = spec
    return spec


class _Tee:

    def __init__(self, stream):
        self.stream = stream
        self.parts = []

    def write(self, s):
        self.parts.append(s)
        return self.stream.write(s)

    def flush(self):
        self.stream.flush()

    def text(self):
        return "".join(self.parts)


def execute_confirmed(bus, ctx, pick):
    tee = _Tee(sys.stderr)
    prev = sys.stderr
    sys.stderr = tee
    try:
        rec = _execute_confirmed(bus, ctx, pick)
    finally:
        sys.stderr = prev
    rec["stderr"] = tee.text() or None
    return rec


def _execute_confirmed(bus, ctx, pick):
    atype = pick.get("action_type")
    rec = {"ts": time.time(), "context_kind": ctx.get("context_kind"),
           "entity_id": str(ctx.get("entity_id")), "action_type": atype,
           "key": pick.get("key"), "params": pick.get("params") or {},
           "policy": pick.get("policy"), "layer": None, "attempt": pick.get("attempt", 1),
           "executed": False, "confirmed": False, "counted": False, "refusal": None,
           "confirm": None, "prechecks": {"passed": True, "failed_precheck": None}}
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
            rec["prechecks"] = {"passed": False, "failed_precheck": "treasury_floor"}
            rec["refusal"] = "pre_check_refused"
            rec["timing"] = dict(_t, gates_ms=int((time.time() - _tg) * 1000),
                                 total_ms=int((time.time() - _t_start) * 1000))
            return rec
    for precheck in spec["prechecks"]:
        ok, reason = precheck(bus, ctx, pick, before)
        if not ok:
            rec["prechecks"] = {"passed": False, "failed_precheck": reason}
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
    settle = float(spec.get("confirm_settle_s") or 0.0)
    if settle > 0.0:
        common.wait("confirm_settle", settle, atype)
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
    common.waitlog("action_confirm_poll", time.time() - _tc, bool(confirmed),
                   "%s polls=%d" % (atype, polls))
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


def _stance_execute(bus, ctx, pick, before):
    return _ev(bus, _LUA_STANCE_ACTIVATE % {"cqi": ctx["entity_id"], "key": pick["key"]}) == "called"


def _stance_confirm(bus, ctx, pick, before):
    st = _stances(bus, ctx["entity_id"]) or {}
    active = next((k for k, v in st.items() if v["active"]), None)
    return active == pick["key"], {"active_stance": active}


register("stance", {
    "layer": "cco", "signal": "is_active_flip",
    "snapshot": _stance_snapshot,
    "prechecks": [],
    "execute": _stance_execute, "confirm": _stance_confirm,
    "timeout_s": 5.0, "poll_s": 0.25,
})


def _build_cost(key):
    try:
        sys.path.insert(0, common.REFERENCE)
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


def _building_execute(bus, ctx, pick, before):
    slot = int((pick.get("params") or {}).get("slot_index"))
    res = _ev(bus, _LUA_CONSTRUCT % {"region": ctx["entity_id"], "slot": slot,
                                     "key": pick["key"]}, timeout=25.0)
    if res != "called":
        sys.stderr.write("cco_actions: construct %s slot %s %r -> %s\n"
                         % (ctx["entity_id"], slot, pick["key"], res))
        return False
    return True


_LUA_HORDE_CONSTRUCT = (_G +
    "local x=cco('CcoCampaignBuildingSlot','%(slot)s');"
    "if not x then return 'NO-CTX' end "
    "local p=g(x,'PossibleUpgradeWithoutConversionsList');"
    "if type(p)~='table' then return 'NO-POSSIBLES' end "
    "for j=0,#p-1 do "
    "  if ts(g(x,'PossibleUpgradeWithoutConversionsList['..j..'].Key'))=='%(key)s' then "
    "    if g(x,'BuildingRequirementsMet(PossibleUpgradeWithoutConversionsList['..j..'])')~=true then "
    "      return 'REQ-NOT-MET' end "
    "    pcall(function() x:Call('Construct(PossibleUpgradeWithoutConversionsList['..j..'])') end);"
    "    return 'called' end end return 'KEY-NOT-IN-SLOT'")


_LUA_HORDE_SLOT_STATE = (_G +
    "local x=cco('CcoCampaignBuildingSlot','%(slot)s');"
    "if not x then return 'NO-CTX' end "
    "local ci=g(x,'ConstructionItemContext');"
    "local cl=ci and g(ci,'BuildingLevelRecordContext');"
    "return ts(g(x,'IsEmpty'))..'|'..ts(ci~=nil)..'|'..ts(cl and g(cl,'Key'))")


def _horde_parts(pick):
    p = pick.get("params") or {}
    slot = p.get("slot_id")
    key = p.get("building_key")
    if not slot or not key:
        raw = str(pick.get("key") or "")
        slot, _, key = raw.partition("@")
    return slot, key


def _horde_build_snapshot(bus, ctx, pick):
    slot, _key = _horde_parts(pick)
    return {"slot": slot, "state": _ev(bus, _LUA_HORDE_SLOT_STATE % {"slot": slot}, timeout=12.0),
            "treasury": _treasury(bus)}


def _horde_build_execute(bus, ctx, pick, before):
    slot, key = _horde_parts(pick)
    res = _ev(bus, _LUA_HORDE_CONSTRUCT % {"slot": slot, "key": key}, timeout=25.0)
    if res != "called":
        sys.stderr.write("cco_actions: horde construct %s %r -> %s\n" % (slot, key, res))
        return False
    return True


def _horde_build_confirm(bus, ctx, pick, before):
    slot, key = _horde_parts(pick)
    st = str(_ev(bus, _LUA_HORDE_SLOT_STATE % {"slot": slot}, timeout=12.0) or "")
    parts = st.split("|")
    after = {"slot": slot, "state": st, "treasury": _treasury(bus)}
    if len(parts) != 3:
        return False, dict(after, unreadable=True)
    return (parts[1] == "true" and parts[2] == key), after


def _horde_build_doomed(bus, ctx, pick, before, after):
    parts = str((after or {}).get("state") or "").split("|")
    if len(parts) != 3 or parts[1] == "true":
        before["doom_streak"] = 0
        return None
    before["doom_streak"] = before.get("doom_streak", 0) + 1
    if before["doom_streak"] < 2:
        return None
    return ("slot %s still not under construction after %d polls -- the order never queued"
            % (parts[0], before["doom_streak"]))


register("horde_building", {
    "layer": "cco", "signal": "force_slot_construction_queued",
    "snapshot": _horde_build_snapshot, "prechecks": [],
    "execute": _horde_build_execute, "confirm": _horde_build_confirm,
    "doomed": _horde_build_doomed,
    "timeout_s": 10.0, "poll_s": 1.5, "spends_gold": True,
})


def _building_confirm(bus, ctx, pick, before):
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
    "prechecks": [],
    "execute": _building_execute, "confirm": _building_confirm,
    "timeout_s": 6.0, "poll_s": 0.25, "spends_gold": True,
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
    cur = _current_tech(bus, before["faction"])
    researching = _researching(bus)
    started = (researching == "true"
               and (cur != before.get("current") or before.get("researching") != "true"))
    return started, {"researching": researching, "current": cur, "actual": cur,
                     "exact_match": cur == pick["key"]}


register("research", {
    "layer": "cco", "signal": "is_researching_and_current_tech",
    "snapshot": _research_snapshot, "prechecks": [],
    "execute": _research_execute, "confirm": _research_confirm,
    "timeout_s": 6.0, "poll_s": 0.25,
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


def _skills_execute(bus, ctx, pick, before):
    cqi = ctx["entity_id"]
    res = _ev(bus, (_LUA_SKILLS % {"cqi": cqi}) +
              "for i=1,#l do local s=l[i]; if ts(g(s,'Key'))=='%s' then "
              "pcall(function() s:Call('AddPoint') end); return 'called' end end return 'NOT-IN-LIST'"
              % pick["key"], timeout=25.0)
    if res != "called":
        sys.stderr.write("cco_actions: skills AddPoint %r -> %s\n" % (pick["key"], res))
        return False
    common.wait("skill_commit_pacing", 0.8, pick["key"])
    _ev(bus, _G + "local c=cco('CcoCampaignCharacter','%s'); "
                  "pcall(function() c:Call('CommitSkillChoices') end); return 'called'" % cqi)
    return True


def _spent_a_point(before_points, after_points):
    try:
        return float(after_points) < float(before_points)
    except (TypeError, ValueError):
        return False


def _skills_confirm(bus, ctx, pick, before):
    cqi = ctx["entity_id"]
    has = _has_skill(bus, cqi, pick["key"])
    points = _ev(bus, _G + "return ts(g(cco('CcoCampaignCharacter','%s'),'SkillPointsAvailable'))" % cqi)
    uncommitted = _ev(bus, _G + "return ts(g(cco('CcoCampaignCharacter','%s'),'HasUncommitedSkills'))" % cqi)
    spent = _spent_a_point(before.get("points"), points)
    return (has == "true" and uncommitted == "false" and spent), {
        "has_skill": has, "uncommitted": uncommitted, "points": points,
        "points_before": before.get("points"), "spent": spent}


register("skills", {
    "layer": "cco", "signal": "skill_point_spent_and_committed",
    "snapshot": _skills_snapshot, "prechecks": [],
    "execute": _skills_execute, "confirm": _skills_confirm,
    "timeout_s": 6.0, "poll_s": 0.25,
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
    common.wait("character_select_settle", 1.0, str(cqi))


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


def _equipped_count(v):
    try:
        return int(str(v).split("|", 1)[0])
    except (TypeError, ValueError):
        return -1


def _items_precheck(bus, ctx, pick, before):
    if str(before.get("can_equip")).lower() == "false":
        return False, "cannot_equip"
    return True, None


def _items_doomed(bus, ctx, pick, before, after):
    now = _equipped_count((after or {}).get("equipped"))
    was = _equipped_count(before.get("equipped"))
    if now < 0 or was < 0 or now != was:
        before["doom_streak"] = 0
        return None
    before["doom_streak"] = before.get("doom_streak", 0) + 1
    if before["doom_streak"] < 2:
        return None
    return ("equipped count still %d after %d polls -- the item never equipped"
            % (now, before["doom_streak"]))


register("items", {
    "layer": "cco", "signal": "equipped_count_increase",
    "snapshot": _items_snapshot, "prechecks": [_items_precheck],
    "execute": _items_execute, "confirm": _items_confirm,
    "doomed": _items_doomed,
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
    "timeout_s": 5.0, "poll_s": 0.25,
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


def _rites_execute(bus, ctx, pick, before):
    idx = int(pick["params"]["rite_index"])
    res = _ev(bus, (_LUA_RITES % {"fac": before["faction"]}) +
              "local r=l[%d]; if not r then return 'NO-RITE' end "
              "pcall(function() r:Call('Perform') end); return 'called'" % idx, timeout=25.0)
    return res == "called"


def _rites_confirm(bus, ctx, pick, before):
    flags = _rite_flags(bus, before["faction"], pick["params"]["rite_index"])
    return (flags != before.get("flags") and not str(flags).startswith("true/")), {"flags": flags}


def _rites_doomed(bus, ctx, pick, before, after):
    flags = (after or {}).get("flags")
    if not flags or flags != before.get("flags"):
        before["doom_streak"] = 0
        return None
    before["doom_streak"] = before.get("doom_streak", 0) + 1
    if before["doom_streak"] < 2:
        return None
    return ("rite flags unchanged (%s) after %d polls -- the rite never fired"
            % (flags, before["doom_streak"]))


register("rites", {
    "layer": "cco", "signal": "can_perform_false_and_complete",
    "snapshot": _rites_snapshot, "prechecks": [],
    "execute": _rites_execute, "confirm": _rites_confirm,
    "doomed": _rites_doomed,
    "timeout_s": 8.0, "poll_s": 1.5,
})


_LUA_SLOT_OP = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(region)s') "
    "if not s then return 'NO-CTX' end local slots=g(s,'BuildingSlotList') "
    "if type(slots)~='table' then return 'NO-SLOTLIST' end "
    "for i,x in ipairs(slots) do if g(x,'Index')==%(slot)d then "
    "  if not g(x,'%(guard)s') then return 'REFUSED-'..ts(g(x,'%(guard)s')) end "
    "  pcall(function() x:Call('%(cmd)s') end) return 'called' end end return 'NO-SLOT'")

_LUA_SLOT_FLAGS = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(region)s') "
    "if not s then return 'NO-CTX' end local slots=g(s,'BuildingSlotList') "
    "if type(slots)~='table' then return 'NO-SLOTLIST' end "
    "for i,x in ipairs(slots) do if g(x,'Index')==%(slot)d then "
    "  local ci=g(x,'ConstructionItemContext') "
    "  return ts(g(x,'BuildingContext.IsDamaged'))..'~'..ts(g(x,'IsRepairing'))"
    "..'~'..ts(ci~=nil)..'~'..ts(g(x,'IsEmpty')) end end return 'NO-SLOT'")


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


def _slot_exec_paced(cmd, guard, pause=1.0):
    inner = _slot_exec(cmd, guard)

    def run(bus, ctx, pick, before):
        common.wait("slot_op_pacing", pause, "before " + cmd)
        ok = inner(bus, ctx, pick, before)
        common.wait("slot_op_pacing", pause, "after " + cmd)
        return ok
    return run


def _slot_confirm(field, want):
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
    "timeout_s": 6.0, "poll_s": 0.25, "spends_gold": True,
})

register("building_cancel", {
    "layer": "cco", "signal": "construction_item_cleared",
    "snapshot": _slot_snapshot,
    "execute": _slot_exec("CancelConstruction", "ConstructionItemContext"),
    "confirm": _slot_confirm("queued", False),
    "timeout_s": 6.0, "poll_s": 0.25,
})

register("building_dismantle", {
    "layer": "cco", "signal": "slot_queued_flip",
    "snapshot": _slot_snapshot,
    "execute": _slot_exec_paced("Dismantle", "CanDismantle"),
    "confirm": _slot_confirm("queued", True),
    "timeout_s": 6.0, "poll_s": 0.25,
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

_LUA_BUNDLE_ACTIVE = (
    "local t=cm:get_character_by_cqi(%(tgt)s) if not t then return 'NO-TARGET' end "
    "if not t:has_military_force() then return 'NO-FORCE' end "
    "return tostring(t:military_force():has_effect_bundle('%(bundle)s'))")

_LUA_BUNDLE_EFFECTS = (
    "local v=common.get_context_value('CcoCampaignCharacter','%(tgt)s',"
    "[[MilitaryForceContext.EffectBundleUnfilteredList.Filter(Key == '%(bundle)s')"
    ".FirstContext.EffectList.Size]]) return tostring(v)")

_LUA_EMBEDDED = (
    "local a=cm:get_character_by_cqi(%(cqi)s) if not a then return 'NO-AGENT' end "
    "if not a:is_embedded_in_military_force() then return 'false' end "
    "local f=a:embedded_in_military_force() "
    "if not f then return 'true~none' end "
    "return 'true~'..tostring(f:general_character():command_queue_index())")


_LUA_SETT_DISPLAY = ("local r=cm:get_region('%(tgt)s') if not r then return 'NO-REGION' end "
                     "local s=r:settlement() if not s then return 'NO-SETT' end "
                     "return tostring(s:display_position_x())..'~'..tostring(s:display_position_y())")

_LUA_CHAR_DISPLAY = ("local c=cm:get_character_by_cqi(%(tgt)s) if not c then return 'NO-CHAR' end "
                     "return tostring(c:display_position_x())..'~'..tostring(c:display_position_y())")

_LUA_CAM = ("local a,b,c,d,e = cm:get_camera_position() "
            "return tostring(a)..'~'..tostring(b)..'~'..tostring(c)..'~'..tostring(d)..'~'..tostring(e)")


def _collect_mod():
    sys.path.insert(0, common.DECISIONS)
    import collect as _C
    return _C


def _options_mod():
    sys.path.insert(0, common.ADVISOR)
    import options as _O
    return _O


def _hero_action_method_name(action):
    spec = _options_mod().HERO_ACTIONS.get(action)
    if not spec:
        return None
    sys.path.insert(0, common.REFERENCE)
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
    p = pick.get("params") or {}
    if p.get("target_kind") == "character" and p.get("target_cqi") is not None:
        return "character", str(p["target_cqi"])
    if p.get("region"):
        return "settlement", str(p["region"])
    return None, None


def _assist_route(pick):
    action = (pick.get("params") or {}).get("action")
    spec = _options_mod().HERO_ACTIONS.get(action) or {}
    return tuple(spec.get("targets") or ()) == ("own_armies",)


def _embedded(bus, cqi):
    return str(_ev(bus, _LUA_EMBEDDED % {"cqi": cqi}, timeout=8.0) or "")


_assist_payloads = {}


def _assist_payload(action):
    if action not in _assist_payloads:
        spec = _options_mod().HERO_ACTIONS.get(action) or {}
        sys.path.insert(0, common.REFERENCE)
        import features_db as _DB
        _assist_payloads[action] = _DB.agent_action_payload(spec.get("loc_suffix"))
    return _assist_payloads[action]


def _bundle_state(bus, tid, bundle):
    if not bundle:
        return None, None
    active = str(_ev(bus, _LUA_BUNDLE_ACTIVE % {"tgt": tid, "bundle": bundle}, timeout=8.0) or "")
    live = str(_ev(bus, _LUA_BUNDLE_EFFECTS % {"tgt": tid, "bundle": bundle}, timeout=8.0) or "")
    return active, live


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
    st["route"] = "assist" if _assist_route(pick) else "panel"
    if st["route"] == "assist":
        st["embedded_before"] = _embedded(bus, ctx["entity_id"])
        payload = _assist_payload((pick.get("params") or {}).get("action"))
        st["payload"] = payload
        st["bundle_before"], st["effects_before"] = _bundle_state(
            bus, tid, (payload or {}).get("target_bundle"))
    return st


_LUA_CHAR_REGION = ("local c=cm:get_character_by_cqi(%(tgt)s) if not c then return '' end "
                    "local r=c:region() if not r or r:is_null_interface() then return '' end "
                    "return tostring(r:name())")


def _banner_rows(nodes):
    return [{"id": str(n.get("id")), "context": str(n.get("context") or ""),
             "x": n.get("x"), "y": n.get("y"), "w": n.get("w"), "h": n.get("h"),
             "visible": n.get("visible"), "state": str(n.get("state") or "")}
            for n in nodes]


def _banner_bounds(nodes):
    vis = [n for n in nodes if n.get("visible")] or list(nodes)
    if not vis:
        return None
    x0 = min(float(n["x"]) for n in vis)
    y0 = min(float(n["y"]) for n in vis)
    x1 = max(float(n["x"]) + float(n.get("w") or 0) for n in vis)
    y1 = max(float(n["y"]) + float(n.get("h") or 0) for n in vis)
    return (x0, y0, x1, y1)


def _dump_overlay(tree, tid):
    import json as _json
    import os as _os
    import nav
    if not nav.dev_mode():
        return None
    try:
        _os.makedirs(nav.SCREEN_DUMP_DIR, exist_ok=True)
        path = _os.path.join(nav.SCREEN_DUMP_DIR,
                             "%d_hero_action_3d_ui_parent.json" % int(time.time() * 1000))
        nodes = tree.get("nodes") or []
        with open(path, "w", encoding="utf-8") as fh:
            _json.dump({"ts": time.time(), "root": "3d_ui_parent", "why": "hero_action",
                        "target": str(tid), "truncated": tree.get("truncated"),
                        "depth": 12, "node_budget": 6000, "nodes": nodes}, fh, default=str)
        sys.stderr.write("cco_actions: dumped 3d_ui_parent for target %s (%d nodes, truncated=%s) "
                         "-> %s\n" % (tid, len(nodes), tree.get("truncated"), path))
        return path
    except Exception as e:
        sys.stderr.write("cco_actions: overlay dump failed -> %s\n" % repr(e)[:90])
        return None


def _overlay_nodes_for(bus, tid):
    import trace as TR
    tr = bus.send("tree", "3d_ui_parent 12 6000", timeout=8.0) or {}
    _dump_overlay(tr, tid)
    nodes = [n for n in (tr.get("nodes") or [])
             if n.get("x") is not None and n.get("y") is not None]
    reg, tier = None, "own_marker"
    hits = [n for n in nodes if str(n.get("context")) == "CcoCampaignCharacter:%s" % tid]
    if not hits:
        tier = "any_context_bound_to_cqi"
        hits = [n for n in nodes if str(n.get("context") or "").endswith(":%s" % tid)]
    if not hits:
        tier = "garrison_settlement_label"
        reg = str(_ev(bus, _LUA_CHAR_REGION % {"tgt": tid}, timeout=6.0) or "").strip() or None
        if reg:
            hits = [n for n in nodes if str(n.get("id")) == "label_settlement:%s" % reg]
            if hits:
                sys.stderr.write("cco_actions: character %s draws no banner of its own -- it is "
                                 "garrisoned in %s, clicking that settlement's banner\n"
                                 % (tid, reg))
    if not hits:
        tier = "none"
    bounds = _banner_bounds(hits)
    TR.emit("hero_target_banner", target=str(tid), tier=tier, region=reg,
            n_positioned=len(nodes), n_candidates=len(hits),
            n_visible=sum(1 for n in hits if n.get("visible")),
            bounds_ui=bounds, candidates=_banner_rows(hits))
    if hits:
        sys.stderr.write("cco_actions: banner for %s via %s -- %d nodes (%d visible), "
                         "ui bounds %s, ids %s\n"
                         % (tid, tier, len(hits), sum(1 for n in hits if n.get("visible")),
                            bounds, sorted({str(n.get("id")) for n in hits})[:12]))
    return hits, nodes


def _centre_camera_on_target(bus, kind, tid):
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
    if kind != "character":
        return True
    hits, nodes = _overlay_nodes_for(bus, tid)
    if hits:
        return True
    seen = collections.Counter(str(n.get("context") or "").split(":")[0]
                               for n in nodes if n.get("context"))
    sys.stderr.write(
        "cco_actions: camera moved to %s %s (%.2f,%.2f) but the target is NOT RENDERED -- no "
        "overlay banner, so there is nothing to click. 3d_ui_parent holds %d positioned nodes, "
        "contexts %s, cqis %s\n"
        % (kind, tid, tx, ty, len(nodes), dict(seen.most_common(6)),
           sorted({str(n.get("context")).split(":")[-1] for n in nodes
                   if n.get("context") and ":" in str(n.get("context"))})[:20]))
    return False


def _hero_target_node(bus, kind, tid):
    import nav
    if kind == "character":
        hits, nodes = _overlay_nodes_for(bus, tid)
        if not hits:
            seen = collections.Counter(str(n.get("context") or "").split(":")[0]
                                       for n in nodes if n.get("context"))
            sys.stderr.write(
                "cco_actions: character %s has no overlay marker and no garrison settlement "
                "banner -- refusing. 3d_ui_parent holds %d positioned nodes, contexts present: "
                "%s; cqis present: %s\n"
                % (tid, len(nodes), dict(seen.most_common(8)),
                   sorted({str(n.get("context")).split(":")[-1] for n in nodes
                           if n.get("context") and ":" in str(n.get("context"))})[:20]))
            return None
        import trace as TR
        pool = [n for n in hits if n.get("visible")] or hits
        byid = {}
        for n in pool:
            byid.setdefault(str(n.get("id")), []).append(n)
        node, anchor = None, None
        for want in _MARKER_ANCHORS:
            if want in byid:
                node, anchor = byid[want][0], want
                break
        if node is None:
            node, anchor = byid[sorted(byid)[0]][0], "alphabetical_fallback"
        anchor = "%s%s" % (anchor, "" if node.get("visible") else "_invisible")
        ux = float(node["x"]) + (node.get("w") or 0) / 2.0
        uy = float(node["y"]) + (node.get("h") or 0) / 2.0
        pt = nav.ui_to_screen(ux, uy)
        TR.emit("hero_target_point", target=str(tid), anchor=anchor,
                chosen=_banner_rows([node])[0], ui_point=[ux, uy], screen_point=list(pt),
                bounds_ui=_banner_bounds(hits), n_candidates=len(hits))
        sys.stderr.write("cco_actions: click point for %s from node %r (anchor=%s) rect "
                         "x=%s y=%s w=%s h=%s vis=%s -> ui (%.1f,%.1f) -> screen %s\n"
                         % (tid, str(node.get("id")), anchor, node.get("x"), node.get("y"),
                            node.get("w"), node.get("h"), node.get("visible"), ux, uy, pt))
        return node
    tr = bus.send("tree", "3d_ui_parent 12 6000", timeout=8.0) or {}
    nodes = tr.get("nodes") or []
    want = "label_settlement:%s" % tid
    hits = [n for n in nodes if str(n.get("id")) == want
            and n.get("x") is not None and n.get("y") is not None]
    if hits:
        return hits[0]
    sys.stderr.write("cco_actions: settlement %s has no on-screen label (%d overlay nodes)\n"
                     % (tid, len(nodes)))
    return None


def _hero_action_button(bus, name, attribute=None):
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
    try:
        return _hero_action_execute_inner(bus, ctx, pick, before)
    finally:
        if before.get("panel_opened"):
            import interrupts
            inflight = sys.exc_info()[1]
            try:
                g = interrupts.claim_screen(bus, "hero_action_close")
            except BaseException:
                if inflight is not None:
                    sys.stderr.write("cco_actions: hero_action guard raised over in-flight %r\n"
                                     % (inflight,))
                raise
            if not g.get("left"):
                _ev(bus, 'common.call_context_command([[CloseAllPanels]]) return "sent"',
                    timeout=8.0)


def _hero_action_execute_inner(bus, ctx, pick, before):
    import click_actions
    import nav
    action = (pick.get("params") or {}).get("action")
    kind, tid = before["target_kind"], before["target_id"]
    where = "hero=%s action=%s target=%s:%s" % (ctx.get("entity_id"), action, kind, tid)

    def fail(step, detail=""):
        before["failed_at"] = step
        sys.stderr.write("cco_actions: hero_action FAILED at %s -- %s%s\n"
                         % (step, where, (" -- " + detail) if detail else ""))
        return False

    if before.get("route") == "assist":
        if kind != "character":
            return fail("assist_target", "assist_army action targeted %s:%s" % (kind, tid))
        payload = before.get("payload")
        if not payload:
            return fail("assist_payload", "no cannot-fail result bundle for action %r" % action)
        effects = ";".join("%s|%s|%s" % (k, s, v) for k, s, v in payload["effects"])
        r = bus.send("assist", "%s %s %s %s %s %s %s" % (
            ctx["entity_id"], tid, payload["target_bundle"], payload["target_turns"],
            payload["actor_bundle"], payload["actor_turns"], effects), timeout=25.0) or {}
        before["assist"] = r
        if r.get("error"):
            return fail("assist", str(r.get("error")))
        if not r.get("applied"):
            return fail("assist_apply", "embedded=%s effects=%s active=%s"
                        % (r.get("embedded"), r.get("effects"), r.get("active")))
        return True

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
    node = _hero_target_node(bus, kind, tid)
    if node is None:
        return fail("target_point", "no overlay node for the target after centring")
    tpath = node.get("path") or node.get("full_path")
    off = bus._out_size()
    opened = False
    if tpath:
        if nav.bus_input(bus, "cco_actions.py:hero_target_rclick", tpath, action="rclick"):
            _tw = time.time()
            row, _ = bus.wait_row(("panel",), timeout=3.0, offset=off,
                                  pred=lambda r2: bool(r2.get("opened"))
                                  and r2.get("name") == _HERO_PANEL)
            opened = row is not None
            common.waitlog("hero_panel_open", time.time() - _tw, opened, _HERO_PANEL)
    if not opened:
        return fail("open_panel", "rclick on %s did not open %s" % (tpath, _HERO_PANEL))
    before["panel_opened"] = True
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
    if before.get("route") == "assist":
        now = _embedded(bus, ctx["entity_id"])
        payload = before.get("payload") or {}
        bundle = payload.get("target_bundle")
        active, live = _bundle_state(bus, before.get("target_id"), bundle)
        after = {"route": "assist", "embedded_before": before.get("embedded_before"),
                 "embedded_after": now, "bundle": bundle, "bundle_active": active,
                 "effects_live": live, "effects_before": before.get("effects_before"),
                 "effects": payload.get("effects"), "result_key": payload.get("result_key"),
                 "assist": before.get("assist")}
        ok = (now.startswith("true") and now.split("~")[-1] == str(before.get("target_id"))
              and active == "true" and _numf(live) is not None and _numf(live) > 0)
        return ok, after
    timeout = 0.05 if before.get("failed_at") else 0.75
    row, _ = bus.wait_row(("agent_action",), timeout=timeout, offset=before["stream_off"],
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
    "prechecks": [],
    "execute": _hero_action_execute, "confirm": _hero_action_confirm,
    "timeout_s": 8.0, "poll_s": 0.05,
})


def _endturn_snapshot(bus, ctx, pick):
    t = _ev(bus, "return cm:model():turn_number()", timeout=8.0)
    return None if t is None else {"turn": t}


_LUA_PENDING = (
    "local function g(c,p) local ok,v=pcall(function() return c:Call(p) end) "
    "if ok and v~=nil then return tostring(v) end return 'nil' end "
    "local r=cco('CcoCampaignRoot','') local pa=nil "
    "pcall(function() pa=r:Call('PendingActionContext') end) "
    "if not pa then return 'none' end "
    "return g(pa,'IsActive')..'|'..g(pa,'IsBlockingLocalPlayer')..'|'"
    "..g(pa,'ShouldBlockAllInteractions')..'|'..g(pa,'ActionType')")

_LUA_ENDTURN = (_G + "local r=cco('CcoCampaignRoot','') "
                     "local ok,err=pcall(function() r:Call('EndTurn') end) "
                     "return tostring(ok)..'|'..tostring(err)")


def pending_action(bus):
    return _ev(bus, _LUA_PENDING, timeout=8.0)


def _attack_pending(pend):
    p = str(pend or "")
    return p.startswith("true") and p.endswith("PENDING_ATTACK")


def _await_pending_attack(bus, cap=20.0, poll=0.5):
    import interrupts
    t0 = time.time()
    battles = 0
    while time.time() - t0 < cap:
        pend = pending_action(bus)
        if not _attack_pending(pend):
            common.waitlog("pending_attack_resolve", time.time() - t0, True,
                           "battles=%d pend=%s" % (battles, str(pend)[:32]))
            return True
        r = interrupts.roots(bus)
        if ("popup_pre_battle" in r or "popup_battle_results" in r
                or "settlement_captured" in r):
            steps = interrupts.resolve_battle(bus)
            if steps:
                battles += 1
                sys.stderr.write("cco_actions: end_turn blocked by PENDING_ATTACK -- "
                                 "resolved the in-flight battle: %s\n" % steps[:4])
            continue
        time.sleep(poll)
    common.waitlog("pending_attack_resolve", time.time() - t0, False,
                   "still pending after %d battles" % battles)
    return False


_LUA_NOTIF_EMPTY = ("local v=common.get_context_value('CcoCampaignRoot','',"
                    "'NotificationQueueContext.IsNotificationListEmpty') return tostring(v)")

_LUA_SUPPRESS_NOTIFS = ("common.call_context_command('CampaignRoot.NotificationQueueContext"
                        ".SetAllNotificationsSuppressed(true)') return 'sent'")

_END_TURN_BUTTON = "hud_campaign|faction_buttons_docker|button_end_turn"


def _end_turn_tooltip(bus):
    r = bus.send("find", _END_TURN_BUTTON, timeout=8.0) or {}
    return str((r.get("result") or {}).get("tooltip") or "")


def _clear_end_turn_blockers(bus):
    import nav
    import interrupts
    t0 = time.time()
    out = {"notifications_empty": _ev(bus, _LUA_NOTIF_EMPTY, timeout=8.0)}
    if str(out["notifications_empty"]).lower() != "true":
        _ev(bus, _LUA_SUPPRESS_NOTIFS, timeout=8.0)
        out["notifications_empty_after"] = _ev(bus, _LUA_NOTIF_EMPTY, timeout=8.0)
    _ev(bus, "cm:dismiss_advice() return 'ok'", timeout=8.0)
    clicks = 0
    for attempt in range(6):
        if "events" not in nav.visible_roots(bus):
            break
        if attempt:
            common.trylog("end_turn_event_dismiss", attempt, 6, False, "events still open")
        g = interrupts.claim_screen(bus, "end_turn_blockers")
        if g.get("fired"):
            out.setdefault("guards", []).append(g.get("steps"))
            if g.get("left"):
                break
            continue
        btns = nav.find_dismiss_buttons(bus, "events")
        if not btns:
            sys.stderr.write("cco_actions: end_turn blocked -- events panel open with no dismiss "
                             "button\n")
            break
        sig = nav.panel_sig(bus, "events")
        r = bus.send("click", btns[0], timeout=8.0) or {}
        if not r.get("clicked"):
            break
        clicks += 1
        nav.await_gone_or_changed(bus, "events", sig, 0.6, "event_dismiss_pacing")
    out["events_dismissed"] = clicks
    out["events_open"] = "events" in nav.visible_roots(bus)
    if clicks or out["events_open"]:
        common.trylog("end_turn_event_dismiss", clicks, 6, not out["events_open"],
                      "dismissed=%d" % clicks)
    out["tooltip"] = _end_turn_tooltip(bus)
    out["cleared_ms"] = int((time.time() - t0) * 1000)
    if out["events_open"] or str(out.get("notifications_empty_after",
                                         out["notifications_empty"])).lower() != "true":
        sys.stderr.write("cco_actions: end_turn preconditions NOT clear -- %s\n"
                         % json.dumps(out)[:300])
    return out


def _endturn_execute(bus, ctx, pick, before):
    try:
        import click_actions
        click_actions.clear_screen(bus, "end_turn")
    except Exception as e:
        sys.stderr.write("cco_actions: end_turn clear_screen -> %s\n" % repr(e)[:100])
    before["blockers"] = _clear_end_turn_blockers(bus)
    pend = pending_action(bus)
    if _attack_pending(pend):
        before["pending_attack_resolved"] = _await_pending_attack(bus)
        pend = pending_action(bus)
    res = _ev(bus, _LUA_ENDTURN)
    ok = str(res or "").startswith("true")
    if not ok or (pend and pend != "none" and not pend.startswith("false")):
        sys.stderr.write("cco_actions: end_turn EndTurn=%r pending_action=%r "
                         "(IsActive|IsBlockingLocalPlayer|ShouldBlockAllInteractions|ActionType)\n"
                         % (res, pend))
    before["pending_action"] = pend
    before["endturn_call"] = res
    return ok


_LUA_OUR_TURN = ("local me=cm:get_local_faction_name(true) "
                 "local ok,l=pcall(function() return cm:model():world():whose_turn_is_it() end) "
                 "if not ok or not l then return 'unknown' end "
                 "local ok2,n=pcall(function() return l:num_items() end) "
                 "if not ok2 then return 'unknown' end "
                 "for i=0,n-1 do if l:item_at(i):name()==me then return 'true' end end return 'false'")


def is_our_turn(bus):
    v = _ev(bus, _LUA_OUR_TURN, timeout=10.0)
    return None if v not in ("true", "false") else (v == "true")


def _endturn_confirm(bus, ctx, pick, before):
    def _took_effect():
        tt = _ev(bus, "return cm:model():turn_number()", timeout=8.0)
        try:
            moved = (tt is not None and float(tt) > float(before["turn"]))
        except (TypeError, ValueError):
            moved = False
        ours = is_our_turn(bus)
        return (moved or ours is False), tt


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
    after = {"turn": t, "our_turn": is_our_turn(bus), "interrupts": steps}
    if not ok:
        after["pending_action"] = pending_action(bus)
        try:
            import interrupts as _I
            after["roots"] = _I.roots(bus)
        except Exception as e:
            after["roots"] = repr(e)[:80]
        after["endturn_call"] = before.get("endturn_call")
        sys.stderr.write("cco_actions: end_turn UNCONFIRMED turn=%s our_turn=%s call=%r "
                         "pending=%r roots=%s\n"
                         % (t, after["our_turn"], after.get("endturn_call"),
                            after["pending_action"], after["roots"]))
    return ok, after


def _endturn_doomed(bus, ctx, pick, before, after):
    if not after or after.get("interrupts"):
        before["doom_streak"] = 0
        return None
    try:
        moved = float(after.get("turn")) > float(before.get("turn"))
    except (TypeError, ValueError):
        moved = False
    if moved or after.get("our_turn") is not True:
        before["doom_streak"] = 0
        return None
    before["doom_streak"] = before.get("doom_streak", 0) + 1
    if before["doom_streak"] < 2:
        return None
    return ("turn still %s and still our turn after %d polls -- EndTurn was refused"
            % (after.get("turn"), before["doom_streak"]))


register("end_turn", {
    "layer": "cco", "signal": "turn_number_increment",
    "snapshot": _endturn_snapshot, "execute": _endturn_execute, "confirm": _endturn_confirm,
    "doomed": _endturn_doomed,
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
