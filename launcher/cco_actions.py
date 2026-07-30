r"""cco_actions.py -- v7 EXECUTION core: the confirmed-action engine, the action REGISTRY, and
every CCO-LED executor. Click-based executors live in click_actions.py and register into the
same REGISTRY (cco is always tried first; a click executor exists only where cco failed the
verification protocol -- see verify_cco_commands.py verdicts).

THE RULE THAT MAKES v7 DATA TRUSTWORTHY (the "how do we know a move failed" answer):
every action runs   snapshot(before) -> gates -> execute -> poll confirm(after) -> record
and `taken = executed AND confirmed`. An API "ok"/"clicked" is recorded but trusted for
NOTHING -- commands report success even when the engine refuses (verified: Construct on a
locked slot, stance Activate on an AP-blocked stance). Distinct failure classes are recorded
so the trainer can exclude and the humans can debug:
    forbidden_key | snapshot_failed | pre_check_refused | execute_failed |
    command_silently_refused (no state change by timeout) | executed_unconfirmed (partial)

ENGINE-SAFETY (live-verified 2026-07-29):
  * NEVER pass a Lua cco wrapper as a Call() argument -- HARD-HANGS the engine. All Call
    strings live in the _LUA_* templates below; only str/int keys are ever interpolated.
  * Every per-entry Call goes through the pcall safe-getter g() (zero-return trap).

Spec contract (a dict in REGISTRY, keyed by action_type):
    layer       "cco" | "click"
    snapshot    fn(bus, ctx, pick) -> dict            # before-state; doubles as gate input
    gates       [fn(bus, ctx, pick, before) -> (ok, reason)]
    execute     fn(bus, ctx, pick, before) -> bool    # issued (never trusted)
    confirm     fn(bus, ctx, pick, before) -> (bool, after_dict)
    timeout_s / poll_s                                 # confirm polling
    retryable   bool                                   # False for irreversible (battles, gifts)
    spends_gold bool                                   # engages the treasury-floor gate
    max_per_entity_turn int

ctx  = {"context_kind": "lord"|"settlement"|"campaign", "entity_id": str, ...}
pick = {"action_type": str, "key": str, "params": {...}, "policy": str}
"""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

TREASURY_FLOOR = 500        # never spend below this (tunable; config.py may override)
try:
    import config as _cfg
    TREASURY_FLOOR = getattr(_cfg, "TREASURY_FLOOR", TREASURY_FLOOR)
except Exception:
    pass

# battle-UI entries are NEVER selectable/executable -- hardcoded at advisor AND executor layer
FORBIDDEN_KEYS = frozenset({"button_attack", "button_spectate"})

# ---------------------------------------------------------------------------- lua templates
# ALL cco Call strings live here (grep-able wrapper-ban invariant: no Call( ever receives a
# Lua local; arguments are inline expression text composed from str/int only).
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
    """One eval; returns result or None (loud stderr, never an exception -- the engine turns
    None into an explicit refusal so a bus miss can't masquerade as anything else)."""
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


# ---------------------------------------------------------------------------- the engine
REGISTRY = {}


def register(action_type, spec):
    spec.setdefault("timeout_s", 6.0)
    spec.setdefault("poll_s", 1.2)
    spec.setdefault("retryable", True)
    spec.setdefault("spends_gold", False)
    spec.setdefault("max_per_entity_turn", 3)
    spec.setdefault("gates", [])
    REGISTRY[action_type] = spec
    return spec


def execute_confirmed(bus, ctx, pick):
    """Run one action through snapshot -> gates -> execute -> poll-confirm. Returns the
    ActionRecord dict (see module docstring). `counted` is the ONLY field that means taken."""
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
    try:
        before = spec["snapshot"](bus, ctx, pick)
    except Exception as e:
        rec["refusal"] = "snapshot_failed"
        rec["confirm"] = {"signal": None, "error": repr(e)[:160]}
        return rec
    if before is None:
        rec["refusal"] = "snapshot_failed"
        return rec
    if spec["spends_gold"]:
        cost = (pick.get("params") or {}).get("cost") or 0
        t = before.get("treasury")
        if t is not None and t - cost < TREASURY_FLOOR:
            rec["gates"] = {"passed": False, "failed_gate": "treasury_floor"}
            rec["refusal"] = "pre_check_refused"
            return rec
    for gate in spec["gates"]:
        ok, reason = gate(bus, ctx, pick, before)
        if not ok:
            rec["gates"] = {"passed": False, "failed_gate": reason}
            rec["refusal"] = "pre_check_refused"
            return rec
    t0 = time.time()
    try:
        rec["executed"] = bool(spec["execute"](bus, ctx, pick, before))
    except Exception as e:
        rec["refusal"] = "execute_failed"
        rec["confirm"] = {"signal": None, "error": repr(e)[:160]}
        return rec
    if not rec["executed"]:
        rec["refusal"] = "execute_failed"
        return rec
    deadline = t0 + spec["timeout_s"]
    confirmed, after, polls = False, {}, 0
    while time.time() < deadline:
        time.sleep(spec["poll_s"])
        polls += 1
        try:
            confirmed, after = spec["confirm"](bus, ctx, pick, before)
        except Exception as e:
            after = {"error": repr(e)[:160]}
            confirmed = False
        if confirmed:
            break
    rec["confirmed"] = bool(confirmed)
    rec["counted"] = rec["executed"] and rec["confirmed"]
    rec["confirm"] = {"signal": spec.get("signal"), "before": before, "after": after,
                     "latency_ms": int((time.time() - t0) * 1000), "polls": polls,
                     "timeout_s": spec["timeout_s"]}
    if not rec["counted"]:
        changed = any(after.get(k) != before.get(k) for k in after) if isinstance(after, dict) else False
        rec["refusal"] = "executed_unconfirmed" if changed else "command_silently_refused"
    return rec


# ---------------------------------------------------------------------------- cco: STANCE
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


# The HUD stance stack is the game's own answer to "which stances may this faction use". The cco
# StanceList cannot answer it -- it lists every stance in the game and Activate will happily set a
# faction-ILLEGAL one (verified rule breach: a High Elf army entered TUNNELING). This gate reads the
# stack ITSELF rather than trusting a whitelist passed in by the caller: the executor must be able
# to refuse an illegal stance even when the advisor asks for one.
# ⚠ the stack is collapsed most of the time, so only the current button is `visible` -- but the bus
# `find` handler enumerates children via ChildCount+Find(i), which is NOT visibility gated.
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
        return False, "stance_legality_unreadable"         # loud: never assume "everything is legal"
    if pick["key"] not in wl:
        return False, "stance_not_in_legality_whitelist"   # engine sets ILLEGAL stances; never bypass
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
    "timeout_s": 5.0, "poll_s": 1.2, "max_per_entity_turn": 1,
})


# ---------------------------------------------------------------------------- cco: BUILDING
# The cco layer exposes NO cost/affordability property on a building-level record (probed live:
# Cost / ConstructionCost / CreateCost / CanAfford / IsAffordable / PurchaseCost all return nil, and
# BuildingRequirementsMet covers requirements only -- NOT gold). So the price comes from the game's
# own DB, decoded offline into reference.sqlite (marble_1 = 1000 gold, verified). Without it the
# treasury gate can never fire and an unaffordable build reaches the engine, which accepts the
# command and silently does nothing -- the exact failure this whole layer exists to prevent.
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
        pick.setdefault("params", {})["cost"] = cost      # feeds the engine's treasury-floor gate
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
    slot = int((pick.get("params") or {}).get("slot_index"))
    t1 = _treasury(bus)
    is_new = _ev(bus, _LUA_SLOT_STATE % {"region": ctx["entity_id"], "slot": slot})
    ok = (is_new == "true") and (t1 is not None and before.get("treasury") is not None
                                 and t1 < before["treasury"])
    return ok, {"treasury": t1, "slot_is_building_new": is_new == "true"}


register("building", {
    "layer": "cco", "signal": "treasury_drop_and_is_building_new",
    "snapshot": _building_snapshot,
    "gates": [_building_gate_slot],
    "execute": _building_execute, "confirm": _building_confirm,
    "timeout_s": 6.0, "poll_s": 2.0, "spends_gold": True, "max_per_entity_turn": 2,
})


# ---------------------------------------------------------------------------- cco: RESEARCH
# faction -> TechnologyManagerContext -> TechnologyList -> entry with NodeKey -> StartResearching.
# LIVE-VERIFIED: is_currently_researching False->True, CurrentResearchingTechnologyContext.NodeKey
# == picked key; a prereq-gated tier-5 tech was silently refused (engine self-guards).
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
    """Research is a GOAL, not an immediate assignment.

    LIVE-VERIFIED: StartResearching on a node further up the tree starts the first researchable
    node on the path to it instead -- asking for wh2_main_tech_hef_5_01 left
    CurrentResearchingTechnologyContext on wh2_main_tech_hef_5_00. Demanding an exact key match
    therefore reports a real, successful command as `executed_unconfirmed`.

    So the signal is "research is now underway and the active tech CHANGED", and the tech that
    actually started is recorded as `actual` -- the request and the outcome both stay visible
    instead of one silently standing in for the other.
    """
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
    "timeout_s": 6.0, "poll_s": 1.5, "max_per_entity_turn": 1,
})


# ---------------------------------------------------------------------------- cco: SKILLS
# CharacterSkill::AddPoint (Void) then Character::CommitSkillChoices (Void).
# LIVE-VERIFIED: has_skill False->True, SkillPointsAvailable 1->0, HasUncommitedSkills true->false.
# NOTE has_skill flips at AddPoint (BEFORE commit) -- the reliable confirm is has_skill AND
# uncommitted back to false. A rank-locked skill with 0 points produced ZERO state change.
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
    "timeout_s": 6.0, "poll_s": 1.2, "max_per_entity_turn": 3,
})


# ---------------------------------------------------------------------------- cco: ITEMS
# Ancillary equip/unequip. LIVE-VERIFIED: RemoveAncillary (Void) 1->0; equip via the TWO-ARG
# form EquipToCharacter(SelectedCharacter(), NullContext()) 0->1 -- the ONE-ARG form silently
# no-ops. SelectedCharacter() is resolved by first calling the Void command Select on the
# character (also verified). Gate CanCharacterEquip(SelectedCharacter()) correctly read false
# when already equipped, and a repeat call then changed nothing.
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
    _select_character(bus, ctx["entity_id"])          # refresh SelectedCharacter() binding
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
    "timeout_s": 6.0, "poll_s": 1.2, "max_per_entity_turn": 2,
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
    "timeout_s": 5.0, "poll_s": 1.2, "max_per_entity_turn": 2,
})


# ---------------------------------------------------------------------------- cco: RITES
# faction AvailableRitualList[i]:Perform. LIVE-VERIFIED: CanPerformRitual true->false and
# IsComplete false->true on exactly the performed entry. NOTE the ritual objects expose no
# readable key/name property, so rites are addressed by LIST INDEX (params.rite_index).
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
    "timeout_s": 8.0, "poll_s": 1.5, "max_per_entity_turn": 2,
})


# ---------------------------------------------------------------------------- cco: END TURN
# CampaignRoot::EndTurn (Void). LIVE-VERIFIED: turn 1 -> 2 with NO notification suppression and
# NO hardware click -- this retires the v6 suppress + hardware-click-the-rect dance entirely.
def _endturn_snapshot(bus, ctx, pick):
    t = _ev(bus, "return cm:model():turn_number()", timeout=8.0)
    return None if t is None else {"turn": t}


def _endturn_execute(bus, ctx, pick, before):
    return _ev(bus, _G + "local r=cco('CcoCampaignRoot',''); "
                         "pcall(function() r:Call('EndTurn') end); return 'called'") == "called"


# ⚠ THE TURN NUMBER IS NOT "IT IS OUR TURN". cm:model():turn_number() advances when the ROUND
# advances, while the AI factions are still playing theirs. Acting in that window is silently
# refused by the engine for EVERYTHING -- live-proven: on "turn 3" with whose_turn_is_it() ==
# {wh2_main_def_karond_kar}, Construct was refused at two different settlements (including one we
# had never built in), research pre-check-refused, and lord recruitment failed. It all looked like
# a mysterious building bug and was simply "not our turn".
#
# whose_turn_is_it() returns a faction LIST, not a faction (calling :name() on it errors).
_LUA_OUR_TURN = ("local me=cm:get_local_faction_name(true) "
                 "local ok,l=pcall(function() return cm:model():world():whose_turn_is_it() end) "
                 "if not ok or not l then return 'unknown' end "
                 "local ok2,n=pcall(function() return l:num_items() end) "
                 "if not ok2 then return 'unknown' end "
                 "for i=0,n-1 do if l:item_at(i):name()==me then return 'true' end end return 'false'")


def is_our_turn(bus):
    """True / False / None(unknown) -- whether the human faction currently holds the turn."""
    v = _ev(bus, _LUA_OUR_TURN, timeout=10.0)
    return None if v not in ("true", "false") else (v == "true")


def _endturn_confirm(bus, ctx, pick, before):
    """Waiting for the turn to advance is NOT enough -- it will not advance while the AI's turn is
    blocked on something that needs us.

    ⚠ THE CASE THIS EXISTS FOR: a faction attacks US during its own turn, so a Battle Deployment
    (popup_pre_battle) opens mid inter-turn. The turn number then never moves, and a confirm that
    only polls the turn number sits there for its whole timeout doing nothing while the battle waits
    for a click. So every poll also CLEARS INTERRUPTS -- autoresolving a defensive battle, taking
    the post-battle options, declining diplomacy -- and only then re-reads the turn.
    """
    t = _ev(bus, "return cm:model():turn_number()", timeout=8.0)
    steps = []
    try:
        advanced = (t is not None and float(t) > float(before["turn"]))
    except (TypeError, ValueError):
        advanced = False
    if not advanced:
        try:
            import interrupts
            steps = interrupts.resolve(bus)
            if steps:
                t = _ev(bus, "return cm:model():turn_number()", timeout=8.0)
                try:
                    advanced = (t is not None and float(t) > float(before["turn"]))
                except (TypeError, ValueError):
                    advanced = False
        except Exception as e:
            sys.stderr.write("cco_actions: end_turn interrupt sweep -> %s\n" % repr(e)[:120])
    return advanced, {"turn": t, "interrupts": steps}


register("end_turn", {
    "layer": "cco", "signal": "turn_number_increment",
    "snapshot": _endturn_snapshot, "execute": _endturn_execute, "confirm": _endturn_confirm,
    "timeout_s": 200.0, "poll_s": 4.0, "retryable": False, "max_per_entity_turn": 1,
})


# ---------------------------------------------------------------------------- noop
register("noop", {"layer": "cco", "signal": "none",
                  "snapshot": lambda bus, ctx, pick: {},
                  "execute": lambda bus, ctx, pick, before: True,
                  "confirm": lambda bus, ctx, pick, before: (True, {})})


if __name__ == "__main__":
    # live smoke: stance flip + revert through the ENGINE (whitelist faked to the known-legal set)
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
