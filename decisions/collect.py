r"""collect.py -- THE RECORDER'S GAME-READ LAYER. The only code in the project that reads campaign
state for the advisor.

Role split this file exists to enforce:
  RECORDER (here)  talks to the game. When the advisor asks, it reads RAW DATA at that instant --
                   campaign scalars, every entity's raw state, the raw world positions, and the
                   full offer set with the game's own availability verdict -- and hands it back for
                   the store to persist. It does NOT compute features.
  ADVISOR          never touches the bus. It builds its features from the DB RECORDS this layer
                   produced, which is also why training and prediction featurize identically.
  LAUNCHER         executes. Its own snapshot/gate/confirm reads are execution-internal and stay
                   in launcher/; nothing here imports them.

Raw means raw: positions, not distances; flags, not ring counts; record keys, not costs. The one
thing that looks derived but is not is `available`/`gate` and reachability -- those are the GAME's
verdicts (cm:character_can_reach_*), which the advisor cannot compute from records.

Design rules carried over from the executor work (all learned the hard way):
  * The bus command line is "<seq> <channel> <payload>" -> EVAL PAYLOADS MUST BE ONE LINE.
  * A nil/empty result proves nothing on its own; every query here is shaped so a broken chain is
    distinguishable from a genuinely empty option set (errors raise, empties return []).
  * NEVER pass a Lua cco wrapper as a Call() argument -- it HARD-HANGS the engine. Inline
    expression text only; every per-entry Call goes through the pcall safe-getter g().
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")

# ---- the recorder's own eval plumbing (no launcher imports: see the role split above) ----------
_G = ("local function g(c,p) local ok,v=pcall(function() return c:Call(p) end);"
      "if ok and v~=nil then return v end return nil end "
      "local function ts(v) return tostring(v) end ")


class CollectError(RuntimeError):
    """A collection chain broke (bus miss / nil context). Loud by design -- never silently empty."""


def _ev(bus, lua, timeout=20.0, allow_nil=False):
    try:
        r = bus.send("eval", lua, timeout=timeout) or {}
    except Exception as e:
        raise CollectError("bus eval failed: %s" % repr(e)[:110])
    if r.get("error"):
        raise CollectError("lua error: %s" % str(r["error"])[:160])
    v = r.get("result")
    if v is None and not allow_nil:
        raise CollectError("eval returned nil: %s" % lua[:90])
    return v


def _chan(bus, channel, key, timeout=10):
    try:
        return (bus.send(channel, "", timeout=timeout) or {}).get(key) or []
    except Exception as e:
        raise CollectError("bus %s: %s" % (channel, repr(e)[:90]))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _flags(raw):
    """'a=true,b=false' -> {'a': True, 'b': False}"""
    out = {}
    for part in str(raw or "").split(","):
        if "=" in part:
            k, v = part.rsplit("=", 1)
            out[k] = (v == "true")
    return out


# ============================================================ STREAM 1: TARGET ROW (once per turn)
# POWER RANK: the GAME'S OWN metric -- cco CcoCampaignFaction.StrengthRank ("the rank amongst all
# current active factions in terms of strength"), the number the diplomacy panel prints. One free
# property read, replacing v6's sweep over every faction's forces.
# NB rank is "lower = stronger"; the store INVERTS it so every reward part is high=good.
# ⚠ the diplomacy PANEL caches its value at open time, so a UI reading lags the live property. Fine
# for a reward as long as we always sample at the SAME point in the turn cycle -- hence end of turn.
_LUA_TARGET = (_G +
    "local f=cm:get_local_faction(true) local me=f:name() local fc=cco('CcoCampaignFaction',"
    "tostring(f:command_queue_index())) local allies=0 local vassals=0 "
    "local fl=cm:model():world():faction_list() "
    "for i=0,fl:num_items()-1 do local o=fl:item_at(i) if o:name()~=me then "
    "local ok1,al=pcall(function() return f:allied_with(o) end) if ok1 and al then allies=allies+1 end "
    "local ok2,va=pcall(function() return o:is_vassal_of(f) end) if ok2 and va then vassals=vassals+1 end end end "
    "return me..'|'..cm:model():turn_number()..'|'..f:income()..'|'..f:region_list():num_items()"
    "..'|'..allies..'|'..vassals..'|'..ts(g(fc,'StrengthRank'))")


def target_row(bus):
    """{campaign_id, turn, income, settlements, allies, vassals, power_rank} -- the reward inputs."""
    p = str(_ev(bus, _LUA_TARGET, timeout=60.0)).split("|")
    if len(p) < 7:
        raise CollectError("target row malformed: %r" % p)
    return {"campaign_id": p[0], "turn": _num(p[1]), "income": _num(p[2]), "settlements": _num(p[3]),
            "allies": _num(p[4]), "vassals": _num(p[5]), "power_rank": _num(p[6]), "ts": time.time()}


# ============================================================ CAMPAIGN + WORLD (raw)
_LUA_CAMPAIGN = (_G + "local f=cm:get_local_faction(true) "
                 "return f:name()..'|'..cm:model():turn_number()..'|'..f:income()..'|'"
                 "..f:region_list():num_items()..'|'..cm:get_faction(cm:get_local_faction_name(true)):treasury()"
                 "..'|'..tostring(f:is_currently_researching())"
                 "..'|'..tostring(f:command_queue_index())")


def campaign_state(bus):
    p = str(_ev(bus, _LUA_CAMPAIGN)).split("|")
    if len(p) < 7:
        raise CollectError("campaign state malformed: %r" % p)
    return {"faction": p[0], "turn": _num(p[1]), "income": _num(p[2]), "settlements": _num(p[3]),
            "treasury": _num(p[4]), "is_researching": p[5] == "true", "faction_cqi": p[6]}


def world_state(bus):
    """Raw positional data for the whole visible map at this instant. The advisor derives its local
    force picture (distances, neighbour counts) from THIS -- the recorder does not."""
    return {"armies": _chan(bus, "chars", "chars"),
            "settlements": _chan(bus, "setts", "setts"),
            "hostiles": _chan(bus, "hostiles", "hostiles")}


# ============================================================ LIVENESS HASH (stuck detector)
# A digest of things that only change when the game ACTUALLY PROGRESSES. Deliberately excludes
# anything that ticks on its own (wall clock, frame counters, animation state) -- if those were in
# it, a frozen game would still look alive.
#
# What is in it: turn, treasury, income, region/character counts, every character's position + AP +
# stance, and the set of visible UI roots. That last one is what catches a blocking popup: an
# unhandled modal pins every other value AND the root set, so the hash stops moving. A dead bus
# raises instead, which the watchdog treats the same way -- "blocked" and "identical" are both stuck.
_LUA_HASH = (_G +
    "local f=cm:get_local_faction(true) local o={} "
    "o[#o+1]=cm:model():turn_number()..':'..f:treasury()..':'..f:income()..':'..f:region_list():num_items() "
    "local cl=f:character_list() for i=0,cl:num_items()-1 do local c=cl:item_at(i) "
    "o[#o+1]=ts(c:command_queue_index())..'@'..ts(c:logical_position_x())..','..ts(c:logical_position_y())"
    "..'/'..ts(math.floor((c:action_points_remaining_percent() or 0)))"
    "..'/'..ts(c:has_military_force() and c:military_force():active_stance() or '-') end "
    "return table.concat(o,'|')")


def state_hash(bus):
    """{hash, parts, roots} -- the liveness digest. Raises (loudly) if the game cannot answer."""
    import hashlib
    parts = str(_ev(bus, _LUA_HASH, timeout=20.0))
    try:
        r = bus.send("roots", "", timeout=10) or {}
    except Exception as e:
        raise CollectError("bus roots: %s" % repr(e)[:90])
    if not r.get("kids"):
        raise CollectError("bus roots returned no kids -- a bus failure would otherwise read as a "
                           "clean screen, which is exactly the state we must not fake")
    roots = sorted(str(k.get("id")) for k in r["kids"] if k.get("visible") and k.get("id"))
    blob = parts + "||" + ",".join(roots)
    return {"hash": hashlib.md5(blob.encode("utf-8", "replace")).hexdigest(),
            "roots": roots, "chars": blob.count("@"), "ts": time.time()}


# ============================================================ UI-STACK ENUMERATORS (read-only)
# The HUD collapsible stacks are read with the bus `find` handler, which enumerates direct children
# via ChildCount+Find(i) -- NOT visibility gated. That matters: these stacks are collapsed most of
# the time, so a visible-tree walk sees only the current button (verified: `find` returned all 13
# stance buttons off a collapsed, invisible stack).
STANCE_STACK = "hud_campaign|BL_parent|land_stance_button_stack|clip_parent|stack_background"


def _find(bus, path, timeout=8.0):
    try:
        r = bus.send("find", path, timeout=timeout) or {}
        return (r.get("result") or {}), (r.get("child_ids") or [])
    except Exception as e:
        raise CollectError("bus find %s: %s" % (path.rsplit("|", 1)[-1], repr(e)[:80]))


def legal_stances(bus):
    """{stance_key: ui_state} for the stances THIS FACTION may use.

    The cco StanceList cannot answer this: it lists every stance in the game and Activate will set
    a faction-ILLEGAL one (verified rule breach -- a High Elf army entered TUNNELING). The HUD stack
    is the game's own answer; state 'inactive' means present-but-not-usable.

    ⚠ KNOWN LIMIT: the stack reflects the CURRENTLY SELECTED army, so with nothing selected some
    genuinely legal stances also read 'inactive' (verified live turn 1: MARCH and DEFAULT inactive
    alongside TUNNELING). That makes this CONSERVATIVE -- it can under-offer stances, never
    over-offer them. Deliberate: the failure we must never have is executing an illegal stance, and
    selecting each army to refresh the stack would steal the UI focus this design avoids.
    """
    _res, kids = _find(bus, STANCE_STACK, timeout=12.0)
    out = {}
    for k in kids:
        if not k.startswith("button_") or k == "button_default":
            continue
        res, _ = _find(bus, "%s|%s" % (STANCE_STACK, k))
        out[k[len("button_"):]] = res.get("state")
    if not out:
        sys.stderr.write("collect: stance stack enumerated 0 buttons (%s)\n" % STANCE_STACK)
    return out


def recruitable_units(bus):
    """`<unit_key>_recruitable` cards currently in units_panel (present only while it is open)."""
    try:
        tr = bus.send("tree", "units_panel 30 9000", timeout=20) or {}
    except Exception as e:
        raise CollectError("bus tree units_panel: %s" % repr(e)[:80])
    return [{"key": str(n.get("id"))[:-len("_recruitable")], "state": n.get("state")}
            for n in (tr.get("nodes") or [])
            if str(n.get("id") or "").endswith("_recruitable") and n.get("visible")]


def edict_options(bus, region):
    """The province's edict record keys (the option set for this settlement's province)."""
    raw = _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s');"
                        "local m=g(s,'FactionProvinceManagerContext'); if not m then return '' end "
                        "local l=g(m,'InitiativeList'); if type(l)~='table' then return '' end local o={} "
                        "for i=1,#l do o[#o+1]=ts(g(l[i],'Key')) end return table.concat(o,',')"
              % region, timeout=20.0)
    return [k for k in str(raw or "").split(",") if k and k != "nil"]


# ============================================================ ENTITY STATE (raw)
_LUA_LORD = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') if not c then return 'NO-CHAR' end "
    "local mf=g(c,'MilitaryForceContext') local ch=cm:get_character_by_cqi(%(cqi)s) "
    "local pend=g(mf,'PendingRecruitmentUnitList') "
    "return ts(g(c,'Rank'))..'|'..ts(g(c,'SkillPointsAvailable'))..'|'..ts(mf and g(mf,'UnitCount'))"
    "..'|'..ts(type(pend)=='table' and #pend or -1)..'|'..ts(g(c,'ActionPointPercent'))"
    "..'|'..ts(mf and g(mf,'IsGarrisoned'))..'|'..ts(ch and ch:is_besieging())"
    "..'|'..ts((function() local l=g(mf,'StanceList') if type(l)=='table' then for i=1,#l do "
    "if g(l[i],'IsActive')==true then return g(l[i],'Key') end end end return 'none' end)())"
    "..'|'..ts(ch and ch:performed_action_this_turn())"
    "..'|'..ts(ch and ch:region() and ch:region():name())"
    "..'|'..ts(ch and ch:logical_position_x())..'|'..ts(ch and ch:logical_position_y())")


def lord_state(bus, cqi):
    p = str(_ev(bus, _LUA_LORD % {"cqi": cqi}, timeout=25.0)).split("|")
    if len(p) < 12:
        raise CollectError("lord state malformed for %s: %r" % (cqi, p))
    return {"cqi": str(cqi), "rank": _num(p[0]), "skill_points": _num(p[1]), "units": _num(p[2]),
            "pending_recruits": _num(p[3]), "ap_pct": _num(p[4]), "garrisoned": p[5] == "true",
            "besieging": p[6] == "true", "stance": p[7], "acted": p[8] == "true",
            "region": (p[9] if p[9] not in ("nil", "") else None),
            "x": _num(p[10]), "y": _num(p[11])}


_LUA_PROVINCE = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(reg)s') if not s then return 'NO-SETT' end "
    "local p=g(s,'ProvinceContext') local m=g(s,'FactionProvinceManagerContext') local r=cm:get_region('%(reg)s') "
    "local slots=g(s,'BuildingSlotList') local free=0 "
    "if type(slots)=='table' then for i=1,#slots do local b=g(slots[i],'BuildingContext') "
    "if not b and g(slots[i],'IsBuildingNew')~=true then free=free+1 end end end "
    "return ts(g(p,'Key'))..'|'..ts(g(p,'IsPlayerCompleteOwner'))..'|'..ts(g(s,'MaxBuildingSlotCount'))"
    "..'|'..free..'|'..ts(m and g(m,'CanSetInitiative'))"
    "..'|'..ts((function() local i=m and g(m,'SelectedInitiative') if i then return g(i,'Key') end return 'none' end)())"
    "..'|'..ts(r and r:get_active_edict_key())..'|'..ts(r and r:public_order())"
    "..'|'..ts(r and r:num_buildings())..'|'..ts(r and r:is_province_capital())")
# NB `settlement():population()` does NOT exist in WH3 (live-verified: "attempt to call method
# 'population' (a nil value)") -- num_buildings() is the settlement-size signal that does.


def province_state(bus, region):
    raw = str(_ev(bus, _LUA_PROVINCE % {"reg": region}, timeout=25.0))
    if raw == "NO-SETT":
        return {"region": region, "settlement_present": False}
    p = raw.split("|")
    if len(p) < 10:
        raise CollectError("province state malformed for %s: %r" % (region, p))
    return {"region": region, "settlement_present": True, "province": p[0],
            "complete_owner": p[1] == "true", "max_slots": _num(p[2]), "free_slots": _num(p[3]),
            "can_set_edict": p[4] == "true", "selected_edict": p[5], "active_edict": p[6],
            "public_order": _num(p[7]), "buildings": _num(p[8]), "is_capital": p[9] == "true"}


# ============================================================ STREAM 2: THE OFFER SETS
def _offer(atype, key, available, gate=None, **params):
    return {"action_type": atype, "key": key, "available": bool(available),
            "gate": gate, "params": params or {}}


def _reach_characters(bus, cqi, target_cqis):
    """{cqi: bool} in ONE eval. Uses the campaign_manager WRAPPER -- the raw model predicate
    returns FALSE POSITIVES when the character has no action points."""
    if not target_cqis:
        return {}
    return _flags(_ev(bus, "local a=cm:get_character_by_cqi(%s) local o={} "
                           "for t in string.gmatch('%s','[^,]+') do local c=cm:get_character_by_cqi(tonumber(t)) "
                           "local ok,v=pcall(function() return cm:character_can_reach_character(a,c) end) "
                           "o[#o+1]=t..'='..tostring(ok and v) end return table.concat(o,',')"
                      % (cqi, ",".join(str(t) for t in target_cqis)), timeout=30.0))


def _reach_settlements(bus, cqi, regions):
    """{region: bool} in ONE eval (same wrapper rule as above)."""
    if not regions:
        return {}
    return _flags(_ev(bus, "local a=cm:get_character_by_cqi(%s) local o={} "
                           "for t in string.gmatch('%s','[^,]+') do local r=cm:get_region(t) "
                           "local s=r and r:settlement() "
                           "local ok,v=pcall(function() return cm:character_can_reach_settlement(a,s) end) "
                           "o[#o+1]=t..'='..tostring(ok and v) end return table.concat(o,',')"
                      % (cqi, ",".join(regions)), timeout=30.0))


_LUA_STANCES = (_G +
    "local mf=g(cco('CcoCampaignCharacter','%(cqi)s'),'MilitaryForceContext');"
    "if not mf then return 'NO-FORCE' end local l=g(mf,'StanceList');"
    "if type(l)~='table' then return 'NO-LIST' end local o={} "
    "for i=1,#l do local v=l[i] o[#o+1]=ts(g(v,'Key'))..'~'..ts(g(v,'IsActive'))"
    "..'~'..ts(g(v,'CanBeActivated'))..'~'..ts(g(v,'CanAfford')) end return table.concat(o,',')")

_LUA_SKILLS = (_G + "local l=g(cco('CcoCampaignCharacter','%(cqi)s'),'SkillList');"
                    "if type(l)~='table' then return '' end local o={} "
                    "for i=1,#l do o[#o+1]=ts(g(l[i],'Key'))..'~'..ts(g(l[i],'Status')) end "
                    "return table.concat(o,',')")


def lord_offers(bus, cqi, state, world, stances_legal):
    """Every lord-context offer, available or not (unavailable ones carry the game's gate reason).

    MOVES cover EVERY enemy army, EVERY enemy settlement and EVERY own settlement the game reports,
    each with the engine's own reachability verdict -- no "nearest N" pre-selection here, because
    choosing between local targets is the advisor's job, not the recorder's.
    """
    offers = []
    acted = state.get("acted")
    # -- stances (the legality whitelist is the HUD stack, see legal_stances)
    raw = _ev(bus, _LUA_STANCES % {"cqi": cqi}, timeout=20.0)
    for row in str(raw or "").split(","):
        p = row.split("~")
        if len(p) < 4:
            continue
        key, active, can_act, afford = p[0], p[1] == "true", p[2] == "true", p[3] == "true"
        legal = stances_legal.get(key) not in (None, "inactive")
        ok = bool(can_act and afford and legal and not active)
        gate = None if ok else ("active" if active else
                                "not_in_legality_whitelist" if not legal else "cannot_activate")
        offers.append(_offer("stance", key, ok, gate, active=active))
    # -- recruitable units (units_panel must be open; an empty list is a legitimate answer)
    for c in recruitable_units(bus):
        offers.append(_offer("recruit_unit", c["key"], c.get("state") == "active",
                             None if c.get("state") == "active" else c.get("state")))
    # -- skills
    has_pts = (state.get("skill_points") or 0) >= 1
    for row in str(_ev(bus, _LUA_SKILLS % {"cqi": cqi}, timeout=25.0, allow_nil=True) or "").split(","):
        if "~" not in row:
            continue
        key, status = row.rsplit("~", 1)
        ok = (status == "active" and has_pts)
        offers.append(_offer("skills", key, ok, None if ok else ("no_points" if not has_pts else status)))
    # -- MOVES: attack armies / attack settlements / garrison
    armies = [h for h in world["hostiles"] if h.get("kind") == "army" and h.get("cqi")]
    reach_c = _reach_characters(bus, cqi, [a["cqi"] for a in armies])
    for a in armies:
        ok = bool(reach_c.get(str(a["cqi"]))) and not acted
        offers.append(_offer("attack_army", "cqi:%s" % a["cqi"], ok,
                             None if ok else ("already_acted_this_turn" if acted else "cannot_reach"),
                             target_cqi=a["cqi"], target_faction=a.get("faction"),
                             x=a.get("x"), y=a.get("y")))
    esetts = [h for h in world["hostiles"] if h.get("kind") == "settlement" and h.get("region")]
    osetts = [s for s in world["settlements"] if s.get("region")]
    reach_s = _reach_settlements(bus, cqi,
                                 [s["region"] for s in esetts] + [s["region"] for s in osetts])
    for s in esetts:
        ok = bool(reach_s.get(s["region"])) and not acted
        offers.append(_offer("attack_settlement", s["region"], ok,
                             None if ok else ("already_acted_this_turn" if acted else "cannot_reach"),
                             target_faction=s.get("faction"), x=s.get("x"), y=s.get("y")))
    garrisoned = state.get("garrisoned")
    for s in osetts:
        ok = bool(reach_s.get(s["region"])) and not garrisoned
        gate = None if ok else ("already_in_settlement" if garrisoned else "cannot_reach")
        offers.append(_offer("garrison", "settlement:%s" % s["region"], ok, gate,
                             x=s.get("x"), y=s.get("y")))
    if garrisoned:
        offers.append(_offer("leave_garrison", "leave", True, None,
                             x=state.get("x"), y=state.get("y")))
    offers.append(_offer("noop", "noop", True))
    return offers


def province_offers(bus, region, state, campaign):
    """Every province-context offer (buildings across free slots, edicts, lord recruitment)."""
    offers = []
    raw = _ev(bus, _G +
              "local s=cco('CcoCampaignSettlement','settlement:%s') local slots=g(s,'BuildingSlotList') "
              "if type(slots)~='table' then return '' end local o={} "
              "for i=1,#slots do local sl=slots[i] local b=g(sl,'BuildingContext') "
              "if not b and g(sl,'IsBuildingNew')~=true then local p=g(sl,'PossibleUpgradeWithoutConversionsList') "
              "if type(p)=='table' then for j=0,#p-1 do "
              "o[#o+1]=ts(g(sl,'Index'))..'~'..ts(g(sl,'PossibleUpgradeWithoutConversionsList['..j..'].Key'))"
              "..'~'..ts(g(sl,'BuildingRequirementsMet(PossibleUpgradeWithoutConversionsList['..j..'])')) end end end end "
              "return table.concat(o,',')" % region, timeout=30.0, allow_nil=True)
    seen = set()
    for row in str(raw or "").split(","):
        p = row.split("~")
        if len(p) < 3:
            continue
        slot, key, met = p[0], p[1], p[2] == "true"
        if key in seen:
            continue
        seen.add(key)
        offers.append(_offer("building", key, met, None if met else "requirements_not_met",
                             slot_index=int(float(slot)) if slot not in ("nil", "") else None))
    # -- edicts: the province must be FULLY OWNED for the commandment stack to exist at all.
    # Live-proven: on a partly-owned province InitiativeList still lists the 5 edict records, but the
    # HUD stack has no buttons and nothing can be clicked -- offering them would hand the advisor
    # options it can never take.
    complete = bool(state.get("complete_owner"))
    sel = state.get("selected_edict")
    for key in edict_options(bus, region):
        ok = complete and key != sel
        offers.append(_offer("edict", key, ok,
                             None if ok else ("province_not_complete" if not complete else "already_selected")))
    # -- lord recruitment: the pool is read data-side (no panel needed) via DatabaseRecordContext
    for sub in _lord_subtypes(campaign["faction"]):
        n, can = _lord_pool(bus, campaign["faction_cqi"], sub)
        for i in range(n):
            ok = bool(can[i]) if i < len(can) else False
            offers.append(_offer("recruit_lord", sub, ok, None if ok else "cannot_recruit_character",
                                 candidate_index=i))
    offers.append(_offer("noop", "noop", True))
    return offers


def _lord_subtypes(faction):
    parts = str(faction).split("_")
    race = parts[2] if len(parts) > 2 else "hef"
    return ["wh2_main_%s_prince" % race, "wh2_main_%s_princess" % race,
            "wh2_main_%s_archmage" % race, "wh2_main_%s_sea_helm" % race]


def _lord_pool(bus, faction_cqi, subtype):
    """(pool_size, [CanRecruitCharacter...]) for a lord subtype -- data-side, no panel."""
    expr = ("CharacterRecruitmentPoolEntriesForAgentSubtype("
            "DatabaseRecordContext(\"CcoAgentSubtypeRecord\",\"%s\"))" % subtype)
    raw = _ev(bus, _G + "local f=cco('CcoCampaignFaction','%s') "
                        "local ok,n=pcall(function() return f:Call('%s.Size') end) "
                        "if not ok or not n or n==0 then return '0' end local o={} "
                        "for i=0,n-1 do o[#o+1]=ts(f:Call('%s['..i..'].CanRecruitCharacter')) end "
                        "return n..':'..table.concat(o,',')" % (faction_cqi, expr, expr), timeout=25.0)
    s = str(raw or "0")
    if ":" not in s:
        return 0, []
    n, flags = s.split(":", 1)
    try:
        return int(float(n)), [f == "true" for f in flags.split(",")]
    except ValueError:
        return 0, []


# ⚠ CanResearch is the GAME'S OWN verdict and the only correct availability test. IsResearched
# alone is not: it ignores PREREQUISITES, so every locked tier-2..5 node reads "available" and
# StartResearching then silently refuses. Live-verified on a real faction: of 73 techs, 72 have
# CanResearch=false and exactly ONE is researchable -- the old test offered 75.
_LUA_TECH = (_G + "local m=g(cco('CcoCampaignFaction','%(fac)s'),'TechnologyManagerContext');"
                  "if not m then return '' end local l=g(m,'TechnologyList');"
                  "if type(l)~='table' then return '' end local o={} "
                  "for i=1,#l do o[#o+1]=ts(g(l[i],'NodeKey'))..'~'..ts(g(l[i],'IsResearched'))"
                  "..'~'..ts(g(l[i],'CanResearch')) end return table.concat(o,',')")

_LUA_RITES = (_G + "local l=g(cco('CcoCampaignFaction','%(fac)s'),'AvailableRitualList');"
                   "if type(l)~='table' then return '' end local o={} "
                   "for i=1,#l do o[#o+1]=ts(g(l[i],'CanPerformRitual')) end return table.concat(o,',')")


def campaign_offers(bus, campaign):
    """Faction-wide offers: research, rites, and END TURN.

    end_turn is a first-class prediction, not the loop's own act: the turn ends when the advisor
    ranks end_turn top, which is why it has to be in the offer set like everything else.
    """
    offers = []
    fac = campaign["faction_cqi"]
    researching = campaign["is_researching"]
    for row in str(_ev(bus, _LUA_TECH % {"fac": fac}, timeout=30.0, allow_nil=True) or "").split(","):
        p = row.split("~")
        if len(p) < 3:
            continue
        key, done, can = p[0], p[1] == "true", p[2] == "true"
        ok = can and not researching
        gate = None if ok else ("already_researching" if researching else
                                "researched" if done else "prerequisites_not_met")
        offers.append(_offer("research", key, ok, gate))
    for i, flag in enumerate(str(_ev(bus, _LUA_RITES % {"fac": fac}, timeout=25.0,
                                     allow_nil=True) or "").split(",")):
        if flag not in ("true", "false"):
            continue
        offers.append(_offer("rites", "rite_index_%d" % (i + 1), flag == "true",
                             None if flag == "true" else "cannot_perform", rite_index=i + 1))
    offers.append(_offer("end_turn", "end_turn", True))
    offers.append(_offer("noop", "noop", True))
    return offers


# ============================================================ THE ONE CALL THE ADVISOR ASKS FOR
def snapshot(bus, active=None):
    """EVERYTHING the advisor needs to rank the whole faction, read at ONE instant.

    Returns
        {ts, campaign{...}, world{armies,settlements,hostiles},
         entities: [{context_kind, context_id, state{...}, offers[...]}, ...]}

    `active` optionally restricts the sweep to entities still in play this turn:
        {"lords": [cqi, ...], "regions": [region_key, ...], "campaign": True}
    """
    camp = campaign_state(bus)
    world = world_state(bus)
    stances_legal = legal_stances(bus)
    lords = [str(c.get("cqi")) for c in world["armies"] if c.get("has_army") and c.get("is_general")]
    regions = [s["region"] for s in world["settlements"] if s.get("region")]
    want_camp = True
    if active is not None:
        lords = [c for c in lords if c in set(str(x) for x in (active.get("lords") or []))]
        regions = [r for r in regions if r in set(active.get("regions") or [])]
        want_camp = bool(active.get("campaign", True))
    ents = []
    for cqi in lords:
        st = lord_state(bus, cqi)
        ents.append({"context_kind": "lord", "context_id": str(cqi), "state": st,
                     "offers": lord_offers(bus, cqi, st, world, stances_legal)})
    for reg in regions:
        st = province_state(bus, reg)
        ents.append({"context_kind": "province", "context_id": reg, "state": st,
                     "offers": province_offers(bus, reg, st, camp)})
    if want_camp:
        ents.append({"context_kind": "campaign", "context_id": camp["faction"], "state": dict(camp),
                     "offers": campaign_offers(bus, camp)})
    return {"ts": time.time(), "campaign": camp, "world": world,
            "stances_legal": stances_legal, "entities": ents}


if __name__ == "__main__":
    from bus import Bus
    import json
    b = Bus()
    print("target_row:", json.dumps(target_row(b)))
    t0 = time.time()
    snap = snapshot(b)
    tot = sum(len(e["offers"]) for e in snap["entities"])
    av = sum(1 for e in snap["entities"] for o in e["offers"] if o["available"])
    print("snapshot: turn %s | %d entities | %d offers (%d available) | %d legal stances | %.1fs"
          % (snap["campaign"]["turn"], len(snap["entities"]), tot, av,
             len(snap["stances_legal"]), time.time() - t0))
    print("world: %d armies, %d settlements, %d hostiles"
          % (len(snap["world"]["armies"]), len(snap["world"]["settlements"]),
             len(snap["world"]["hostiles"])))
    for e in snap["entities"]:
        print("  %-9s %-28s state=%-2d offers=%-4d avail=%d"
              % (e["context_kind"], str(e["context_id"])[:28], len(e["state"]), len(e["offers"]),
                 sum(1 for o in e["offers"] if o["available"])))
        for o in [o for o in e["offers"] if o["available"]][:5]:
            print("      + %-18s %-40s %s" % (o["action_type"], str(o["key"])[:40], o["params"]))
