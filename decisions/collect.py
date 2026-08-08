from __future__ import annotations

import collections
import math
import random
import re
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")

MOVEMENT_STANCES = frozenset((
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DOUBLE_TIME",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_SET_CAMP_RAIDING",
))
MOVE_SAMPLES = 8
MOVE_CANDIDATES = 16
MOVE_MIN_R = 3.0

_G = ("local function g(c,p) local ok,v=pcall(function() return c:Call(p) end);"
      "if ok and v~=nil then return v end return nil end "
      "local function ts(v) return tostring(v) end ")


class CollectError(RuntimeError):
    pass


def _ev(bus, lua, timeout=20.0, allow_nil=False):
    try:
        r = bus.send("eval", lua, timeout=timeout) or {}
    except Exception as e:
        raise CollectError("bus eval failed: %s" % repr(e)[:110])
    if r.get("error"):
        _e = str(r["error"])
        raise CollectError("lua error: ...%s" % _e[-260:] if len(_e) > 260 else
                           "lua error: %s" % _e)
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
    out = {}
    for part in str(raw or "").split(","):
        if "=" in part:
            k, v = part.rsplit("=", 1)
            out[k] = (v == "true")
    return out


_LUA_TARGET = (_G +
    "local f=cm:get_local_faction(true) local me=f:name() local fc=cco('CcoCampaignFaction',"
    "tostring(f:command_queue_index())) local allies=0 local vassals=0 "
    "local fl=cm:model():world():faction_list() "
    "for i=0,fl:num_items()-1 do local o=fl:item_at(i) if o:name()~=me then "
    "local ok1,al=pcall(function() return f:allied_with(o) end) if ok1 and al then allies=allies+1 end "
    "local ok2,va=pcall(function() return o:is_vassal_of(f) end) if ok2 and va then vassals=vassals+1 end end end "
    "return me..'|'..cm:model():turn_number()..'|'..f:income()..'|'..f:region_list():num_items()"
    "..'|'..allies..'|'..vassals..'|'..ts(g(fc,'StrengthRank'))"
    "..'|'..(function() local l=f:faction_leader() "
    "if not l or l:is_null_interface() then return 'nil' end "
    "local ok,v=pcall(function() return l:rank() end) return ts(ok and v or nil) end)()")


def target_row(bus):
    p = str(_ev(bus, _LUA_TARGET, timeout=60.0)).split("|")
    if len(p) < 8:
        raise CollectError("target row malformed: %r" % p)
    return {"campaign_id": p[0], "campaign_uuid": campaign_uuid(bus),
            "turn": _num(p[1]), "income": _num(p[2]), "settlements": _num(p[3]),
            "allies": _num(p[4]), "vassals": _num(p[5]), "power_rank": _num(p[6]),
            "lord_level": _num(p[7]), "ts": time.time()}


_LUA_FACTION_RESOURCES = (
    "local f=cm:get_local_faction(true) "
    "if not f or f:is_null_interface() then return '' end "
    "local ok,rm=pcall(function() return f:pooled_resource_manager() end) "
    "if not ok or not rm then return '' end "
    "local ok2,rs=pcall(function() return rm:resources() end) "
    "if not ok2 or not rs then return '' end "
    "local n=0 local okn,v=pcall(function() return rs:num_items() end) "
    "if okn and v then n=v end "
    "local out={} "
    "for i=0,n-1 do "
    "  local okr,r=pcall(function() return rs:item_at(i) end) "
    "  if okr and r then "
    "    local k,val "
    "    pcall(function() k=r:key() end) "
    "    pcall(function() val=r:value() end) "
    "    if k and val then out[#out+1]=tostring(k)..'~'..tostring(val) end "
    "  end "
    "end "
    "return table.concat(out,',')")


def _parse_resources(raw):
    out = {}
    for row in str(raw or "").split(","):
        k, _, v = row.partition("~")
        if not k or not v:
            continue
        n = _num(v)
        if n is not None:
            out[k.strip()] = n
    return out


def faction_resources(bus):
    return _parse_resources(_ev(bus, _LUA_FACTION_RESOURCES, timeout=25.0, allow_nil=True))


_LUA_DIPLO = (
    "local me=cm:get_local_faction(true) if not me then return '' end "
    "local o={} local fl=cm:model():world():faction_list() "
    "for i=0,fl:num_items()-1 do local f=fl:item_at(i) "
    "if f and not f:is_null_interface() and f:name()~=me:name() then "
    "  local ok,st=pcall(function() return me:diplomatic_standing_with(f) end) "
    "  if ok and st~=nil then "
    "    local function bl(fn) local k,v=pcall(fn) return (k and v) and 1 or 0 end "
    "    o[#o+1]=f:name()..'~'..tostring(st) "
    "      ..'~'..bl(function() return me:at_war_with(f) end) "
    "      ..'~'..bl(function() return me:allied_with(f) end) "
    "      ..'~'..bl(function() return f:is_vassal_of(me) end) "
    "      ..'~'..bl(function() return me:is_vassal_of(f) end) "
    "      ..'~'..bl(function() return me:trade_agreement_with(f) end) "
    "  end end end "
    "return table.concat(o,',')")


def diplomacy_state(bus):
    raw = str(_ev(bus, _LUA_DIPLO, timeout=30.0, allow_nil=True) or "")
    out = []
    for row in raw.split(","):
        p = row.split("~")
        if len(p) < 7 or not p[0]:
            continue
        out.append({"faction": p[0], "standing": _num(p[1]),
                    "at_war": p[2] == "1", "allied": p[3] == "1",
                    "their_vassal": p[4] == "1", "our_master": p[5] == "1",
                    "trade": p[6] == "1"})
    return out


CAMPAIGN_UUID_KEY = "tw_stack_campaign_uuid"

_LUA_UUID_EXPR = ("local ok,v=pcall(function() return cm:get_cached_value('%s', function() "
                  "local t={} for i=1,4 do t[i]=string.format('%%04x', cm:random_number(65535,0)) end "
                  "return cm:get_local_faction_name(true)..'_'..table.concat(t) end) end) "
                  "if ok and v then return tostring(v) end return 'NO-UUID'" % CAMPAIGN_UUID_KEY)
_LUA_UUID = _LUA_UUID_EXPR


def campaign_uuid(bus):
    v = _ev(bus, _LUA_UUID, timeout=25.0, allow_nil=True)
    return None if v in (None, "NO-UUID", "nil", "") else str(v)


_LUA_CAMPAIGN = (_G + "local okc,t0=pcall(os.clock) "
                 "local f=cm:get_local_faction(true) local me=f:name() "
                 "local fc=cco('CcoCampaignFaction',tostring(f:command_queue_index())) "
                 "local allies=0 local vassals=0 "
                 "local fl=cm:model():world():faction_list() "
                 "for i=0,fl:num_items()-1 do local o=fl:item_at(i) if o:name()~=me then "
                 "local ok1,al=pcall(function() return f:allied_with(o) end) "
                 "if ok1 and al then allies=allies+1 end "
                 "local ok2,va=pcall(function() return o:is_vassal_of(f) end) "
                 "if ok2 and va then vassals=vassals+1 end end end "
                 "return me..'|'..cm:model():turn_number()..'|'..f:income()..'|'"
                 "..f:region_list():num_items()..'|'..cm:get_faction(cm:get_local_faction_name(true)):treasury()"
                 "..'|'..tostring(f:is_currently_researching())"
                 "..'|'..tostring(f:command_queue_index())"
                 "..'|'..(function() " + _LUA_UUID_EXPR + " end)()"
                 "..'|'..(function() local ok,v=pcall(function() "
                 "return f:military_force_list():num_items() end) "
                 "if ok and v then return v else return -1 end end)()"
                 "..'|'..(function() local l=f:faction_leader() "
                 "if not l or l:is_null_interface() then return 'nil' end "
                 "local ok,v=pcall(function() return l:rank() end) return ts(ok and v or nil) end)()"
                 "..'|'..allies..'|'..vassals..'|'..ts(g(fc,'StrengthRank'))"
                 "..'|'..(function() if okc and t0 then "
                 "local ok2,t1=pcall(os.clock) if ok2 and t1 then "
                 "return math.floor((t1-t0)*1000) end end return -1 end)()")


_GAME_VERSION = ["unread"]


def _game_version():
    if _GAME_VERSION[0] != "unread":
        return _GAME_VERSION[0]
    try:
        import ctypes
        import os
        sys.path.insert(0, r"D:\tw_stack\launcher")
        from bus_launcher import GAME_DIR
        path = os.path.join(GAME_DIR, "Warhammer3.exe")
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            raise OSError("no version resource on %s" % path)
        buf = ctypes.create_string_buffer(size)
        ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf)
        val, vlen = ctypes.c_void_p(), ctypes.c_uint()
        ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(val), ctypes.byref(vlen))
        ffi = ctypes.cast(val, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        ms, ls = ffi[2], ffi[3]
        _GAME_VERSION[0] = "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception as e:
        sys.stderr.write("collect: game version unreadable -> %s\n" % repr(e)[:120])
        _GAME_VERSION[0] = None
    return _GAME_VERSION[0]


def campaign_state(bus, with_uuid=True):
    return _parse_campaign(_ev(bus, _LUA_CAMPAIGN))


def _parse_campaign(raw):
    p = str(raw).split("|")
    if len(p) < 8:
        raise CollectError("campaign state malformed: %r" % p)
    uid = p[7]
    setts = _num(p[3])
    armies = _num(p[8]) if len(p) > 8 else None
    if armies is None and len(p) <= 8:
        sys.stderr.write("collect: campaign_state has no army count -- defeat detection falls back "
                         "to the defeat SCREEN only (settlements alone cannot decide it: a horde "
                         "legitimately owns nothing)\n")
    return {"faction": p[0], "turn": _num(p[1]), "income": _num(p[2]), "settlements": setts,
            "treasury": _num(p[4]), "is_researching": p[5] == "true", "faction_cqi": p[6],
            "campaign_uuid": (None if uid in ("NO-UUID", "nil", "") else uid),
            "armies": (None if armies is None or armies < 0 else armies),
            "lord_level": _num(p[9]) if len(p) > 9 else None,
            "allies": _num(p[10]) if len(p) > 10 else None,
            "vassals": _num(p[11]) if len(p) > 11 else None,
            "power_rank": (-_num(p[12]) if len(p) > 12 and _num(p[12]) is not None else None),
            "_eval_ms": _num(p[13]) if len(p) > 13 else None,
            "game_version": _game_version(),
            "defeated": False}


_LUA_RUINS = (_G +
    "local out={} local rl=cm:model():world():region_manager():region_list() "
    "for i=0,rl:num_items()-1 do local r=rl:item_at(i) "
    "  local cs=cco('CcoCampaignSettlement','settlement:'..r:name()) "
    "  local ab=cs and g(cs,'IsAbandoned') "
    "  if ts(ab)=='true' then "
    "    local sh=cs and g(cs,'IsShrouded') "
    "    if ts(sh)=='false' then local s=r:settlement() "
    "      local x=s and ts(s:logical_position_x()) or 'nil' "
    "      local y=s and ts(s:logical_position_y()) or 'nil' "
    "      out[#out+1]=r:name()..'~'..x..'~'..y end end end "
    "return table.concat(out,',')")


def _parse_ruins(raw):
    out = []
    for row in str(raw or "").split(","):
        p = row.split("~")
        if len(p) == 3 and p[0]:
            out.append({"region": p[0], "x": _num(p[1]), "y": _num(p[2])})
    return out


def ruins(bus):
    return _parse_ruins(_ev(bus, _LUA_RUINS, timeout=30.0, allow_nil=True))


_LUA_ENEMY_AGENTS = (
    "local function t(fn) local ok,v=pcall(fn) if ok then return v end return nil end "
    "local me=cm:get_local_faction(true) local myname=me:name() local out={} "
    "local fl=cm:model():world():faction_list() "
    "for i=0,fl:num_items()-1 do local f=fl:item_at(i) "
    "  local fname=t(function() return f:name() end) "
    "  if fname and fname~=myname and #out<60 then "
    "    local cl=t(function() return f:character_list() end) "
    "    local nc=cl and t(function() return cl:num_items() end) or 0 "
    "    for j=0,nc-1 do if #out>=60 then break end "
    "      local c=t(function() return cl:item_at(j) end) "
    "      local mf=c and t(function() return c:has_military_force() end) "
    "      if c and mf==false then "
    "        local vis=t(function() return c:is_visible_to_faction(myname) end) "
    "        if vis==true then "
    "          local war=t(function() return me:at_war_with(f) end) "
    "          out[#out+1]=tostring(t(function() return c:command_queue_index() end))..'~'"
    "..tostring(t(function() return c:logical_position_x() end))..'~'"
    "..tostring(t(function() return c:logical_position_y() end))..'~'..fname..'~'..tostring(war) "
    "        end end end end end "
    "return table.concat(out,',')")


def _parse_enemy_agents(raw):
    out = []
    for row in str(raw or "").split(","):
        p = row.split("~")
        if len(p) == 5 and p[0] and p[0] != "nil":
            out.append({"cqi": p[0], "x": _num(p[1]), "y": _num(p[2]),
                        "faction": p[3], "at_war": p[4] == "true"})
    return out


def enemy_agents(bus):
    return _parse_enemy_agents(_ev(bus, _LUA_ENEMY_AGENTS, timeout=30.0, allow_nil=True))


def _tstage(prof, name, fn, *a, **k):
    if prof is None:
        return fn(*a, **k)
    t = time.time()
    try:
        return fn(*a, **k)
    finally:
        prof[name] = prof.get(name, 0) + int((time.time() - t) * 1000)


RUIN_OWNER = "ruins"


def _mask_ruin_owners(rows, ruin_keys):
    for r in rows or ():
        if str(r.get("region")) in ruin_keys:
            r["region_owner"] = RUIN_OWNER
    return rows


def world_state(bus, prof=None):
    rs = _tstage(prof, "world_state/ruins", ruins, bus)
    ruin_keys = {r["region"] for r in rs}
    hostiles = [h for h in _tstage(prof, "world_state/hostiles", _chan, bus, "hostiles", "hostiles")
                if not (h.get("kind") == "settlement" and str(h.get("region")) in ruin_keys)]
    return {"armies": _mask_ruin_owners(
                _tstage(prof, "world_state/chars", _chan, bus, "chars", "chars"), ruin_keys),
            "settlements": _tstage(prof, "world_state/setts", _chan, bus, "setts", "setts"),
            "hostiles": _mask_ruin_owners(hostiles, ruin_keys),
            "enemy_agents": _tstage(prof, "world_state/enemy_agents", enemy_agents, bus),
            "ruins": rs}


_LUA_HASH = (_G +
    "local f=cm:get_local_faction(true) local o={} "
    "local ok,l=pcall(function() return cm:model():world():whose_turn_is_it() end) "
    "if ok and l then local w={} for i=0,l:num_items()-1 do w[#w+1]=l:item_at(i):name() end "
    "o[#o+1]='whose:'..table.concat(w,',') end "
    "o[#o+1]=cm:model():turn_number()..':'..f:treasury()..':'..f:income()..':'..f:region_list():num_items() "
    "local cl=f:character_list() for i=0,cl:num_items()-1 do local c=cl:item_at(i) "
    "o[#o+1]=ts(c:command_queue_index())..'@'..ts(c:logical_position_x())..','..ts(c:logical_position_y())"
    "..'/'..ts(math.floor((c:action_points_remaining_percent() or 0)))"
    "..'/'..ts(c:has_military_force() and c:military_force():active_stance() or '-') end "
    "return table.concat(o,'|')")


def state_hash(bus):
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
    blob = parts
    return {"hash": hashlib.md5(blob.encode("utf-8", "replace")).hexdigest(),
            "roots": roots, "chars": blob.count("@"), "ts": time.time()}


STANCE_STACK = "hud_campaign|BL_parent|land_stance_button_stack|clip_parent|stack_background"


def _find(bus, path, timeout=8.0):
    try:
        r = bus.send("find", path, timeout=timeout) or {}
        return (r.get("result") or {}), (r.get("child_ids") or [])
    except Exception as e:
        raise CollectError("bus find %s: %s" % (path.rsplit("|", 1)[-1], repr(e)[:80]))


_LUA_RECRUITABLE = (_G +
    "local c=cm:get_character_by_cqi(%(cqi)s) "
    "if not c or not c:has_military_force() then return '' end "
    "local mf=c:military_force() "
    "local ul=mf:unit_list() if ul:num_items()==0 then return '' end "
    "local seed=ts(ul:item_at(0):unit_key()) "
    "local u=cco('CcoMainUnitRecord',seed) local rl=u and g(u,'RecordList') "
    "if type(rl)~='table' then return '' end "
    "local out={} "
    "for i=1,#rl do local k=g(rl[i],'Key') "
    "  if k then local ok,v=pcall(function() return mf:can_recruit_unit(ts(k)) end) "
    "    if ok and v then out[#out+1]=ts(k) end end end "
    "return table.concat(out,',')")


RECRUIT_QUEUES = ("local", "global")


def _parse_recruitable(raw):
    return [{"key": k, "state": "active"}
            for k in str(raw or "").split(",") if k and k != "nil"]


def recruitable_units(bus, cqi):
    return _parse_recruitable(_ev(bus, _LUA_RECRUITABLE % {"cqi": cqi},
                                  timeout=30.0, allow_nil=True))


_LUA_MERC_POOLS = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') if not c then return '' end "
    "local ch=cm:get_character_by_cqi(%(cqi)s) local reg=nil "
    "if ch and not ch:is_null_interface() then local r0=ch:region() "
    "if r0 and not r0:is_null_interface() then reg=r0:name() end end "
    "local function agg(list, canfn) "
    "  local by={} local order={} "
    "  for i=1,#list do local u=list[i] local rec=g(u,'MainUnitRecordContext') "
    "    local k=rec and g(rec,'Key') "
    "    if k then local a=tonumber(ts(g(u,'AvailableUnitCount'))) or 0 "
    "      local cost=tonumber(ts(g(rec,'Cost'))) "
    "      local e=by[k] if not e then e={a=0,c=cost,can=false} by[k]=e order[#order+1]=k end "
    "      e.a=e.a+a if cost and (not e.c or cost<e.c) then e.c=cost end "
    "      if canfn and canfn(i-1)==true then e.can=true end end end "
    "  return by, order end "
    "local out={} "
    "local fl=g(c,'FactionContext.MercenaryPoolContext.MercenaryPoolUnitList') "
    "if type(fl)=='table' then "
    "  local by,order=agg(fl, function(i0) return g(c,"
    "'FactionContext.MercenaryPoolContext.CanRecruitUnitForFaction(FactionContext, "
    "FactionContext.MercenaryPoolContext.MercenaryPoolUnitList['..i0..'])') end) "
    "  for _,k in ipairs(order) do local e=by[k] "
    "    out[#out+1]='F~'..k..'~'..ts(e.a)..'~'..ts(e.c)..'~'..ts(e.can) end end "
    "if reg then local s=cco('CcoCampaignSettlement','settlement:'..reg) "
    "  local pl=s and g(s,'ProvinceContext.MercenaryPoolContext.MercenaryPoolUnitList') "
    "  if type(pl)=='table' then "
    "    local by,order=agg(pl, nil) "
    "    for _,k in ipairs(order) do local e=by[k] "
    "      if e.a>0 then "
    "        local can=g(c,'FactionContext.FactionRecordContext.IsUnitPossibleToRecruit("
    "DatabaseRecordContext(\"CcoMainUnitRecord\", \"'..k..'\"))') "
    "        if can==true then out[#out+1]='P~'..k..'~'..ts(e.a)..'~'..ts(e.c) end end end end end "
    "return table.concat(out,'|')")


def _parse_merc_pools(raw):
    pools = {"recruit_ror": [], "raise_dead": []}
    for row in str(raw or "").split("|"):
        p = row.split("~")
        if len(p) < 4 or not p[1] or p[1] == "nil":
            continue
        r = {"key": p[1], "avail": _num(p[2]) or 0.0, "cost": _num(p[3])}
        if p[0] == "F":
            r["can"] = len(p) > 4 and p[4] == "true"
            pools["recruit_ror"].append(r)
        elif p[0] == "P":
            pools["raise_dead"].append(r)
    return pools


def mercenary_pools(bus, cqi):
    return _parse_merc_pools(_ev(bus, _LUA_MERC_POOLS % {"cqi": cqi},
                                 timeout=40.0, allow_nil=True))


def _merc_offers(state, merc_pools):
    offers = []
    at_sea = not state.get("region")
    for r in (merc_pools or {}).get("raise_dead") or []:
        offers.append(_offer("raise_dead", r["key"], True, None,
                             unit=r["key"], cost=r["cost"], pool_avail=r["avail"]))
    for r in (merc_pools or {}).get("recruit_ror") or []:
        ok = bool(r.get("can")) and r["avail"] > 0 and not at_sea
        gate = None if ok else ("at_sea" if at_sea else
                                "locked" if not r.get("can") else "pool_empty")
        offers.append(_offer("recruit_ror", r["key"], ok, gate,
                             unit=r["key"], cost=r["cost"], pool_avail=r["avail"]))
    return offers


def edict_options(bus, region):
    raw = _ev(bus, _G + "local s=cco('CcoCampaignSettlement','settlement:%s');"
                        "local m=g(s,'FactionProvinceManagerContext'); if not m then return '' end "
                        "local l=g(m,'InitiativeList'); if type(l)~='table' then return '' end local o={} "
                        "for i=1,#l do o[#o+1]=ts(g(l[i],'Key')) end return table.concat(o,',')"
              % region, timeout=20.0)
    return [k for k in str(raw or "").split(",") if k and k != "nil"]


_LUA_LORD = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') if not c then return 'NO-CHAR' end "
    "local mf=g(c,'MilitaryForceContext') local ch=cm:get_character_by_cqi(%(cqi)s) "
    "if ch and ch:is_null_interface() then ch=nil end "
    "local rg=nil if ch then local r0=ch:region() "
    "if r0 and not r0:is_null_interface() then rg=r0 end end "
    "local pend={} "
    "if ch and ch:has_military_force() then "
    "  local ok,it=pcall(function() return ch:military_force():recruitment_items() end) "
    "  if ok and it then local n=0 pcall(function() n=#it end) "
    "    for i=1,n do pend[#pend+1]=ts(it[i]) end end end "
    "return ts(g(c,'Rank'))..'|'..ts(g(c,'SkillPointsAvailable'))..'|'..ts(mf and g(mf,'UnitCount'))"
    "..'|'..ts(#pend)..'|'..ts(g(c,'ActionPointPercent'))"
    "..'|'..ts(mf and g(mf,'IsGarrisoned'))..'|'..ts(ch and ch:is_besieging())"
    "..'|'..ts((function() local l=g(mf,'StanceList') if type(l)=='table' then for i=1,#l do "
    "if g(l[i],'IsActive')==true then return g(l[i],'Key') end end end return 'none' end)())"
    "..'|'..ts(ch and ch:performed_action_this_turn())"
    "..'|'..ts(rg and rg:name())"
    "..'|'..ts(ch and ch:logical_position_x())..'|'..ts(ch and ch:logical_position_y())"
    "..'|'..ts(ch and ch:character_subtype_key())"
    "..'|'..ts(ch and ch:is_faction_leader())"
    "..'|'..table.concat(pend,',')"
    "..'|'..ts(g(c,'ActionPointsRemaining'))..'|'..ts(g(c,'ActionPointsPerTurn'))"
    "..'|'..(function() if not (ch and ch:has_military_force()) then return 'nil' end "
    "local ok,v=pcall(function() local ul=ch:military_force():unit_list() local t=0 "
    "for i=0,ul:num_items()-1 do t=t+ul:item_at(i):percentage_proportion_of_full_strength() end "
    "return math.floor(t)/100 end) return ts(ok and v or nil) end)()")


def lord_state(bus, cqi):
    return _parse_lord(_ev(bus, _LUA_LORD % {"cqi": cqi}, timeout=25.0), cqi)


def _parse_lord(raw, cqi):
    p = str(raw).split("|")
    if len(p) < 15:
        raise CollectError("lord state malformed for %s: %r" % (cqi, p))
    return {"cqi": str(cqi), "rank": _num(p[0]), "skill_points": _num(p[1]), "units": _num(p[2]),
            "pending_recruits": _num(p[3]), "ap_pct": _num(p[4]), "garrisoned": p[5] == "true",
            "besieging": p[6] == "true", "stance": p[7], "acted": p[8] == "true",
            "region": (p[9] if p[9] not in ("nil", "") else None),
            "x": _num(p[10]), "y": _num(p[11]),
            "subtype": (p[12] if p[12] not in ("nil", "") else None),
            "is_leader": p[13] == "true",
            "pending_recruit_keys": [k for k in str(p[14]).split(",")
                                     if k and k not in ("nil", "-")],
            "ap_remaining": _num(p[15]) if len(p) > 15 else None,
            "ap_per_turn": _num(p[16]) if len(p) > 16 else None,
            "hp": _num(p[17]) if len(p) > 17 else None}


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
    "..'|'..ts(r and r:num_buildings())..'|'..ts(r and r:is_province_capital())"
    "..'|'..(function() local o={} if type(slots)=='table' then for i=1,#slots do local x=slots[i] "
    "local bc=g(x,'BuildingContext') local lv=bc and g(bc,'BuildingLevelRecordContext') "
    "local ci=g(x,'ConstructionItemContext') local cl=ci and g(ci,'BuildingLevelRecordContext') "
    "o[#o+1]=ts(g(x,'Index'))..':'..ts(lv and g(lv,'Key') or '-')..':'..ts(g(x,'IsActive'))"
    "..':'..ts(cl and g(cl,'Key') or '-')..':'..ts(ci and g(ci,'TurnsToCompletion') or '-')"
    "..':'..ts(ci and g(ci,'IsPaused') or '-') end end "
    "return table.concat(o,',') end)()"
    "..'|'..(function() local o={} "
    "if not r then return '' end "
    "local ok,pv=pcall(function() return r:province() end) "
    "if not ok or not pv or pv:is_null_interface() then return '' end "
    "local ok2,prm=pcall(function() return pv:pooled_resource_manager() end) "
    "if not ok2 or not prm or prm:is_null_interface() then return '' end "
    "local ok3,pl=pcall(function() return prm:resources() end) "
    "if not ok3 or not pl then return '' end "
    "for i=0,pl:num_items()-1 do local pr=pl:item_at(i) "
    "if pr then local okk,k=pcall(function() return pr:key() end) "
    "local okv,v=pcall(function() return pr:value() end) "
    "if okk and okv and k then o[#o+1]=ts(k)..'='..ts(v) end end end "
    "return table.concat(o,',') end)()"
    "..'|'..(function() local n=0 if type(slots)=='table' then for i=1,#slots do "
    "local bc=g(slots[i],'BuildingContext') "
    "local lv=bc and g(bc,'BuildingLevelRecordContext') "
    "local k=lv and ts(g(lv,'Key')) or '' "
    "if k~='' and string.find(k,'settlement') then n=n+(tonumber(g(lv,'Level')) or 0) end "
    "end end return n end)()")


def province_state(bus, region):
    return _parse_province(_ev(bus, _LUA_PROVINCE % {"reg": region}, timeout=25.0), region)


def _parse_province(raw, region):
    raw = str(raw)
    if raw == "NO-SETT":
        return {"region": region, "settlement_present": False}
    p = raw.split("|")
    if len(p) < 10:
        raise CollectError("province state malformed for %s: %r" % (region, p))
    built, locked, building_now = {}, [], {}
    for chunk in (p[10] if len(p) > 10 else "").split(","):
        bits = chunk.split(":")
        if len(bits) < 3:
            continue
        idx, key, active = bits[0], bits[1], bits[2] == "true"
        if key not in ("-", "nil", ""):
            built[idx] = key
        elif not active:
            locked.append(idx)
        if len(bits) >= 6 and bits[3] not in ("-", "nil", ""):
            building_now[idx] = {"key": bits[3], "turns_left": _num(bits[4]),
                                 "paused": bits[5] == "true"}
    corruption = {}
    for chunk in (p[11] if len(p) > 11 else "").split(","):
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            if k and k != "nil":
                corruption[k] = _num(v)
    return {"region": region, "settlement_present": True, "province": p[0],
            "complete_owner": p[1] == "true", "max_slots": _num(p[2]), "free_slots": _num(p[3]),
            "can_set_edict": p[4] == "true", "selected_edict": p[5], "active_edict": p[6],
            "public_order": _num(p[7]), "buildings": _num(p[8]), "is_capital": p[9] == "true",
            "built": built, "locked_slots": locked, "building_now": building_now,
            "corruption": corruption,
            "settlement_level": (_num(p[12]) if len(p) > 12 else None)}


_LUA_ENTITY_TARGETS = (_G +
    "local f=cm:get_local_faction(true) local o={} "
    "local cl=f:character_list() "
    "for i=0,cl:num_items()-1 do local c=cl:item_at(i) "
    "  local ok,rk=pcall(function() return c:rank() end) "
    "  local oc,ac=pcall(function() return c:military_force():is_armed_citizenry() end) "
    "  if ok and rk and not (oc and ac) then "
    "    local kind='lord' "
    "    if not c:has_military_force() then kind='hero' end "
    "    o[#o+1]=kind..'~'..ts(c:command_queue_index())..'~'..ts(rk) end end "
    "local rl=f:region_list() "
    "for i=0,rl:num_items()-1 do local r=rl:item_at(i) local reg=r:name() "
    "  local st=cco('CcoCampaignSettlement','settlement:'..reg) "
    "  if st then local slots=g(st,'BuildingSlotList') local n=0 "
    "    if type(slots)=='table' then for j=1,#slots do "
    "      local bc=g(slots[j],'BuildingContext') "
    "      local lv=bc and g(bc,'BuildingLevelRecordContext') "
    "      local k=lv and ts(g(lv,'Key')) or '' "
    "      if k~='' and string.find(k,'settlement') then n=n+(tonumber(g(lv,'Level')) or 0) end "
    "    end end "
    "    o[#o+1]='province~'..reg..'~'..ts(n) end end "
    "return table.concat(o,',')")


def entity_target_rows(bus):
    raw = str(_ev(bus, _LUA_ENTITY_TARGETS, timeout=30.0, allow_nil=True) or "")
    out = []
    for chunk in raw.split(","):
        p = chunk.split("~")
        if len(p) != 3 or not p[1]:
            continue
        v = _num(p[2])
        if v is not None:
            out.append({"context_kind": p[0], "context_id": p[1], "value": v})
    return out


def _offer(atype, key, available, gate=None, **params):
    return {"action_type": atype, "key": key, "available": bool(available),
            "gate": gate, "params": params or {}}


_LUA_MOVE_CANDIDATES = (
    "local c=cm:get_character_by_cqi(%(cqi)s) "
    "if not c or c:is_null_interface() then return '' end "
    "local fn=c:faction():name() "
    "local cx,cy=c:logical_position_x(),c:logical_position_y() "
    "local rays={} "
    "for d=0,7 do local a=d*0.785398 local best=0 "
    "  for _,r in ipairs({3,5,7,9,12,15,20,28,40,55}) do "
    "    local px=math.floor(cx+r*math.cos(a)+0.5) local py=math.floor(cy+r*math.sin(a)+0.5) "
    "    local ok,v=pcall(function() return c:can_reach_position(px,py) end) "
    "    if ok and v then best=r end "
    "  end "
    "  rays[d+1]=best "
    "end "
    "local seen,out={},{} local tries=0 "
    "while #out<%(n)s and tries<%(n)s*3 do tries=tries+1 "
    "  local d=math.random(0,7) local R=rays[d+1] "
    "  if R>=%(minr)s then "
    "    local a=d*0.785398+(math.random()-0.5)*0.785398 "
    "    local r=%(minr)s+math.random()*(R-%(minr)s) "
    "    local sx=math.floor(cx+r*math.cos(a)+0.5) local sy=math.floor(cy+r*math.sin(a)+0.5) "
    "    local ok,vx,vy=pcall(function() return "
    "      cm:find_valid_spawn_location_for_character_from_position(fn,sx,sy,true) end) "
    "    if ok and vx and vy and vx>=0 and vy>=0 then "
    "      local k=vx..','..vy "
    "      if not seen[k] then "
    "        local ok2,reach=pcall(function() return c:can_reach_position(vx,vy) end) "
    "        if ok2 and reach then seen[k]=true out[#out+1]=k end "
    "      end "
    "    end "
    "  end "
    "end "
    "return table.concat(rays,',')..'||'..table.concat(out,'|')")


def _move_lua(cqi, state):
    if state.get("x") is None or state.get("y") is None:
        return None
    return _LUA_MOVE_CANDIDATES % {"cqi": cqi, "minr": int(MOVE_MIN_R), "n": MOVE_CANDIDATES}


def _parse_moves(raw):
    raw = str(raw or "")
    rays_part, _, tiles_part = raw.partition("||")
    rays = [int(float(r)) for r in rays_part.split(",") if r.strip().lstrip("-").isdigit()]
    reach_max = max(rays) if rays else None
    out = []
    tiles = [t for t in tiles_part.split("|") if t][:MOVE_SAMPLES]
    for i, tile in enumerate(tiles):
        mx, my = tile.split(",")
        out.append(_offer("move", "xy:%s,%s" % (mx, my), True, None,
                          x=int(mx), y=int(my), sample_index=i,
                          reach_rays=rays or None, reach_max=reach_max))
    return out


def _move_offers(bus, cqi, state):
    lua = _move_lua(cqi, state)
    if lua is None:
        return []
    return _parse_moves(_ev(bus, lua, timeout=25.0, allow_nil=True))


def _reach_lua(cqi, target_cqis, regions):
    return ("local a=cm:get_character_by_cqi(%s) local o={} "
            "for t in string.gmatch('%s','[^,]+') do local c=cm:get_character_by_cqi(tonumber(t)) "
            "local ok,v=pcall(function() return cm:character_can_reach_character(a,c) end) "
            "o[#o+1]='C'..t..'='..tostring(ok and v) end "
            "for t in string.gmatch('%s','[^,]+') do local r=cm:get_region(t) "
            "local s=r and r:settlement() "
            "local ok,v=pcall(function() return cm:character_can_reach_settlement(a,s) end) "
            "o[#o+1]='R'..t..'='..tostring(ok and v) end return table.concat(o,',')"
            % (cqi, ",".join(str(t) for t in target_cqis), ",".join(regions)))


def _parse_reach(raw):
    chars, setts = {}, {}
    for part in str(raw or "").split(","):
        if "=" not in part:
            continue
        k, v = part.rsplit("=", 1)
        (chars if k.startswith("C") else setts)[k[1:]] = (v == "true")
    return chars, setts


def _reach(bus, cqi, target_cqis, regions):
    if not target_cqis and not regions:
        return {}, {}
    return _parse_reach(_ev(bus, _reach_lua(cqi, target_cqis, regions), timeout=40.0,
                            allow_nil=True))


_LUA_LORD_OFFERS = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') local mf=g(c,'MilitaryForceContext') "
    "local st={} if mf then local l=g(mf,'StanceList') "
    "if type(l)=='table' then for i=1,#l do local v=l[i] st[#st+1]=ts(g(v,'Key'))"
    "..'~'..ts(g(v,'IsActive'))..'~'..ts(g(v,'CanBeActivated'))..'~'..ts(g(v,'CanAfford')) end end end "
    "local sk={} local s=g(c,'SkillList') "
    "if type(s)=='table' then for i=1,#s do sk[#sk+1]=ts(g(s[i],'Key'))..'~'..ts(g(s[i],'Status')) end end "
    "return table.concat(st,',')..'||'..table.concat(sk,',')")


_LUA_STATIONED = (_G +
    "local out={} local f=cm:get_local_faction(true) local rl=f:region_list() "
    "for ri=0,rl:num_items()-1 do local reg=rl:item_at(ri):name() "
    "  local s=cco('CcoCampaignSettlement','settlement:'..reg) "
    "  local sf=s and g(s,'StationedForceContext') "
    "  local sc=sf and g(sf,'CommandingCharacterContext') "
    "  local gf=s and g(s,'GarrisonForceContext') "
    "  local gc=gf and g(gf,'CommandingCharacterContext') "
    "  out[#out+1]=reg..'~'..ts(sc and g(sc,'CQI'))..'~'..ts(gc and g(gc,'CQI')) end "
    "return table.concat(out,',')")


def _parse_stationed(raw):
    stationed, citizenry = {}, set()
    for row in str(raw or "").split(","):
        parts = row.split("~")
        if len(parts) != 3:
            continue
        reg, scqi, gcqi = parts
        stationed[reg] = None if scqi in ("nil", "") else scqi
        if gcqi not in ("nil", ""):
            citizenry.add(gcqi)
    return {"stationed": stationed, "citizenry": citizenry}


def settlement_forces(bus):
    return _parse_stationed(_ev(bus, _LUA_STATIONED, timeout=25.0, allow_nil=True))


HORDE_SLOT_SCAN = 1200


_LUA_HORDE_SLOTS = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') "
    "local f=c and g(c,'MilitaryForceContext') "
    "if not f or g(f,'IsHorde')~=true then return 'not_horde' end "
    "local me=ts(g(c,'Name')) local o={} "
    "for i=0,%(cap)d do local s=cco('CcoCampaignBuildingSlot','force_slot_'..i) "
    "if s~=nil and g(s,'IsActive')~=nil then local ch=g(s,'CharacterContext') "
    "if ch and ts(g(ch,'Name'))==me then "
    "local p=g(s,'PossibleUpgradeWithoutConversionsList') "
    "if type(p)=='table' then for j=0,#p-1 do "
    "o[#o+1]=i..'~'..ts(g(s,'Index'))..'~'..ts(g(s,'IsEmpty'))..'~'"
    "..ts(g(s,'PossibleUpgradeWithoutConversionsList['..j..'].Key'))..'~'"
    "..ts(g(s,'PossibleUpgradeWithoutConversionsList['..j..'].IsActiveForBuildingBrowser(this)')) "
    "end end end end end return 'horde||'..table.concat(o,',')")


def _horde_slots_lua(cqi):
    return _LUA_HORDE_SLOTS % {"cqi": cqi, "cap": HORDE_SLOT_SCAN}


def _parse_horde_slots(raw):
    raw = str(raw or "")
    if not raw.startswith("horde||"):
        return None
    out = []
    for row in raw[len("horde||"):].split(","):
        p = row.split("~")
        if len(p) != 5 or not p[0]:
            continue
        out.append({"slot_id": "force_slot_%s" % p[0], "slot_index": _num(p[1]),
                    "empty": p[2] == "true", "key": p[3], "available": p[4] == "true"})
    return out


def _horde_building_offers(slots):
    offers = []
    for s in slots or []:
        ok = bool(s["available"])
        offers.append(_offer("horde_building", "%s@%s" % (s["slot_id"], s["key"]), ok,
                             None if ok else "requirements_not_met",
                             slot_id=s["slot_id"], slot_index=s["slot_index"],
                             building_key=s["key"], slot_empty=s["empty"]))
    return offers


def _lord_targets(world):
    armies = [h for h in world["hostiles"] if h.get("kind") == "army" and h.get("cqi")]
    esetts = [h for h in world["hostiles"] if h.get("kind") == "settlement" and h.get("region")]
    osetts = [s for s in world["settlements"] if s.get("region")]
    rsetts = [s for s in (world.get("ruins") or []) if s.get("region")]
    return armies, esetts, osetts, rsetts


def lord_offers(bus, cqi, state, world, stationed=None, prof=None):
    ev_raw = _tstage(prof, "lord_offers/ev", _ev, bus, _LUA_LORD_OFFERS % {"cqi": cqi},
                     timeout=25.0, allow_nil=True)
    recruit_rows = _tstage(prof, "lord_offers/recruitable", recruitable_units, bus, cqi)
    armies, esetts, osetts, rsetts = _lord_targets(world)
    reach_c, reach_s = _tstage(prof, "lord_offers/reach", _reach, bus, cqi,
                               [a["cqi"] for a in armies],
                               [s["region"] for s in esetts] + [s["region"] for s in osetts]
                               + [s["region"] for s in rsetts])
    moves = _move_offers(bus, cqi, state)
    horde = _parse_horde_slots(_tstage(prof, "lord_offers/horde", _ev, bus,
                                       _horde_slots_lua(cqi), timeout=30.0, allow_nil=True))
    merc = _tstage(prof, "lord_offers/mercenary", mercenary_pools, bus, cqi)
    return _lord_offers_assemble(cqi, state, world, stationed, ev_raw, recruit_rows,
                                 reach_c, reach_s, moves, horde_slots=horde, merc_pools=merc)


def _lord_offers_assemble(cqi, state, world, stationed, ev_raw, recruit_rows,
                          reach_c, reach_s, moves, anc_pool=None, equipped=None,
                          equipped_anywhere=None, horde_slots=None, merc_pools=None):
    offers = []
    acted = state.get("acted")
    raw = str(ev_raw or "")
    st_raw, _, sk_raw = raw.partition("||")
    for row in st_raw.split(","):
        p = row.split("~")
        if len(p) < 4:
            continue
        key, active, can_act, afford = p[0], p[1] == "true", p[2] == "true", p[3] == "true"
        ok = bool(can_act and afford and not active)
        gate = None if ok else ("active" if active else
                                "cannot_activate" if not can_act else "cannot_afford")
        offers.append(_offer("stance", key, ok, gate, active=active))
    for c in recruit_rows:
        for queue in RECRUIT_QUEUES:
            offers.append(_offer("recruit_unit", "%s@%s" % (c["key"], queue),
                                 c.get("state") == "active",
                                 None if c.get("state") == "active" else c.get("state"),
                                 unit=c["key"], queue=queue))
    has_pts = (state.get("skill_points") or 0) >= 1
    for row in sk_raw.split(","):
        if "~" not in row:
            continue
        key, status = row.rsplit("~", 1)
        ok = (status == "active" and has_pts)
        offers.append(_offer("skills", key, ok, None if ok else ("no_points" if not has_pts else status)))
    armies, esetts, osetts, rsetts = _lord_targets(world)
    marching = str(state.get("stance") or "") in MOVEMENT_STANCES
    recruiting = (state.get("pending_recruits") or 0) > 0
    for a in armies:
        ok = bool(reach_c.get(str(a["cqi"]))) and not marching
        offers.append(_offer("attack_army", "cqi:%s" % a["cqi"], ok,
                             None if ok else ("movement_stance" if marching else
                                              "recruiting" if recruiting else "cannot_reach"),
                             target_cqi=a["cqi"], target_faction=a.get("faction"),
                             x=a.get("x"), y=a.get("y")))
    for s in esetts:
        ok = bool(reach_s.get(s["region"])) and not marching and not recruiting
        offers.append(_offer("attack_settlement", s["region"], ok,
                             None if ok else ("movement_stance" if marching else
                                              "recruiting" if recruiting else "cannot_reach"),
                             target_faction=s.get("faction"), x=s.get("x"), y=s.get("y")))
    for s in rsetts:
        ok = bool(reach_s.get(s["region"])) and not marching and not recruiting
        offers.append(_offer("colonize", s["region"], ok,
                             None if ok else ("movement_stance" if marching else
                                              "recruiting" if recruiting else "cannot_reach"),
                             x=s.get("x"), y=s.get("y")))
    garrisoned = state.get("garrisoned")
    occ = stationed or {}
    for s in osetts:
        holder = occ.get(str(s["region"]))
        taken = holder is not None and str(holder) != str(cqi)
        ok = bool(reach_s.get(s["region"])) and not garrisoned and not taken
        gate = None if ok else ("already_in_settlement" if garrisoned
                                else "settlement_occupied" if taken else "cannot_reach")
        offers.append(_offer("garrison", "settlement:%s" % s["region"], ok, gate,
                             x=s.get("x"), y=s.get("y")))
    offers.extend(_item_offers(anc_pool, equipped, equipped_anywhere))
    offers.extend(_horde_building_offers(horde_slots))
    offers.extend(_merc_offers(state, merc_pools))
    offers.extend(moves or [])
    offers.append(_offer("noop", "noop", True))
    return offers


_LUA_HERO_OFFERS = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') "
    "local sk={} local s=g(c,'SkillList') "
    "if type(s)=='table' then for i=1,#s do sk[#sk+1]=ts(g(s[i],'Key'))..'~'..ts(g(s[i],'Status')) end end "
    "local tk='' local ch=cm:get_character_by_cqi(%(cqi)s) "
    "if ch then local ok,v=pcall(function() return ch:character_type_key() end) "
    "if ok then tk=ts(v) end end "
    "local hk={} local h=g(c,'HiddenSkillList') "
    "if type(h)=='table' then for i=1,#h do hk[#hk+1]=ts(g(h[i],'Key')) end end "
    "return ts(g(c,'IsAgent'))..'||'..ts(g(c,'CanBeEmbedded'))..'||'..table.concat(sk,',')..'||'..tk"
    "..'||'..table.concat(hk,',')")


ABILITY_TARGETS = {
    "hinder_settlement": ("ruins", "enemy_settlements"),
    "hinder_army": ("enemy_armies",),
    "hinder_agent": ("enemy_armies", "enemy_agents"),
    "hinder_character": ("enemy_armies", "enemy_agents"),
    "assist_army": ("own_armies",),
}

ACTION_TARGETS = {
    "scout_settlement": ("ruins",),
    "assault_garrison": ("enemy_settlements",),
    "steal_technology": ("enemy_settlements",),
    "damage_walls": ("enemy_settlements",),
    "assassinate": ("enemy_agents",),
    "wound": ("enemy_agents",),
}

INNATE_ACTIONS = frozenset(("scout_settlement",))

COVERED_ACTIONS = frozenset((
    "assassinate", "assault_garrison", "assault_units", "block_army", "damage_walls",
    "hinder_replenishment", "increase_mobility", "replenish_troops", "scavenge", "scouting",
    "steal_technology", "training", "wound",
    "scout_settlement",
))

NEEDS_SUBPICK = frozenset((
    "damage_building",
    "assault_unit",
))


def _build_hero_actions():
    out = {}
    try:
        sys.path.insert(0, r"D:\tw_stack\advisor\reference")
        import features_db as _DB
        entries = _DB.agent_action_catalogue()
    except Exception as e:
        sys.stderr.write("collect: hero-action catalogue unavailable -> %s\n" % repr(e)[:120])
        return out
    for e in entries:
        act, ability = e["action"], e["ability"]
        if act not in COVERED_ACTIONS or act in NEEDS_SUBPICK or ability not in ABILITY_TARGETS:
            continue
        spec = out.setdefault(act, {"loc_suffix": [],
                                    "targets": ACTION_TARGETS.get(act, ABILITY_TARGETS[ability]),
                                    "innate": act in INNATE_ACTIONS})
        spec["loc_suffix"].append("%s_%s" % (ability, act))
    return out


HERO_ACTIONS = _build_hero_actions()

_hero_matrix = {}


def hero_type_counts(world):
    out = {}
    for a in ((world or {}).get("armies") or []):
        if a.get("has_army"):
            continue
        t = a.get("agent_type")
        if t:
            out[t] = out.get(t, 0) + 1
    return out


_subtype_types = {}


def _hero_subtype_types(faction):
    key = str(faction or "")
    if key not in _subtype_types:
        try:
            sys.path.insert(0, r"D:\tw_stack\advisor\reference")
            import features_db as _DB
            _subtype_types[key] = {sub: agent for agent, sub in _DB.permitted_agent_subtypes(key)}
        except Exception as e:
            sys.stderr.write("collect: permitted subtypes for %s -> %s\n" % (key, repr(e)[:100]))
            _subtype_types[key] = {}
    return _subtype_types[key]


def _hero_action_matrix(action):
    if action not in _hero_matrix:
        spec = HERO_ACTIONS.get(action) or {}
        try:
            sys.path.insert(0, r"D:\tw_stack\advisor\reference")
            import features_db as _DB
            rows = _DB.agent_action_rows(spec.get("loc_suffix") or "")
            ability = rows[0]["ability"] if rows else None
            _hero_matrix[action] = {
                "types": {r["agent"]: r["key"] for r in rows},
                "ability": ability,
                "attribute": {r["agent"]: r["attribute"] for r in rows},
                "chance": {r["agent"]: r["chance"] for r in rows},
                "category": _DB.agent_ability_category(ability) if ability else None,
            }
        except Exception as e:
            sys.stderr.write("collect: hero-action matrix for %s -> %s\n" % (action, repr(e)[:100]))
            _hero_matrix[action] = {"types": {}, "ability": None, "attribute": {}, "chance": {},
                                    "category": None}
    return _hero_matrix[action]


_LUA_ANCILLARY_POOL = (_G +
    "local f=cco('CcoCampaignFaction','%(fac)s') local l=g(f,'AncillaryList') "
    "if type(l)~='table' then return '' end local o={} "
    "for i=1,#l do o[#o+1]=i..'~'..ts(g(l[i],'Name'))..'~'..ts(g(l[i],'Key')) end "
    "return table.concat(o,'|')")

_LUA_EQUIPPED = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') local l=g(c,'AncillaryList') "
    "if type(l)~='table' then return '' end local o={} "
    "for i=1,#l do o[#o+1]=i..'~'..ts(g(l[i],'Name'))..'~'..ts(g(l[i],'Key')) end "
    "return table.concat(o,'|')")

_LUA_AP_ALL = (
    "local f=cm:get_local_faction(true) local cl=f:character_list() local o={} "
    "local ok=pcall(function() for i=0,cl:num_items()-1 do local c=cl:item_at(i) "
    "local cq=tostring(c:command_queue_index()) local x=cco('CcoCampaignCharacter',cq) "
    "o[#o+1]=cq..'~'..tostring(x:Call('ActionPointsRemaining'))..'~'"
    "..tostring(x:Call('ActionPointsPerTurn')) end end) "
    "if not ok then return '' end return table.concat(o,'|')")


def _parse_ap_all(raw):
    out = {}
    for row in str(raw or "").split("|"):
        p = row.split("~")
        if len(p) == 3 and p[0].isdigit():
            rem, per = _num(p[1]), _num(p[2])
            out[p[0]] = {"ap_remaining": rem, "ap_per_turn": per,
                         "ap_pct": (rem / per) if (rem is not None and per) else None}
    return out


_LUA_EQUIPPED_ALL = (_G +
    "local f=cm:get_local_faction(true) local cl=f:character_list() local o={} "
    "for i=0,cl:num_items()-1 do local c=cl:item_at(i) "
    "  local cq=ts(c:command_queue_index()) "
    "  local l=g(cco('CcoCampaignCharacter',cq),'AncillaryList') "
    "  if type(l)=='table' then for j=1,#l do "
    "    o[#o+1]=j..'~'..ts(g(l[j],'Name'))..'~'..ts(g(l[j],'Key')) end end end "
    "return table.concat(o,'|')")


def _parse_ancillaries(raw):
    out = []
    for row in str(raw or "").split("|"):
        p = row.split("~")
        if len(p) == 3 and p[0].isdigit():
            out.append({"index": int(p[0]), "name": p[1], "key": p[2]})
    return out


def ancillary_pool(bus, faction_cqi):
    return _parse_ancillaries(_ev(bus, _LUA_ANCILLARY_POOL % {"fac": faction_cqi},
                                  timeout=25.0, allow_nil=True))


def _free_by_type(pool, equipped_anywhere):
    held = collections.Counter(a["name"] for a in equipped_anywhere or [])
    free = collections.Counter(a["name"] for a in pool or [])
    for name, n in held.items():
        free[name] -= n
    return free


def _item_offers(pool, equipped, equipped_anywhere=None):
    offers = []
    if pool and equipped_anywhere is None:
        raise CollectError("_item_offers: item pool with no faction-wide equipped set -- "
                           "assignability cannot be counted")
    free = _free_by_type(pool, equipped_anywhere)
    for a in pool or []:
        name = a["name"]
        ok = free.get(name, 0) > 0
        if ok:
            free[name] -= 1
        offers.append(_offer("items", a["key"] or name, ok,
                             None if ok else "already_equipped",
                             pool_index=a["index"], item_name=name, item_key=a["key"]))
    for a in equipped or []:
        offers.append(_offer("item_unequip", a["key"] or a["name"], True, None,
                             equipped_index=a["index"], item_name=a["name"], item_key=a["key"]))
    return offers


SETTLEMENT_COLOCATION = 1.5


def _settlement_points(world):
    w = world or {}
    pts = []
    for s in (w.get("settlements") or []):
        pts.append((s.get("x"), s.get("y")))
    for h in (w.get("hostiles") or []):
        if h.get("kind") == "settlement":
            pts.append((h.get("x"), h.get("y")))
    for r in (w.get("ruins") or []):
        pts.append((r.get("x"), r.get("y")))
    return [(float(x), float(y)) for x, y in pts if x is not None and y is not None]


def _on_settlement(pts, x, y):
    if x is None or y is None:
        return False
    fx, fy = float(x), float(y)
    for sx, sy in pts:
        if math.hypot(fx - sx, fy - sy) <= SETTLEMENT_COLOCATION:
            return True
    return False


def _is_citizenry(hostile):
    if "is_armed_citizenry" not in hostile:
        raise CollectError("hostiles row %s carries no is_armed_citizenry -- the installed mod "
                           "pack is older than the handler that emits it; rebuild bus/dist/tw.pack"
                           % hostile.get("cqi"))
    return hostile["is_armed_citizenry"] is True


def _hero_action_targets(world, kind, self_cqi):
    w = world or {}
    if kind == "ruins":
        return [{"target_kind": "settlement", "region": t["region"], "x": t.get("x"),
                 "y": t.get("y")} for t in (w.get("ruins") or []) if t.get("region")]
    if kind == "enemy_settlements":
        return [{"target_kind": "settlement", "region": h["region"], "x": h.get("x"),
                 "y": h.get("y"), "target_faction": h.get("faction")}
                for h in (w.get("hostiles") or [])
                if h.get("kind") == "settlement" and h.get("region")]
    if kind == "enemy_armies":
        return [{"target_kind": "character", "target_cqi": h["cqi"], "x": h.get("x"),
                 "y": h.get("y"), "target_faction": h.get("faction")}
                for h in (w.get("hostiles") or [])
                if h.get("kind") == "army" and h.get("cqi") and not _is_citizenry(h)]
    if kind == "own_armies":
        cz = set(str(x) for x in w["citizenry"])
        return [{"target_kind": "character", "target_cqi": a["cqi"], "x": a.get("x"),
                 "y": a.get("y"), "target_own": True}
                for a in (w.get("armies") or [])
                if a.get("has_army") and a.get("cqi") and str(a["cqi"]) != str(self_cqi)
                and str(a["cqi"]) not in cz]
    if kind == "enemy_agents":
        return [{"target_kind": "character", "target_cqi": a["cqi"], "x": a.get("x"),
                 "y": a.get("y"), "target_faction": a.get("faction"), "target_agent": True}
                for a in (w.get("enemy_agents") or [])
                if a.get("cqi") and str(a["cqi"]) != str(self_cqi)]
    return []


def _same_tile(state, x, y):
    hx, hy = (state or {}).get("x"), (state or {}).get("y")
    if hx is None or hy is None or x is None or y is None:
        return False
    return abs(float(hx) - float(x)) < 1e-6 and abs(float(hy) - float(y)) < 1e-6


def _granted_actions(skill_keys):
    try:
        sys.path.insert(0, r"D:\tw_stack\advisor\reference")
        import features_db as _DB
        return _DB.actions_for_skills(skill_keys)
    except Exception as e:
        raise CollectError("agent-action unlock map unreadable (%s) -- refusing to offer hero "
                           "actions the hero may not have" % repr(e)[:90])


def _hero_action_offers(cqi, state, world, reach_c, reach_s, is_agent, type_key, skills=(),
                        granted=None, can_embed=True):
    offers = []
    active = [k for k in (skills or ()) if k]
    granted = set(granted or ())
    sett_pts = _settlement_points(world)
    embedded = any(_same_tile(state, a.get("x"), a.get("y"))
                   for a in (world.get("armies") or [])
                   if a.get("has_army") and str(a.get("cqi")) != str(cqi))
    for action, spec in HERO_ACTIONS.items():
        mat = _hero_action_matrix(action)
        action_key = (mat["types"] or {}).get(type_key)
        if action_key is None:
            continue
        sufs = spec["loc_suffix"]
        sufs = [sufs] if isinstance(sufs, str) else list(sufs)
        has_skill = any(any(s in k for s in sufs) for k in active)
        unlocked = action_key in granted
        cands = []
        for tk in spec["targets"]:
            cands.extend(_hero_action_targets(world, tk, cqi))
        for t in cands:
            is_char = t["target_kind"] == "character"
            tid = t.get("target_cqi") if is_char else t.get("region")
            reachable = bool((reach_c if is_char else reach_s or {}).get(str(tid)))
            in_sett = is_char and _on_settlement(sett_pts, t.get("x"), t.get("y"))
            embeds = mat.get("category") == "embedded"
            ok = bool(is_agent and action_key and unlocked and reachable
                      and not (embeds and embedded))
            gate = (None if ok else
                    "not_agent" if not is_agent else
                    "agent_type_cannot_%s" % (type_key or "unknown") if not action_key else
                    ("no_granted_actions" if not granted else "action_not_granted")
                    if not unlocked else
                    "cannot_reach" if not reachable else
                    "already_embedded")
            offers.append(_offer(
                "hero_action", "%s@%s" % (action, ("cqi:%s" % tid) if is_char else tid), ok, gate,
                action=action, action_key=action_key, ability=mat["ability"],
                ability_category=mat.get("category"),
                attribute=(mat.get("attribute") or {}).get(type_key),
                chance=(mat.get("chance") or {}).get(type_key),
                agent_type=type_key, skill_unlocked=has_skill, innate=bool(spec.get("innate")),
                target_kind=t["target_kind"],
                target_cqi=t.get("target_cqi"), region=t.get("region"),
                target_faction=t.get("target_faction"), target_own=bool(t.get("target_own")),
                target_on_settlement=bool(in_sett), target_is_agent=bool(t.get("target_agent")),
                x=t.get("x"), y=t.get("y")))
    return offers


def hero_offers(bus, cqi, state, world):
    ev_raw = _ev(bus, _LUA_HERO_OFFERS % {"cqi": cqi}, timeout=25.0, allow_nil=True)
    moves = _move_offers(bus, cqi, state)
    tgt_c, tgt_r = _hero_action_reach_targets(world, cqi)
    reach_c, reach_s = _reach(bus, cqi, tgt_c, tgt_r)
    return _hero_offers_assemble(cqi, state, ev_raw, moves, world, reach_c, reach_s)


def _hero_action_reach_targets(world, cqi):
    chars, regions = [], []
    for spec in HERO_ACTIONS.values():
        cands = []
        for tk in spec["targets"]:
            cands.extend(_hero_action_targets(world, tk, cqi))
        for t in cands:
            if t["target_kind"] == "character":
                if t["target_cqi"] not in chars:
                    chars.append(t["target_cqi"])
            elif t.get("region") and t["region"] not in regions:
                regions.append(t["region"])
    return chars, regions


def _hero_offers_assemble(cqi, state, ev_raw, moves, world=None, reach_c=None, reach_s=None,
                          anc_pool=None, equipped=None, equipped_anywhere=None):
    offers = []
    parts = str(ev_raw or "").split("||")
    is_agent = parts[0] == "true" if parts else False
    can_embed = (parts[1] == "true") if len(parts) > 1 else False
    sk_raw = parts[2] if len(parts) > 2 else ""
    type_key = parts[3].strip() if len(parts) > 3 else ""
    has_pts = (state.get("skill_points") or 0) >= 1
    active_skills = []
    for row in sk_raw.split(","):
        if "~" not in row:
            continue
        key, status = row.rsplit("~", 1)
        if status == "active":
            active_skills.append(key)
        ok = (status == "active" and has_pts)
        offers.append(_offer("skills", key, ok,
                             None if ok else ("no_points" if not has_pts else status)))
    hidden_skills = [k for k in (parts[4] if len(parts) > 4 else "").split(",") if k]
    granted = _granted_actions(active_skills + hidden_skills)
    offers.extend(_hero_action_offers(cqi, state, world or {}, reach_c or {}, reach_s or {},
                                      is_agent, type_key, active_skills, granted, can_embed))
    offers.extend(_item_offers(anc_pool, equipped, equipped_anywhere))
    offers.extend(moves or [])
    offers.append(_offer("noop", "noop", True))
    return offers


_LUA_PROVINCE_OFFERS = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%s') local slots=g(s,'BuildingSlotList') "
    "local o={} "
    "if type(slots)=='table' then for i=1,#slots do local sl=slots[i] "
    "if g(sl,'IsActive')==true and not g(sl,'ConstructionItemContext') then "
    "local empty=(g(sl,'IsEmpty')==true) "
    "local canup=(g(sl,'CanUpgrade')==true) "
    "local p=g(sl,'PossibleUpgradeWithoutConversionsList') "
    "if type(p)=='table' then for j=0,#p-1 do "
    "o[#o+1]=ts(g(sl,'Index'))..'~'..ts(g(sl,'PossibleUpgradeWithoutConversionsList['..j..'].Key'))"
    "..'~'..ts(g(sl,'PossibleUpgradeWithoutConversionsList['..j..'].IsActiveForBuildingBrowser(this)'))"
    "..'~'..ts(empty)..'~'..ts(canup) end end end end end "
    "local ed={} local m=g(s,'FactionProvinceManagerContext') "
    "if m then local il=g(m,'InitiativeList') "
    "if type(il)=='table' then for i=1,#il do ed[#ed+1]=ts(g(il[i],'Key')) end end end "
    "return 'false||'..table.concat(o,',')..'||'..table.concat(ed,',')")


_LUA_SLOT_STATES = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%s') local slots=g(s,'BuildingSlotList') "
    "local o={} "
    "if type(slots)=='table' then for i=1,#slots do local sl=slots[i] "
    "local ci=g(sl,'ConstructionItemContext') "
    "local cl=ci and g(ci,'BuildingLevelRecordContext') "
    "o[#o+1]=ts(g(sl,'Index'))"
    "..'~'..ts(g(sl,'IsDamaged'))..'~'..ts(g(sl,'CanRepair'))..'~'..ts(g(sl,'IsRepairing'))"
    "..'~'..ts(g(sl,'CanDismantle'))..'~'..ts(g(sl,'DismantleRefundAmount'))"
    "..'~'..ts(ci~=nil)..'~'..ts(g(sl,'CanBeCancelled'))"
    "..'~'..ts(cl and g(cl,'Key'))"
    "..'~'..ts(g(sl,'IsEmpty')) end end "
    "return table.concat(o,',')")


def _parse_slot_states(raw):
    out = []
    for row in str(raw or "").split(","):
        p = row.split("~")
        if len(p) != 10 or not p[0] or p[0] == "nil":
            continue
        out.append({"index": _num(p[0]), "damaged": p[1] == "true", "can_repair": p[2] == "true",
                    "repairing": p[3] == "true", "can_dismantle": p[4] == "true",
                    "refund": _num(p[5]), "building": p[6] == "true",
                    "can_cancel": p[7] == "true",
                    "key": None if p[8] in ("nil", "") else p[8],
                    "empty": p[9] == "true"})
    return out


def _slot_action_offers(region, slots):
    offers = []
    for s in slots or []:
        idx = s.get("index")
        if idx is None:
            continue
        key = "%s@%s" % (region, int(idx))
        ok = s["can_repair"] and s["damaged"] and not s["repairing"]
        offers.append(_offer("building_repair", key, ok,
                             None if ok else ("not_damaged" if not s["damaged"] else
                                              "already_repairing" if s["repairing"] else
                                              "cannot_repair"),
                             region=region, slot_index=idx, damaged=s["damaged"],
                             repairing=s["repairing"], building_key=s["key"]))
        ok = s["can_cancel"] and s["building"]
        offers.append(_offer("building_cancel", key, ok,
                             None if ok else ("nothing_queued" if not s["building"]
                                              else "cannot_cancel"),
                             region=region, slot_index=idx, queued=s["building"],
                             building_key=s["key"]))
        ok = s["can_dismantle"] and not s["empty"]
        offers.append(_offer("building_dismantle", key, ok,
                             None if ok else ("slot_empty" if s["empty"] else "cannot_dismantle"),
                             region=region, slot_index=idx, refund=s["refund"],
                             building_key=s["key"]))
    return offers


def province_offers(bus, region, state, campaign):
    combo = _ev(bus, _LUA_PROVINCE_OFFERS % region, timeout=30.0, allow_nil=True)
    campaign = dict(campaign, hero_type_counts=hero_type_counts(world))
    pools = _lord_pools(bus, campaign["faction_cqi"], _lord_subtypes(bus, campaign["faction"]))
    return _province_offers_assemble(region, state, campaign, combo, pools)


def _province_offers_assemble(region, state, campaign, combo, lord_pools, slot_states=None):
    offers = []
    combo = str(combo or "")
    cparts = combo.split("||")
    if len(cparts) < 3:
        raise CollectError("province offers malformed for %s: %r" % (region, combo[:120]))
    raw = cparts[1]
    edicts = [k for k in cparts[2].split(",") if k and k != "nil"]
    seen = set()
    for row in str(raw or "").split(","):
        p = row.split("~")
        if len(p) < 5:
            continue
        slot, key, active, empty, canup = p[0], p[1], p[2] == "true", p[3] == "true", p[4] == "true"
        if key in seen:
            continue
        seen.add(key)
        ok = active
        gate = None if ok else ("not_buildable_now" if empty else "not_upgradeable_now")
        offers.append(_offer("building", key, ok, gate,
                             slot_index=int(float(slot)) if slot not in ("nil", "") else None,
                             is_upgrade=(not empty)))
    complete = bool(state.get("complete_owner"))
    sel = state.get("selected_edict")
    for key in edicts:
        ok = complete and key != sel
        offers.append(_offer("edict", key, ok,
                             None if ok else ("province_not_complete" if not complete else "already_selected")))
    for sub, (n, can, traits, ranks, agents) in (lord_pools or {}).items():
        if not n:
            continue
        oks = [bool(can[i]) if i < len(can) else False for i in range(n)]
        i = oks.index(True) if any(oks) else 0
        tr = traits[i] if i < len(traits) else []
        is_agent = bool(agents[i]) if i < len(agents) and agents[i] is not None else False
        agent_type = (_hero_subtype_types(campaign.get("faction")) or {}).get(sub)
        fielded = (campaign.get("hero_type_counts") or {}).get(agent_type or "", 0)
        if is_agent:
            ok = bool(any(oks) and agent_type)
            gate = (None if ok else
                    "agent_type_unknown" if not agent_type else "cannot_recruit_character")
        else:
            ok = any(oks)
            gate = None if ok else "cannot_recruit_character"
        offers.append(_offer("recruit_hero" if is_agent else "recruit_lord", sub, ok, gate,
                             candidate_index=i, n_candidates=n, traits=tr,
                             trait=(tr[0] if tr else None), n_traits=len(tr),
                             is_agent=is_agent, agent_type=agent_type, type_fielded=fielded,
                             cand_rank=(ranks[i] if i < len(ranks) else None)))
    offers.extend(_slot_action_offers(region, slot_states))
    offers.append(_offer("noop", "noop", True))
    return offers


_LUA_SUBCULTURE_SUBTYPES = (
    "local function t(fn) local ok,v=pcall(fn) if ok then return tostring(v) end return nil end "
    "local f=cm:get_local_faction(true) "
    "local mysub=t(function() return f:subculture() end) "
    "local seen={} local out={} "
    "local fl=cm:model():world():faction_list() "
    "for i=0,fl:num_items()-1 do local fa=fl:item_at(i) "
    "  if t(function() return fa:subculture() end)==mysub then "
    "    local cl=fa:character_list() "
    "    for j=0,cl:num_items()-1 do local c=cl:item_at(j) "
    "      local ct=t(function() return c:character_type_key() end) "
    "      local st=t(function() return c:character_subtype_key() end) "
    "      if st and ct=='general' and not seen[st] then seen[st]=true out[#out+1]=st end end end end "
    "return table.concat(out,',')")

_SUBTYPE_CACHE = {}


_SUBTYPE_TOKEN = re.compile(r"^wh\d?_[a-z0-9]+_([a-z]+)_")


def _lord_subtypes(bus, faction):
    key = str(faction)
    if key not in _SUBTYPE_CACHE:
        raw = str(_ev(bus, _LUA_SUBCULTURE_SUBTYPES, timeout=30.0, allow_nil=True) or "")
        live = [s for s in raw.split(",") if s and s != "nil"]
        toks = set()
        for s_ in live + [key]:
            m = _SUBTYPE_TOKEN.match(str(s_))
            if m:
                toks.add(m.group(1))
        extra = []
        if toks:
            try:
                sys.path.insert(0, "D:/tw_stack/advisor/reference")
                import features_db as DB
                extra = [sub for sub, _label in DB.agent_subtypes(toks)]
            except Exception as e:
                raise CollectError("agent-subtype reference unreadable (%s); refusing to offer "
                                   "only the subtypes that happen to be alive on the map"
                                   % repr(e)[:90])
            if not extra:
                raise CollectError("reference DB returned no agent subtypes for race tokens %s "
                                   "-- it is stale against the running packs" % sorted(toks))
        _SUBTYPE_CACHE[key] = sorted(set(live) | set(extra))
    return _SUBTYPE_CACHE[key]


def _lord_pools(bus, faction_cqi, subtypes):
    if not subtypes:
        return {}
    lst = ",".join("'%s'" % s for s in subtypes)
    raw = _ev(bus, _G + "local f=cco('CcoCampaignFaction','%s') local out={} "
                        "for _,sub in ipairs({%s}) do "
                        "local e='CharacterRecruitmentPoolEntriesForAgentSubtype(DatabaseRecordContext(\"CcoAgentSubtypeRecord\",\"'..sub..'\"))' "
                        "local ok,n=pcall(function() return f:Call(e..'.Size') end) "
                        "if not ok or not n then n=0 end local o={} "
                        "for i=0,n-1 do local base=e..'['..i..']' "
                        "local can=ts(f:Call(base..'.CanRecruitCharacter')) "
                        "local okr,rk=pcall(function() return f:Call(base..'.CharacterContext.Rank') end) "
                        "if not okr or rk==nil then rk='' end "
                        "local tr={} local okt,nt=pcall(function() return f:Call(base..'.CharacterContext.TraitsList.Size') end) "
                        "if okt and nt then for j=0,nt-1 do "
                        "local okk,k=pcall(function() return f:Call(base..'.CharacterContext.TraitsList['..j..'].TraitRecordContext.Key') end) "
                        "if okk and k then tr[#tr+1]=ts(k) end end end "
                        "local oka,ia=pcall(function() return f:Call(base..'.CharacterContext.IsAgent') end) "
                        "o[#o+1]=can..'^'..table.concat(tr,'+')..'^'..ts(rk)..'^'..ts(oka and ia) end "
                        "out[#out+1]=sub..'='..n..':'..table.concat(o,',') end "
                        "return table.concat(out,';;')" % (faction_cqi, lst), timeout=40.0,
             allow_nil=True)
    out = {}
    for chunk in str(raw or "").split(";;"):
        if "=" not in chunk or ":" not in chunk:
            continue
        sub, rest = chunk.split("=", 1)
        n, flags = rest.split(":", 1)
        can, traits, ranks, agents = [], [], [], []
        for f in flags.split(","):
            if not f:
                continue
            bits = f.split("^")
            can.append(bits[0] == "true")
            tr = bits[1] if len(bits) > 1 else ""
            traits.append([t for t in tr.split("+") if t and t != "nil"])
            ranks.append(_num(bits[2]) if len(bits) > 2 else None)
            agents.append(bits[3] == "true" if len(bits) > 3 else None)
        try:
            out[sub] = (int(float(n)), can, traits, ranks, agents)
        except ValueError:
            out[sub] = (0, [], [], [], [])
    return out


_LUA_CAMPAIGN_OFFERS = (_G +
    "local f=cco('CcoCampaignFaction','%(fac)s') local m=g(f,'TechnologyManagerContext') "
    "local cur='none' if m then local c=g(m,'CurrentResearchingTechnologyContext') "
    "if c then cur=ts(g(c,'NodeKey')) end end "
    "local pts=ts(g(m,'ResearchPoints')) "
    "local tech={} local l=m and g(m,'TechnologyList') "
    "if type(l)=='table' then for i=1,#l do tech[#tech+1]=ts(g(l[i],'NodeKey'))"
    "..'~'..ts(g(l[i],'IsResearched'))..'~'..ts(g(l[i],'CanResearch'))"
    "..'~'..ts(g(l[i],'Cost')) end end "
    "local rites={} local r=g(f,'AvailableRitualList') "
    "if type(r)=='table' then for i=1,#r do rites[#rites+1]=ts(g(r[i],'CanPerformRitual')) end end "
    "return cur..'||'..table.concat(tech,',')..'||'..table.concat(rites,',')..'||'..pts")


def current_research(bus, faction_cqi):
    v = _ev(bus, _G + "local m=g(cco('CcoCampaignFaction','%s'),'TechnologyManagerContext') "
                      "local c=m and g(m,'CurrentResearchingTechnologyContext') "
                      "if c then return ts(g(c,'NodeKey')) end return 'none'" % faction_cqi,
            timeout=20.0, allow_nil=True)
    return None if v in (None, "none", "nil") else str(v)


def campaign_offers(bus, campaign, prof=None):
    fac = campaign["faction_cqi"]
    raw = _tstage(prof, "campaign_offers/ev", _ev, bus, _LUA_CAMPAIGN_OFFERS % {"fac": fac},
                  timeout=30.0, allow_nil=True)
    dip = _tstage(prof, "campaign_offers/diplomacy", diplomacy_offers, bus, campaign.get("turn"))
    return _campaign_offers_assemble(raw, dip)


def _campaign_offers_assemble(raw, diplo_offers):
    offers = []
    raw = str(raw or "")
    parts = raw.split("||")
    if len(parts) < 3:
        raise CollectError("campaign offers malformed: %r" % raw[:120])
    current, tech_raw, rites_raw = parts[0], parts[1], parts[2]
    current = None if current in ("none", "nil", "") else current
    points = _num(parts[3]) if len(parts) > 3 else None
    for row in tech_raw.split(","):
        p = row.split("~")
        if len(p) < 3:
            continue
        key, done, can = p[0], p[1] == "true", p[2] == "true"
        cost = _num(p[3]) if len(p) > 3 else None
        gate = None if can else ("researched" if done else
                                 "in_progress" if key == current else "prerequisites_not_met")
        if current and can:
            can, gate = False, "already_researching"
        offers.append(_offer("research", key, can, gate, in_progress=(key == current),
                             cost=cost, points_available=points, current_research=current))
    for i, flag in enumerate(rites_raw.split(",")):
        if flag not in ("true", "false"):
            continue
        offers.append(_offer("rites", "rite_index_%d" % (i + 1), flag == "true",
                             None if flag == "true" else "cannot_perform", rite_index=i + 1))
    offers.extend(diplo_offers or [])
    offers.append(_offer("end_turn", "end_turn", True))
    offers.append(_offer("noop", "noop", True))
    return offers


DIPLO_SCHEMA = 2

DIPLO_TERMS = ("nonaggression_pact", "trade_agreement", "defensive_alliance", "soft_access",
               "military_alliance", "vassal", "confederation")
DIPLO_DECLARE_WAR = "declare_war"
DIPLO_PEACE = "peace"
DIPLO_GIFT_TIERS = ("small", "medium", "large")

_LUA_DIPLO_TARGETS = (
    "local me=cm:get_local_faction(true) "
    "if not me or me:is_null_interface() then return '' end "
    "local out={} "
    "local ok,fl=pcall(function() return me:factions_met() end) "
    "if not ok or not fl then return 'READ_FAILED' end "
    "local myname='' pcall(function() myname=me:name() end) "
    "local function b(g) local o,v=pcall(g) if o and v then return 1 else return 0 end end "
    "for i=0,fl:num_items()-1 do "
    "  local okf,f=pcall(function() return fl:item_at(i) end) "
    "  if okf and f and not f:is_null_interface() then "
    "    local nm=nil pcall(function() nm=f:name() end) "
    "    if nm and nm~=myname then "
    "      local excl=0 "
    "      if b(function() return f:is_dead() end)==1 "
    "         or b(function() return f:is_rebel() end)==1 "
    "         or b(function() return f:is_quest_battle_faction() end)==1 then excl=1 end "
    "      local war=b(function() return me:at_war_with(f) end) "
    "      local ally=b(function() return me:allied_with(f) end) "
    "      local trade=b(function() return me:trade_agreement_with(f) end) "
    "      local vas=b(function() return f:is_vassal_of(me) end) "
    "      local st=0 local o2,v2=pcall(function() return me:diplomatic_standing_with(nm) end) "
    "      if o2 and type(v2)=='number' then st=v2 end "
    "      out[#out+1]=nm..'~'..war..'~'..ally..'~'..trade..'~'..vas..'~'..st..'~'..excl "
    "    end "
    "  end "
    "end "
    "return table.concat(out,',')")




def _bus(bus, cmd, arg=None, timeout=20.0):
    try:
        return bus.send(cmd, arg, timeout=timeout) if arg is not None else \
            bus.send(cmd, timeout=timeout)
    except Exception as e:
        sys.stderr.write("collect: bus %s failed -> %s\n" % (cmd, repr(e)[:110]))
        return None


def _roots(bus):
    r = _bus(bus, "roots") or {}
    return [k.get("id") for k in (r.get("kids") or [])] or list(r.get("roots") or [])


def diplomacy_offers(bus, turn=None, epoch=None):
    return _diplo_offers_build(bus, turn, epoch,
                               _ev(bus, _LUA_DIPLO_TARGETS, timeout=30.0, allow_nil=True))


def _parse_diplo_targets(raw):
    if raw is None or str(raw) in ("nil", "None", "READ_FAILED"):
        return None
    targets = []
    for row in str(raw).split(","):
        p = row.split("~")
        if len(p) < 6 or not p[0]:
            continue
        try:
            standing = float(p[5])
        except (TypeError, ValueError):
            standing = 0.0
        targets.append({"faction": p[0], "at_war": p[1] == "1", "allied": p[2] == "1",
                        "trade": p[3] == "1", "their_vassal": p[4] == "1", "standing": standing,
                        "excluded": len(p) > 6 and p[6] == "1"})
    return targets


def diplo_unseen_check(targets, world):
    if targets is None:
        return None
    seen = set()
    for h in ((world or {}).get("hostiles") or []):
        f = h.get("faction")
        if f:
            seen.add(f)
    known = set(t["faction"] for t in targets)
    return sorted(seen - known)


def _diplo_offers_build(bus, turn, epoch, raw):
    targets = _parse_diplo_targets(raw)
    if targets is None:
        sys.stderr.write("collect: DIPLOMACY TARGET READ FAILED -- no diplomacy will be offered this "
                         "snapshot. This is a broken read, NOT an empty world.\n")
        return []
    if not targets:
        sys.stderr.write("collect: diplomacy targets EMPTY from factions_met -- 0 diplomacy offers "
                         "this snapshot.\n")
        return []
    targets = [t for t in targets if not t.get("excluded")]
    if not targets:
        return []
    targets.sort(key=lambda t: -abs(t["standing"]))
    offers = []
    for t in targets:
        f = t["faction"]
        rel = {"standing": t["standing"], "at_war": t["at_war"], "allied": t["allied"],
               "trade": t["trade"], "their_vassal": t["their_vassal"]}
        at_war = bool(t["at_war"])
        offers.append(_offer("diplomacy", "%s:%s" % (f, DIPLO_DECLARE_WAR), not at_war,
                             "already_at_war" if at_war else None,
                             faction=f, terms=[DIPLO_DECLARE_WAR], **rel))
        offers.append(_offer("diplomacy", "%s:%s" % (f, DIPLO_PEACE), at_war,
                             None if at_war else "not_at_war",
                             faction=f, terms=[DIPLO_PEACE], **rel))
        for a in DIPLO_TERMS:
            offers.append(_offer("diplomacy", "%s:%s" % (f, a), not at_war,
                                 "at_war_offers_only_peace" if at_war else None,
                                 faction=f, terms=[a], **rel))
        for tier in DIPLO_GIFT_TIERS:
            offers.append(_offer("diplomacy", "%s:gift_%s" % (f, tier), not at_war,
                                 "at_war_offers_only_peace" if at_war else None,
                                 faction=f, terms=[], gift=tier, **rel))
    return offers


def _bres(reply, what, allow_nil=False):
    r = reply or {}
    if r.get("error"):
        _e = str(r["error"])
        raise CollectError("lua error (%s): ...%s" % (what, _e[-240:]) if len(_e) > 240 else
                           "lua error (%s): %s" % (what, _e))
    v = r.get("result")
    if v is None and not allow_nil:
        raise CollectError("eval returned nil: %s" % what)
    return v


def snapshot(bus, active=None, diplo_epoch=None):
    prof = {}
    t0 = time.time()
    ra = bus.send_batch([("eval", _LUA_CAMPAIGN), ("eval", _LUA_FACTION_RESOURCES),
                         ("eval", _LUA_RUINS), ("chars", ""), ("setts", ""), ("hostiles", ""),
                         ("eval", _LUA_STATIONED), ("eval", _LUA_DIPLO_TARGETS),
                         ("eval", _LUA_ENEMY_AGENTS), ("eval", _LUA_AP_ALL)], timeout=40.0)
    prof["wave_a_ms"] = int((time.time() - t0) * 1000)
    camp = _parse_campaign(_bres(ra[0], "campaign_state"))
    prof["campaign_state_engine_ms"] = camp.pop("_eval_ms", None)
    camp["resources"] = _parse_resources(_bres(ra[1], "faction_resources", allow_nil=True))
    rs = _parse_ruins(_bres(ra[2], "ruins", allow_nil=True))
    ruin_keys = {r["region"] for r in rs}
    world = {"armies": _mask_ruin_owners(ra[3].get("chars") or [], ruin_keys),
             "settlements": ra[4].get("setts") or [],
             "hostiles": _mask_ruin_owners(
                 [h for h in (ra[5].get("hostiles") or [])
                  if not (h.get("kind") == "settlement"
                          and str(h.get("region")) in ruin_keys)], ruin_keys),
             "ruins": rs,
             "enemy_agents": _parse_enemy_agents(_bres(ra[8], "enemy_agents", allow_nil=True))}
    diplo_raw = _bres(ra[7], "diplo_targets", allow_nil=True)
    world["relations"] = _parse_diplo_targets(diplo_raw)
    world["diplo_schema"] = DIPLO_SCHEMA
    world["diplo_unseen"] = diplo_unseen_check(world["relations"], world)
    world["diplo_hostile_rows"] = len(world["hostiles"])
    if world["diplo_unseen"]:
        sys.stderr.write("collect: DIPLO MET-SET DISCREPANCY -- %d faction(s) on the map but absent "
                         "from factions_met (%d hostiles rows, channel caps at 60): %s\n"
                         % (len(world["diplo_unseen"]), len(world["hostiles"]),
                            ",".join(world["diplo_unseen"])))
    sf = _parse_stationed(_bres(ra[6], "settlement_forces", allow_nil=True))
    stationed, citizenry = sf["stationed"], sf["citizenry"]
    world["citizenry"] = sorted(citizenry)
    ap_all = _parse_ap_all(_bres(ra[9], "ap_all"))
    for c in world["armies"]:
        ap = ap_all.get(str(c.get("cqi")))
        if ap is None:
            raise CollectError("no action points for character %s -- every entity the features "
                               "read must carry AP in the pre-decision snapshot" % c.get("cqi"))
        c.update(ap)
    lords = [str(c.get("cqi")) for c in world["armies"]
             if c.get("has_army") and str(c.get("cqi")) not in citizenry]
    heroes = [str(c.get("cqi")) for c in world["armies"] if not c.get("has_army")]
    regions = [s["region"] for s in world["settlements"] if s.get("region")]
    want_camp = True
    if active is not None:
        lords = [c for c in lords if c in set(str(x) for x in (active.get("lords") or []))]
        heroes = [c for c in heroes if c in set(str(x) for x in (active.get("heroes") or []))]
        regions = [r for r in regions if r in set(active.get("regions") or [])]
        want_camp = bool(active.get("campaign", True))
    dip = []
    if want_camp:
        t0 = time.time()
        dip = _diplo_offers_build(bus, camp.get("turn"), diplo_epoch, diplo_raw)
        prof["campaign_offers/diplomacy"] = int((time.time() - t0) * 1000)

    armies_t, esetts_t, osetts_t, rsetts_t = _lord_targets(world)
    reach_cqis = [a["cqi"] for a in armies_t]
    reach_regions = ([s["region"] for s in esetts_t] + [s["region"] for s in osetts_t]
                     + [s["region"] for s in rsetts_t])
    wave_b = []
    wave_b.append(("eval", _LUA_ANCILLARY_POOL % {"fac": camp["faction_cqi"]}))
    wave_b.append(("eval", _LUA_EQUIPPED_ALL))
    for cqi in lords:
        wave_b += [("eval", _LUA_LORD % {"cqi": cqi}),
                   ("eval", _LUA_LORD_OFFERS % {"cqi": cqi}),
                   ("eval", _LUA_RECRUITABLE % {"cqi": cqi}),
                   ("eval", _reach_lua(cqi, reach_cqis, reach_regions)),
                   ("eval", _LUA_EQUIPPED % {"cqi": cqi}),
                   ("eval", _horde_slots_lua(cqi)),
                   ("eval", _LUA_MERC_POOLS % {"cqi": cqi})]
    for cqi in heroes:
        h_c, h_r = _hero_action_reach_targets(world, cqi)
        wave_b += [("eval", _LUA_LORD % {"cqi": cqi}),
                   ("eval", _LUA_HERO_OFFERS % {"cqi": cqi}),
                   ("eval", _reach_lua(cqi, h_c, h_r)),
                   ("eval", _LUA_EQUIPPED % {"cqi": cqi})]
    for reg in regions:
        wave_b += [("eval", _LUA_PROVINCE % {"reg": reg}),
                   ("eval", _LUA_PROVINCE_OFFERS % reg),
                   ("eval", _LUA_SLOT_STATES % reg)]
    if want_camp:
        wave_b.append(("eval", _LUA_CAMPAIGN_OFFERS % {"fac": camp["faction_cqi"]}))
    t0 = time.time()
    rb = []
    for j in range(0, len(wave_b), 14):
        rb += bus.send_batch(wave_b[j:j + 14], timeout=40.0)
    prof["wave_b_ms"] = int((time.time() - t0) * 1000)
    i = 0
    lord_data, hero_data, prov_data = {}, {}, {}
    anc_pool = _parse_ancillaries(_bres(rb[i], "ancillary_pool", allow_nil=True))
    i += 1
    equipped_all = _parse_ancillaries(_bres(rb[i], "equipped_all"))
    i += 1
    for cqi in lords:
        lord_data[cqi] = (_parse_lord(_bres(rb[i], "lord_state:%s" % cqi), cqi),
                          _bres(rb[i + 1], "lord_offers:%s" % cqi, allow_nil=True),
                          _parse_recruitable(_bres(rb[i + 2], "recruitable:%s" % cqi, allow_nil=True)),
                          _parse_reach(_bres(rb[i + 3], "reach:%s" % cqi, allow_nil=True)),
                          _parse_ancillaries(_bres(rb[i + 4], "equipped:%s" % cqi, allow_nil=True)),
                          _parse_horde_slots(_bres(rb[i + 5], "horde_slots:%s" % cqi,
                                                   allow_nil=True)),
                          _parse_merc_pools(_bres(rb[i + 6], "merc_pools:%s" % cqi,
                                                  allow_nil=True)))
        i += 7
    for cqi in heroes:
        hero_data[cqi] = (_parse_lord(_bres(rb[i], "hero_state:%s" % cqi), cqi),
                          _bres(rb[i + 1], "hero_offers:%s" % cqi, allow_nil=True),
                          _parse_reach(_bres(rb[i + 2], "hero_reach:%s" % cqi, allow_nil=True)),
                          _parse_ancillaries(_bres(rb[i + 3], "equipped:%s" % cqi, allow_nil=True)))
        i += 4
    for reg in regions:
        prov_data[reg] = (_parse_province(_bres(rb[i], "province_state:%s" % reg), reg),
                          _bres(rb[i + 1], "province_offers:%s" % reg, allow_nil=True),
                          _parse_slot_states(_bres(rb[i + 2], "slot_states:%s" % reg,
                                                   allow_nil=True)))
        i += 3
    camp_offers_raw = _bres(rb[i], "campaign_offers", allow_nil=True) if want_camp else None

    wave_c, move_cqis = [], []
    for cqi in lords + heroes:
        st = (lord_data.get(cqi) or hero_data.get(cqi))[0]
        lua = _move_lua(cqi, st)
        if lua is not None:
            wave_c.append(("eval", lua))
            move_cqis.append(cqi)
    t0 = time.time()
    rc = bus.send_batch(wave_c, timeout=40.0) if wave_c else []
    prof["wave_c_ms"] = int((time.time() - t0) * 1000)
    moves = {}
    for j, cqi in enumerate(move_cqis):
        st = (lord_data.get(cqi) or hero_data.get(cqi))[0]
        moves[cqi] = _parse_moves(_bres(rc[j], "moves:%s" % cqi, allow_nil=True))

    ents = []
    for cqi in lords:
        st, ev, rec, (rch_c, rch_s), equipped, horde, merc = lord_data[cqi]
        ents.append({"context_kind": "lord", "context_id": str(cqi), "state": st,
                     "offers": _lord_offers_assemble(cqi, st, world, stationed, ev, rec,
                                                     rch_c, rch_s, moves.get(cqi),
                                                     anc_pool, equipped, equipped_all,
                                                     horde, merc)})
    for cqi in heroes:
        st, ev, (rch_c, rch_s), equipped = hero_data[cqi]
        ents.append({"context_kind": "hero", "context_id": str(cqi), "state": st,
                     "offers": _hero_offers_assemble(cqi, st, ev, moves.get(cqi), world,
                                                     rch_c, rch_s, anc_pool, equipped,
                                                     equipped_all)})
    t0 = time.time()
    camp = dict(camp, hero_type_counts=hero_type_counts(world))
    pools = (_lord_pools(bus, camp["faction_cqi"], _lord_subtypes(bus, camp["faction"]))
             if regions else {})
    prof["lord_pools_ms"] = int((time.time() - t0) * 1000)
    for reg in regions:
        st, combo, slots = prov_data[reg]
        ents.append({"context_kind": "province", "context_id": reg, "state": st,
                     "offers": _province_offers_assemble(reg, st, camp, combo, pools, slots)})
    if want_camp:
        ents.append({"context_kind": "campaign", "context_id": camp["faction"], "state": dict(camp),
                     "offers": _campaign_offers_assemble(camp_offers_raw, dip)})
    prof["_entities"] = len(ents)
    prof["_lords"] = len(lords)
    prof["_heroes"] = len(heroes)
    prof["_regions"] = len(regions)
    prof["_wave_b_cmds"] = len(wave_b)
    prof["_wave_c_cmds"] = len(wave_c)
    return {"ts": time.time(), "campaign": camp, "world": world,
            "entities": ents, "profile": prof}


if __name__ == "__main__":
    from bus import Bus
    import json
    b = Bus()
    print("target_row:", json.dumps(target_row(b)))
    t0 = time.time()
    snap = snapshot(b)
    tot = sum(len(e["offers"]) for e in snap["entities"])
    av = sum(1 for e in snap["entities"] for o in e["offers"] if o["available"])
    print("snapshot: turn %s | %d entities | %d offers (%d available) | %.1fs"
          % (snap["campaign"]["turn"], len(snap["entities"]), tot, av, time.time() - t0))
    print("world: %d armies, %d settlements, %d hostiles"
          % (len(snap["world"]["armies"]), len(snap["world"]["settlements"]),
             len(snap["world"]["hostiles"])))
    for e in snap["entities"]:
        print("  %-9s %-28s state=%-2d offers=%-4d avail=%d"
              % (e["context_kind"], str(e["context_id"])[:28], len(e["state"]), len(e["offers"]),
                 sum(1 for o in e["offers"] if o["available"])))
        for o in [o for o in e["offers"] if o["available"]][:5]:
            print("      + %-18s %-40s %s" % (o["action_type"], str(o["key"])[:40], o["params"]))
