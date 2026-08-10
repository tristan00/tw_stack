from __future__ import annotations

import sys

_T = 15.0

_FS, _RS, _SS = "\x1f", "\x1e", "\x1d"

_G = ("local function g(c,p) local ok,v=pcall(function() return c:Call(p) end);"
      "if ok and v~=nil then return v end return nil end "
      "local function ts(v) return tostring(v) end ")


class CcoQueryError(RuntimeError):
    pass


def _ev(bus, lua, timeout=_T):
    try:
        r = bus.send("eval", lua, timeout=timeout) or {}
    except Exception as e:
        raise CcoQueryError("bus eval failed: %s" % repr(e)[:120])
    if r.get("error"):
        raise CcoQueryError("lua error: %s" % str(r["error"])[:200])
    if r.get("result") is None:
        raise CcoQueryError("nil result (chain broke; lua head: %s)" % lua[:80])
    return r["result"]


def _b(v):
    return True if v == "true" else False if v == "false" else None


def list_entities(bus):
    lua = (
        "local f=cm:get_local_faction(true); if not f then return 'NO-FACTION' end "
        "local out={} local cl=f:character_list() "
        "for i=0,cl:num_items()-1 do local c=cl:item_at(i) "
        "  if c:has_military_force() and not c:military_force():is_armed_citizenry() then "
        "    out[#out+1]='L'..'%s'..ts(c:command_queue_index())..'%s'..ts(c:get_forename())"
        "    ..'%s'..ts(c:cqi()==f:faction_leader():cqi()) end end "
        "local rl=f:region_list() "
        "for i=0,rl:num_items()-1 do out[#out+1]='R'..'%s'..rl:item_at(i):name() end "
        "return table.concat(out,'%s')"
    ) % (_FS, _FS, _FS, _FS, _RS)
    lua = "local function ts(v) return tostring(v) end " + lua
    raw = _ev(bus, lua)
    if raw == "NO-FACTION":
        raise CcoQueryError("no local faction (campaign not loaded?)")
    lords, regions = [], []
    for rec in str(raw).split(_RS):
        parts = rec.split(_FS)
        if parts[0] == "L" and len(parts) >= 4:
            lords.append({"cqi": int(float(parts[1])), "name": parts[2],
                          "is_leader": parts[3] == "true"})
        elif parts[0] == "R" and len(parts) >= 2:
            regions.append(parts[1])
    return {"lords": lords, "regions": regions}


def settlement_actions(bus, region):
    lua = (_G +
        "local s=cco('CcoCampaignSettlement','settlement:%s');"
        "if not s then return 'NO-CTX' end "
        "local slots=s:Call('BuildingSlotList');"
        "if type(slots)~='table' then return 'NO-SLOTLIST' end "
        "local out={} "
        "for i,sl in ipairs(slots) do "
        "  local rec={'S', ts(g(sl,'Index')), ts(g(sl,'SlotActivateLevel')), ts(g(sl,'IsBuildingNew'))} "
        "  local built=g(sl,'BuildingContext'); rec[#rec+1]=built and ts(g(built,'BuildingLevelRecordContext.Key')) or '' "
        "  local p=g(sl,'PossibleUpgradeWithoutConversionsList') "
        "  local ps={} "
        "  if type(p)=='table' then for j=0,#p-1 do "
        "    ps[#ps+1]=ts(g(sl,'PossibleUpgradeWithoutConversionsList['..j..'].Key'))"
        "    ..'~'..ts(g(sl,'BuildingRequirementsMet(PossibleUpgradeWithoutConversionsList['..j..'])')) end end "
        "  rec[#rec+1]=table.concat(ps,';') "
        "  out[#out+1]=table.concat(rec,'%s') "
        "end "
        "local mgr=g(s,'FactionProvinceManagerContext') "
        "local ed={'E'} "
        "if mgr then "
        "  ed[#ed+1]=ts(g(mgr,'CanSetInitiative')) "
        "  local ins=g(mgr,'InstalledInitiative') "
        "  ed[#ed+1]=ins and ts(g(ins,'RecordContext.Key') or g(ins,'Key')) or '' "
        "  local il=g(mgr,'InitiativeList') local ks={} "
        "  if type(il)=='table' then for i,v in ipairs(il) do "
        "    ks[#ks+1]=ts(g(v,'RecordContext.Key') or g(v,'Key')) end end "
        "  ed[#ed+1]=table.concat(ks,';') "
        "else ed[#ed+1]='NO-MGR' end "
        "out[#out+1]=table.concat(ed,'%s') "
        "return table.concat(out,'%s')"
    ) % (region, _FS, _FS, _RS)
    raw = _ev(bus, lua, timeout=25.0)
    if raw in ("NO-CTX", "NO-SLOTLIST"):
        raise CcoQueryError("settlement chain broke for %s: %s" % (region, raw))
    slots, edicts = [], None
    for rec in str(raw).split(_RS):
        parts = rec.split(_FS)
        if parts[0] == "S" and len(parts) >= 6:
            possibles = []
            if parts[5]:
                for p in parts[5].split(";"):
                    if "~" in p:
                        k, rm = p.rsplit("~", 1)
                        possibles.append({"key": k, "req_met": _b(rm)})
            slots.append({"index": int(float(parts[1])) if parts[1] != "nil" else None,
                          "activate_level": int(float(parts[2])) if parts[2] != "nil" else None,
                          "is_building_new": _b(parts[3]),
                          "building": parts[4] or None,
                          "possibles": possibles})
        elif parts[0] == "E":
            if len(parts) >= 2 and parts[1] == "NO-MGR":
                edicts = {"error": "no province manager context"}
            elif len(parts) >= 4:
                edicts = {"can_set": _b(parts[1]), "installed": parts[2] or None,
                          "options": [k for k in parts[3].split(";") if k and k != "nil"]}
    if not slots:
        raise CcoQueryError("settlement %s: zero slots parsed from %r" % (region, str(raw)[:120]))
    return {"region": region, "slots": slots, "edicts": edicts}


def lord_actions(bus, char_cqi):
    lua = (_G +
        "local ch=cco('CcoCampaignCharacter','%s');"
        "if not ch then return 'NO-CTX' end "
        "local app=ts(g(ch,'ActionPointPercent')) "
        "local mf=g(ch,'MilitaryForceContext');"
        "if not mf then return 'NO-FORCE'..'%s'..app end "
        "local out={} "
        "local l=g(mf,'StanceList') "
        "if type(l)=='table' then for i,v in ipairs(l) do "
        "  out[#out+1]='T'..'%s'..ts(g(v,'Key'))..'%s'..ts(g(v,'IsActive'))"
        "  ..'%s'..ts(g(v,'CanBeActivated'))..'%s'..ts(g(v,'CanAfford')) end end "
        "local pend=g(mf,'PendingRecruitmentUnitList') "
        "out[#out+1]='F'..'%s'..ts(g(mf,'UnitCount'))..'%s'..ts(type(pend)=='table' and #pend or 0)..'%s'..app "
        "return table.concat(out,'%s')"
    ) % (char_cqi, _FS, _FS, _FS, _FS, _FS, _FS, _FS, _FS, _RS)
    raw = _ev(bus, lua, timeout=20.0)
    if raw == "NO-CTX":
        raise CcoQueryError("character ctx broke for cqi %s" % char_cqi)
    out = {"cqi": int(char_cqi), "stances": [], "unit_count": None,
           "pending_recruits": None, "action_point_pct": None}
    s = str(raw)
    if s.startswith("NO-FORCE"):
        parts = s.split(_FS)
        out["action_point_pct"] = None if len(parts) < 2 or parts[1] == "nil" else float(parts[1])
        out["no_force"] = True
        return out
    for rec in s.split(_RS):
        parts = rec.split(_FS)
        if parts[0] == "T" and len(parts) >= 5:
            out["stances"].append({"key": parts[1], "active": _b(parts[2]),
                                   "can_activate": _b(parts[3]), "can_afford": _b(parts[4])})
        elif parts[0] == "F" and len(parts) >= 4:
            out["unit_count"] = None if parts[1] == "nil" else int(float(parts[1]))
            out["pending_recruits"] = None if parts[2] == "nil" else int(float(parts[2]))
            out["action_point_pct"] = None if parts[3] == "nil" else float(parts[3])
    if not out["stances"]:
        raise CcoQueryError("lord %s: zero stances parsed from %r" % (char_cqi, s[:120]))
    return out


if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import common
    sys.path.insert(0, common.BUS)
    from bus import Bus
    b = Bus()
    ents = list_entities(b)
    print("entities:", ents)
    if ents["regions"]:
        sa = settlement_actions(b, ents["regions"][0])
        print("settlement_actions(%s):" % ents["regions"][0])
        for sl in sa["slots"]:
            print("  slot", sl["index"], "built=", sl["building"], "new=", sl["is_building_new"],
                  "n_possible=", len(sl["possibles"]))
        print("  edicts:", sa["edicts"])
    if ents["lords"]:
        la = lord_actions(b, ents["lords"][0]["cqi"])
        print("lord_actions(%s):" % ents["lords"][0]["cqi"], la)
