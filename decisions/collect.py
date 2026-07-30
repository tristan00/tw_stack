r"""collect.py -- the v7 recorder's DATA COLLECTION primitives.

Implements the three read-side calls the advisor loop needs (see V7_DESIGN.md):

  1. target_row(bus)                      once per turn (end of turn) -> the REWARD inputs
  2. context_features(bus, kind, entity)  campaign block + the one relevant context block
  3. available_actions(bus, kind, entity) the full offer set, incl. unavailable options + reason

Design rules carried over from the executor work (all learned the hard way):
  * The bus command line is "<seq> <channel> <payload>" -> EVAL PAYLOADS MUST BE ONE LINE.
  * A nil/empty result proves nothing on its own; every query here is shaped so a broken chain
    is distinguishable from a genuinely empty option set (errors raise, empties return []).
  * Option sets are produced by the SAME enumerators the executors use, so what the advisor is
    offered is exactly what the launcher can execute.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

import cco_actions as CCO          # noqa: E402  engine + cco enumerators/helpers
import cm_actions as CM            # noqa: E402  hostiles/setts helpers
import click_actions as CLICK      # noqa: E402  edict/recruit enumerators

_G = CCO._G


class CollectError(RuntimeError):
    """A collection chain broke (bus miss / nil context). Loud by design -- never silently empty."""


def _ev(bus, lua, timeout=20.0):
    v = CCO._ev(bus, lua, timeout=timeout)
    if v is None:
        raise CollectError("eval returned nil: %s" % lua[:90])
    return v


# ============================================================ 1. TARGET ROW (once per turn)
# The FW-6 reward inputs in ONE eval. Same reward as v6, focused collection.
#
# POWER RANK: use the GAME'S OWN metric -- cco CcoCampaignFaction.StrengthRank ("Returns the rank
# amongst all current active factions in terms of strength"). This is the exact number the
# diplomacy panel prints as "Strength Rank", and it is a single free property read, replacing v6's
# expensive sweep over every faction's forces (and replacing a hand-rolled unit-count proxy, which
# disagreed with the game outright).
# ⚠ The diplomacy PANEL caches its value at open time, so a UI reading can lag the live property by
# a few ranks (observed: panel 120 vs live 144 for us; 262 vs 263, 32 vs 37, 26 vs 32 for others).
# That is fine for the reward as long as we always sample at the SAME point in the turn cycle --
# which is why this is called once, at end of turn.
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
    raw = str(_ev(bus, _LUA_TARGET, timeout=60.0))
    p = raw.split("|")
    if len(p) < 7:
        raise CollectError("target row malformed: %r" % raw[:120])
    def num(x):
        try:
            return float(x)
        except ValueError:
            return None
    return {"campaign_id": p[0], "turn": num(p[1]), "income": num(p[2]), "settlements": num(p[3]),
            "allies": num(p[4]), "vassals": num(p[5]), "power_rank": num(p[6]), "ts": time.time()}


# ============================================================ 2. CONTEXT FEATURES
_LUA_CAMPAIGN = ("local f=cm:get_local_faction(true) "
                 "return f:name()..'|'..cm:model():turn_number()..'|'..f:income()..'|'"
                 "..f:region_list():num_items()..'|'..cm:get_faction(cm:get_local_faction_name(true)):treasury()")


def campaign_features(bus):
    p = str(_ev(bus, _LUA_CAMPAIGN)).split("|")
    if len(p) < 5:
        raise CollectError("campaign features malformed: %r" % p)
    def num(x):
        try:
            return float(x)
        except ValueError:
            return None
    return {"faction": p[0], "turn": num(p[1]), "income": num(p[2]),
            "settlements": num(p[3]), "treasury": num(p[4])}


_LUA_LORD = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') if not c then return 'NO-CHAR' end "
    "local mf=g(c,'MilitaryForceContext') local ch=cm:get_character_by_cqi(%(cqi)s) "
    "local pend=g(mf,'PendingRecruitmentUnitList') "
    "return ts(g(c,'Rank'))..'|'..ts(g(c,'SkillPointsAvailable'))..'|'..ts(mf and g(mf,'UnitCount'))"
    "..'|'..ts(type(pend)=='table' and #pend or -1)..'|'..ts(g(c,'ActionPointPercent'))"
    "..'|'..ts(mf and g(mf,'IsGarrisoned'))..'|'..ts(ch and ch:is_besieging())"
    "..'|'..ts((function() local l=g(mf,'StanceList') if type(l)=='table' then for i=1,#l do "
    "if g(l[i],'IsActive')==true then return g(l[i],'Key') end end end return 'none' end)())")


def lord_features(bus, cqi):
    p = str(_ev(bus, _LUA_LORD % {"cqi": cqi}, timeout=25.0)).split("|")
    if len(p) < 8:
        raise CollectError("lord features malformed for %s: %r" % (cqi, p))
    def num(x):
        try:
            return float(x)
        except ValueError:
            return None
    out = {"rank": num(p[0]), "skill_points": num(p[1]), "unit_count": num(p[2]),
           "pending_recruits": num(p[3]), "ap_pct": num(p[4]),
           "is_garrisoned": p[5] == "true", "is_besieging": p[6] == "true", "stance_current": p[7]}
    ea = CM.nearest_enemy_army(bus)
    es = CM.nearest_enemy_settlement(bus)
    own = CM.nearest_own_settlement(bus, cqi)
    out["enemy_army_dist"] = (ea or {}).get("dist")
    out["enemy_settlement_dist"] = (es or {}).get("dist")
    out["own_settlement_region"] = (own or {}).get("region")
    return out


_LUA_PROVINCE = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(reg)s') if not s then return 'NO-SETT' end "
    "local p=g(s,'ProvinceContext') local m=g(s,'FactionProvinceManagerContext') local r=cm:get_region('%(reg)s') "
    "local slots=g(s,'BuildingSlotList') local free=0 "
    "if type(slots)=='table' then for i=1,#slots do local b=g(slots[i],'BuildingContext') "
    "if not b and g(slots[i],'IsBuildingNew')~=true then free=free+1 end end end "
    "return ts(g(p,'Key'))..'|'..ts(g(p,'IsPlayerCompleteOwner'))..'|'..ts(g(s,'MaxBuildingSlotCount'))"
    "..'|'..free..'|'..ts(m and g(m,'CanSetInitiative'))"
    "..'|'..ts((function() local i=m and g(m,'SelectedInitiative') if i then return g(i,'Key') end return 'none' end)())"
    "..'|'..ts(r and r:get_active_edict_key())")


def province_features(bus, region):
    p = str(_ev(bus, _LUA_PROVINCE % {"reg": region}, timeout=25.0)).split("|")
    if len(p) < 7:
        raise CollectError("province features malformed for %s: %r" % (region, p))
    def num(x):
        try:
            return float(x)
        except ValueError:
            return None
    return {"region": region, "province": p[0], "province_complete": p[1] == "true",
            "max_slots": num(p[2]), "free_slots": num(p[3]), "can_set_edict": p[4] == "true",
            "selected_edict": p[5], "active_edict": p[6]}


def context_features(bus, kind, entity_id=None):
    """Campaign block + the one context block the decision needs (V7_DESIGN.md §2)."""
    feats = campaign_features(bus)
    if kind == "lord":
        feats.update(lord_features(bus, entity_id))
    elif kind == "province":
        feats.update(province_features(bus, entity_id))
    elif kind == "campaign":
        feats["is_researching"] = _ev(bus, "return tostring(cm:get_local_faction(true)"
                                           ":is_currently_researching())") == "true"
    else:
        raise CollectError("unknown context kind %r" % kind)
    feats["context_kind"] = kind
    feats["context_id"] = str(entity_id) if entity_id is not None else feats["faction"]
    return feats


# ============================================================ 3. AVAILABLE ACTIONS
def _offer(atype, key, available, gate=None, **params):
    return {"action_type": atype, "key": key, "available": bool(available),
            "gate": gate, "params": params or {}}


def entities(bus):
    """The advisor's iteration sets: lords (generals) and owned regions."""
    lords, regions = [], []
    try:
        for c in (bus.send("chars", "", timeout=10) or {}).get("chars") or []:
            if c.get("has_army") and c.get("is_general"):
                lords.append({"cqi": c.get("cqi"), "subtype": c.get("subtype"),
                              "ap_pct": c.get("ap_pct")})
        for s in (bus.send("setts", "", timeout=10) or {}).get("setts") or []:
            if s.get("region"):
                regions.append(s["region"])
    except Exception as e:
        raise CollectError("entities: %s" % repr(e)[:80])
    return {"lords": lords, "regions": regions}


def lord_actions(bus, cqi, stance_whitelist=None):
    """Every lord-context offer, available or not (unavailable ones carry a gate reason)."""
    offers = []
    # stances -- legality whitelist is MANDATORY (engine will set faction-illegal stances)
    wl = stance_whitelist or set()
    st = CCO._stances(bus, cqi) or {}
    for key, s in st.items():
        legal = key in wl
        ok = bool(s["can_activate"] and s["can_afford"] and legal and not s["active"])
        gate = None if ok else ("active" if s["active"] else
                                "not_in_legality_whitelist" if not legal else "cannot_activate")
        offers.append(_offer("stance", key, ok, gate))
    # recruitable units (requires units_panel open; empty is a legitimate answer)
    for c in CLICK.recruitable_units(bus):
        offers.append(_offer("recruit_unit", c["key"], c.get("state") == "active",
                             None if c.get("state") == "active" else c.get("state")))
    # skills
    raw = CCO._ev(bus, (CCO._LUA_SKILLS % {"cqi": cqi}) +
                  "local o={} for i=1,#l do o[#o+1]=ts(g(l[i],'Key'))..'~'..ts(g(l[i],'Status')) end "
                  "return table.concat(o,',')", timeout=25.0)
    pts = CCO._ev(bus, _G + "return ts(g(cco('CcoCampaignCharacter','%s'),'SkillPointsAvailable'))" % cqi)
    try:
        has_pts = float(pts or 0) >= 1
    except (TypeError, ValueError):
        has_pts = False
    for row in str(raw or "").split(","):
        if "~" not in row:
            continue
        key, status = row.rsplit("~", 1)
        ok = (status == "active" and has_pts)
        offers.append(_offer("skills", key, ok, None if ok else ("no_points" if not has_pts else status)))
    # attacks + garrison (targets from the bus; reachability is gated at execute time)
    ea = CM.nearest_enemy_army(bus)
    if ea:
        offers.append(_offer("attack_army", "cqi:%s" % ea["cqi"], True, None,
                             target_cqi=ea["cqi"], dist=ea.get("dist")))
    es = CM.nearest_enemy_settlement(bus)
    if es:
        offers.append(_offer("attack_settlement", es["region"], True, None, dist=es.get("dist")))
    own = CM.nearest_own_settlement(bus, cqi)
    if own:
        offers.append(_offer("garrison", "settlement:%s" % own["region"], True, None,
                             x=own.get("x"), y=own.get("y")))
    offers.append(_offer("noop", "noop", True))
    return offers


def province_actions(bus, region):
    """Every province-context offer (buildings across free slots, edicts, lord recruitment)."""
    offers = []
    treasury = campaign_features(bus)["treasury"]
    raw = CCO._ev(bus, _G +
                  "local s=cco('CcoCampaignSettlement','settlement:%s') local slots=g(s,'BuildingSlotList') "
                  "if type(slots)~='table' then return '' end local o={} "
                  "for i=1,#slots do local sl=slots[i] local b=g(sl,'BuildingContext') "
                  "if not b and g(sl,'IsBuildingNew')~=true then local p=g(sl,'PossibleUpgradeWithoutConversionsList') "
                  "if type(p)=='table' then for j=0,#p-1 do "
                  "o[#o+1]=ts(g(sl,'Index'))..'~'..ts(g(sl,'PossibleUpgradeWithoutConversionsList['..j..'].Key'))"
                  "..'~'..ts(g(sl,'BuildingRequirementsMet(PossibleUpgradeWithoutConversionsList['..j..'])')) end end end end "
                  "return table.concat(o,',')" % region, timeout=30.0)
    seen = set()
    for row in str(raw or "").split(","):
        parts = row.split("~")
        if len(parts) < 3:
            continue
        slot, key, met = parts[0], parts[1], parts[2] == "true"
        if key in seen:
            continue
        seen.add(key)
        offers.append(_offer("building", key, met, None if met else "requirements_not_met",
                             slot_index=int(float(slot)) if slot not in ("nil", "") else None))
    # edicts
    sel = CLICK._selected_edict(bus, region)
    for key in CLICK.edict_options(bus, region):
        ok = key != sel
        offers.append(_offer("edict", key, ok, None if ok else "already_selected"))
    # lord recruitment -- pool read data-side (no UI needed) via DatabaseRecordContext
    for sub in _lord_subtypes(bus):
        n, can = _lord_pool(bus, sub)
        for i in range(n):
            offers.append(_offer("recruit_lord", sub, bool(can[i]) if i < len(can) else False,
                                 None, candidate_index=i))
    offers.append(_offer("noop", "noop", True))
    return offers


def _lord_subtypes(bus):
    """Lord subtypes the faction can raise (UI type buttons name them; keys are the DB subtypes)."""
    fac = str(CCO._ev(bus, "return cm:get_local_faction_name(true)") or "")
    race = fac.split("_")[2] if len(fac.split("_")) > 2 else "hef"
    return ["wh2_main_%s_prince" % race, "wh2_main_%s_princess" % race,
            "wh2_main_%s_archmage" % race, "wh2_main_%s_sea_helm" % race]


def _lord_pool(bus, subtype):
    """(pool_size, [CanRecruitCharacter...]) for a lord subtype -- data-side, no panel."""
    expr = ("CharacterRecruitmentPoolEntriesForAgentSubtype("
            "DatabaseRecordContext(\"CcoAgentSubtypeRecord\",\"%s\"))" % subtype)
    raw = CCO._ev(bus, _G + "local f=cco('CcoCampaignFaction','%s') "
                            "local ok,n=pcall(function() return f:Call('%s.Size') end) "
                            "if not ok or not n or n==0 then return '0' end local o={} "
                            "for i=0,n-1 do o[#o+1]=ts(f:Call('%s['..i..'].CanRecruitCharacter')) end "
                            "return n..':'..table.concat(o,',')"
                  % (CCO._faction_cqi(bus), expr, expr), timeout=25.0)
    s = str(raw or "0")
    if ":" not in s:
        return 0, []
    n, flags = s.split(":", 1)
    try:
        return int(float(n)), [f == "true" for f in flags.split(",")]
    except ValueError:
        return 0, []


def campaign_actions(bus):
    """Campaign-context offers: research + rites (+ noop)."""
    offers = []
    fac = CCO._faction_cqi(bus)
    researching = CCO._ev(bus, "return tostring(cm:get_local_faction(true):is_currently_researching())")
    raw = CCO._ev(bus, (CCO._LUA_TECH % {"fac": fac}) +
                  "local o={} for i=1,#l do o[#o+1]=ts(g(l[i],'NodeKey'))..'~'..ts(g(l[i],'IsResearched')) end "
                  "return table.concat(o,',')", timeout=30.0)
    for row in str(raw or "").split(","):
        if "~" not in row:
            continue
        key, done = row.rsplit("~", 1)
        ok = (done != "true" and researching != "true")
        offers.append(_offer("research", key, ok,
                             None if ok else ("already_researching" if researching == "true" else "researched")))
    rites = CCO._ev(bus, (CCO._LUA_RITES % {"fac": fac}) +
                    "local o={} for i=1,#l do o[#o+1]=ts(g(l[i],'CanPerformRitual')) end "
                    "return table.concat(o,',')", timeout=25.0)
    for i, flag in enumerate(str(rites or "").split(",")):
        if flag not in ("true", "false"):
            continue
        offers.append(_offer("rites", "rite_index_%d" % (i + 1), flag == "true",
                             None if flag == "true" else "cannot_perform", rite_index=i + 1))
    offers.append(_offer("noop", "noop", True))
    return offers


def available_actions(bus, kind, entity_id=None, stance_whitelist=None):
    if kind == "lord":
        return lord_actions(bus, entity_id, stance_whitelist)
    if kind == "province":
        return province_actions(bus, entity_id)
    if kind == "campaign":
        return campaign_actions(bus)
    raise CollectError("unknown context kind %r" % kind)


if __name__ == "__main__":
    from bus import Bus
    b = Bus()
    import json
    print("target_row:", json.dumps(target_row(b)))
    ents = entities(b)
    print("entities:", json.dumps(ents))
    print("campaign features:", json.dumps(context_features(b, "campaign")))
    acts = available_actions(b, "campaign")
    print("campaign actions: %d (%d available)" % (len(acts), sum(1 for a in acts if a["available"])))
    if ents["regions"]:
        reg = ents["regions"][0]
        print("province features:", json.dumps(context_features(b, "province", reg)))
        pa = available_actions(b, "province", reg)
        print("province actions: %d (%d available)" % (len(pa), sum(1 for a in pa if a["available"])))
        for a in pa[:6]:
            print("   ", a["action_type"], a["key"], a["available"], a["gate"])
    if ents["lords"]:
        cqi = ents["lords"][0]["cqi"]
        print("lord features:", json.dumps(context_features(b, "lord", cqi)))
        la = available_actions(b, "lord", cqi)
        print("lord actions: %d (%d available)" % (len(la), sum(1 for a in la if a["available"])))
