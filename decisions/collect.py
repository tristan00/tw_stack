from __future__ import annotations

import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

sys.path.insert(0, common.BUS)

MOVE_SAMPLES = 16
MOVE_MIN_R = 3.0

_G = ("local function g(c,p) local ok,v=pcall(function() return c:Call(p) end);"
      "if ok and v~=nil then return v end return nil end "
      "local function ts(v) if v==nil then return '' end return tostring(v) end "
      "local function tv(ok,v) if ok and v~=nil then return tostring(v) end return '' end ")


class CollectError(RuntimeError):
    pass


_TRY_FAILS = []


def _note_try_fails(reply):
    tf = (reply or {}).get("try_fails")
    if tf:
        _TRY_FAILS.extend(tf)
    return reply


def _drain_try_fails():
    out = {}
    for row in _TRY_FAILS:
        try:
            out[str(row.get("err"))] = out.get(str(row.get("err")), 0) + int(row.get("n") or 1)
        except AttributeError:
            out[str(row)] = out.get(str(row), 0) + 1
    del _TRY_FAILS[:]
    return out


def _ev(bus, lua, timeout=20.0, allow_nil=False):
    try:
        r = _note_try_fails(bus.send("eval", lua, timeout=timeout) or {})
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


CAMPAIGN_UUID_KEY = "tw_stack_campaign_uuid"

_LUA_UUID_EXPR = ("local ok,v=pcall(function() return cm:get_cached_value('%s', function() "
                  "local t={} for i=1,4 do t[i]=string.format('%%04x', cm:random_number(65535,0)) end "
                  "return cm:get_local_faction_name(true)..'_'..table.concat(t) end) end) "
                  "if ok and v then return tostring(v) end return 'NO-UUID'" % CAMPAIGN_UUID_KEY)
_LUA_UUID = _LUA_UUID_EXPR


def campaign_uuid(bus):
    v = _ev(bus, _LUA_UUID, timeout=25.0, allow_nil=True)
    return None if v in (None, "NO-UUID", "nil", "") else str(v)


_LUA_CAMPAIGN_MAP = (
    "local out='' "
    "local function try(fn) if out~='' then return end "
    "  local ok,v=pcall(fn) if ok and v~=nil and tostring(v)~='' then out=tostring(v) end end "
    "try(function() return cm:model():campaign_name_key() end) "
    "try(function() return cm:get_campaign_name() end) "
    "try(function() return cm:model():campaign_name() end) "
    "try(function() return cco('CcoCampaignRoot',''):Call('CampaignKey') end) "
    "return out")

_MAP_CACHE = {}


def _presave_radius():
    v = os.environ.get("TW_PRESAVE_RADIUS", "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        raise ValueError(
            "TW_PRESAVE_RADIUS is %r, which is not a number. The start condition of a "
            "campaign is not something to guess at -- fix the launcher rather than "
            "recording an unknown radius." % v)


def _selector():
    return os.environ.get("TW_SELECTOR", "").strip() or None


def campaign_map(bus, uuid=None):
    key = str(uuid or "")
    if key in _MAP_CACHE:
        return _MAP_CACHE[key]
    v = _ev(bus, _LUA_CAMPAIGN_MAP, timeout=25.0, allow_nil=True)
    v = None if v in (None, "nil", "") else str(v)
    _MAP_CACHE[key] = v
    if len(_MAP_CACHE) > 64:
        _MAP_CACHE.clear()
        _MAP_CACHE[key] = v
    return v


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
                 "local ok,v=pcall(function() return l:rank() end) return tv(ok,v) end)()"
                 "..'|'..allies..'|'..vassals..'|'..ts(g(fc,'StrengthRank'))"
                 "..'|'..(function() if okc and t0 then "
                 "local ok2,t1=pcall(os.clock) if ok2 and t1 then "
                 "return math.floor((t1-t0)*1000) end end return -1 end)()"
                 "..'|'..(function() local l=f:faction_leader() "
                 "if not l or l:is_null_interface() then return 'nil' end "
                 "local ok,v=pcall(function() return l:is_wounded() end) "
                 "return tv(ok,v) end)()"
                 "..'|'..(function() local ok,v=pcall(function() return f:is_dead() end) "
                 "return tv(ok,v) end)()"
                 "..'|'..(function() local ok,v=pcall(function() "
                 "return cm:model():combined_difficulty_level() end) "
                 "if ok and v~=nil then return tostring(v) end return 'nil' end)()"
                 "..'|'..(function() local l=f:faction_leader() "
                 "if not l or l:is_null_interface() then return 'nil' end "
                 "local ok,v=pcall(function() "
                 "local fn=common.get_localised_string(l:get_forename()) "
                 "local sn='' pcall(function() "
                 "sn=common.get_localised_string(l:get_surname()) end) "
                 "if sn and sn~='' then return fn..' '..sn end return fn end) "
                 "if ok and v and v~='' then return v end return 'nil' end)()")


_GAME_VERSION = ["unread"]


def _game_version():
    if _GAME_VERSION[0] != "unread":
        return _GAME_VERSION[0]
    try:
        import ctypes
        import os
        sys.path.insert(0, common.LAUNCHER)
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
            "ll_wounded": (p[14] == "true") if len(p) > 14 and p[14] in ("true", "false") else None,
            "game_version": _game_version(),
            "defeated": ((p[15] == "true") if len(p) > 15 and p[15] in ("true", "false")
                         else None),
            "difficulty": _num(p[16]) if len(p) > 16 else None,
            "leader": (p[17].strip() if len(p) > 17 and p[17].strip()
                       and p[17].strip() != "nil" else None)}


_LUA_REGIONS = (_G +
    "local function t(fn) local ok,v=pcall(fn) if ok then return v end return nil end "
    "local rl=cm:model():world():region_manager():region_list() "
    "local vis={} local seen={} "
    "for i=0,rl:num_items()-1 do local r=rl:item_at(i) "
    "  local nm=t(function() return r:name() end) "
    "  if nm then local cs=cco('CcoCampaignSettlement','settlement:'..nm) "
    "    if ts(cs and g(cs,'IsShrouded'))=='false' then "
    "      vis[#vis+1]=r seen[nm]=true end end end "
    "local out={} "
    "for i=1,#vis do local r=vis[i] local nm=r:name() "
    "  local cs=cco('CcoCampaignSettlement','settlement:'..nm) "
    "  local s=t(function() return r:settlement() end) "
    "  local x=s and ts(t(function() return s:logical_position_x() end)) or 'nil' "
    "  local y=s and ts(t(function() return s:logical_position_y() end)) or 'nil' "
    "  local ow='' local of=t(function() return r:owning_faction() end) "
    "  if of and t(function() return of:is_null_interface() end)==false then "
    "    ow=ts(t(function() return of:name() end)) end "
    "  local pv=ts(t(function() return r:province_name() end)) "
    "  local cap=ts(t(function() return r:is_province_capital() end)) "
    "  local ab=ts(g(cs,'IsAbandoned')) "
    "  local adj={} local al=t(function() return r:adjacent_region_list() end) "
    "  if al then for j=0,al:num_items()-1 do "
    "    local an=t(function() return al:item_at(j):name() end) "
    "    if an and seen[an] then adj[#adj+1]=an end end end "
    "  out[#out+1]=nm..'~'..x..'~'..y..'~'..pv..'~'..ow..'~'..cap..'~'..ab"
    "..'~'..table.concat(adj,'|') end "
    "return table.concat(out,',')")


def _key(v):
    s = str(v or "")
    return None if s in ("", "nil", "None") else s


def _parse_regions(raw):
    out = []
    for row in str(raw or "").split(","):
        p = row.split("~")
        if len(p) < 8 or not p[0]:
            continue
        out.append({"region": p[0], "x": _num(p[1]), "y": _num(p[2]),
                    "province": _key(p[3]), "owner": _key(p[4]),
                    "capital": p[5] == "true", "abandoned": p[6] == "true",
                    "adjacent": [a for a in p[7].split("|") if a]})
    return out


def _ruins_of(regs):
    return [{"region": r["region"], "x": r["x"], "y": r["y"]}
            for r in (regs or ()) if r.get("abandoned")]


def regions(bus):
    return _parse_regions(_ev(bus, _LUA_REGIONS, timeout=30.0, allow_nil=True))


_LUA_WAR_GRAPH = (
    "local me=cm:get_local_faction(true) local ml=me:factions_met() "
    "local metset={} metset[me:name()]=true "
    "for i=0,ml:num_items()-1 do metset[ml:item_at(i):name()]=true end "
    "local out={} "
    "for i=0,ml:num_items()-1 do local f=ml:item_at(i) "
    "  local ok,wl=pcall(function() return f:factions_at_war_with() end) "
    "  if ok and wl then local e={} "
    "    for j=0,wl:num_items()-1 do local w=wl:item_at(j):name() "
    "      if metset[w] then e[#e+1]=w end end "
    "    if #e>0 then out[#out+1]=f:name()..'>'..table.concat(e,'|') end end end "
    "return table.concat(out,',')")


def _parse_war_graph(raw):
    out = []
    for row in str(raw or "").split(","):
        subj, _, rest = row.partition(">")
        if not subj or not rest:
            continue
        peers = [w for w in rest.split("|") if w]
        if peers:
            out.append({"faction": subj, "at_war_with": peers})
    return out


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
    regs = _tstage(prof, "world_state/regions", regions, bus)
    rs = _ruins_of(regs)
    ruin_keys = {r["region"] for r in rs}
    hostiles = [h for h in _tstage(prof, "world_state/hostiles", _chan, bus, "hostiles", "hostiles")
                if not (h.get("kind") == "settlement" and str(h.get("region")) in ruin_keys)]
    return {"armies": _mask_ruin_owners(
                _tstage(prof, "world_state/chars", _chan, bus, "chars", "chars"), ruin_keys),
            "settlements": _tstage(prof, "world_state/setts", _chan, bus, "setts", "setts"),
            "hostiles": _mask_ruin_owners(hostiles, ruin_keys),
            "enemy_agents": _tstage(prof, "world_state/enemy_agents", enemy_agents, bus),
            "ruins": rs,
            "regions": regs}


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
    "    if ok and v then out[#out+1]=ts(k)..'~'..ts(g(rl[i],'Cost'))"
    "..'~'..ts(g(rl[i],'IsRecruitmentDisabled')) end end end "
    "return table.concat(out,',')")


def _parse_recruitable(raw):
    out = []
    for row in str(raw or "").split(","):
        p = row.split("~")
        if not p[0] or p[0] == "nil":
            continue
        out.append({"key": p[0], "state": "active",
                    "cost": _num(p[1]) if len(p) > 1 else None,
                    "disabled": (p[2] == "true") if len(p) > 2 else None})
    return out


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


MERC_FLAVOR_ACTIONS = {"raise_dead": "raise_dead", "renown": "recruit_ror",
                       "blessed_spawning": "recruit_blessed",
                       "imperial_supply": "recruit_imperial"}

_MERC_REFERENCE_DB = common.REFERENCE_DB
_merc_flavor_map = None
_merc_drop_logged = set()


def _merc_flavors():
    global _merc_flavor_map
    if _merc_flavor_map is None:
        con = sqlite3.connect("file:%s?mode=ro" % _MERC_REFERENCE_DB.replace("\\", "/"), uri=True)
        try:
            rows = con.execute("SELECT DISTINCT unit, flavor FROM merc_units").fetchall()
        finally:
            con.close()
        if not rows:
            raise CollectError("reference merc_units is empty -- rebuild reference.sqlite "
                               "(advisor/reference/build_reference.py)")
        m = {}
        for unit, flavor in rows:
            m.setdefault(unit, set()).add(flavor)
        _merc_flavor_map = m
    return _merc_flavor_map


def _parse_merc_pools(raw):
    pools = {}
    flavors = _merc_flavors()
    dropped = {}
    for row in str(raw or "").split("|"):
        p = row.split("~")
        if len(p) < 4 or not p[1] or p[1] == "nil":
            continue
        origin, key = p[0], p[1]
        fset = flavors.get(key)
        if not fset:
            dropped[key] = "unmapped"
            continue
        if origin == "P":
            flavor = "raise_dead" if "raise_dead" in fset else sorted(fset)[0]
        else:
            non_rd = sorted(fset - {"raise_dead"})
            flavor = non_rd[0] if non_rd else "raise_dead"
        atype = MERC_FLAVOR_ACTIONS.get(flavor)
        if atype is None:
            dropped[key] = flavor
            continue
        r = {"key": key, "avail": _num(p[2]) or 0.0, "cost": _num(p[3])}
        if origin == "F":
            r["can"] = len(p) > 4 and p[4] == "true"
        pools.setdefault(atype, []).append(r)
    new_drops = sorted(set(dropped.values()) - _merc_drop_logged)
    if new_drops:
        _merc_drop_logged.update(new_drops)
        sys.stderr.write("collect: merc pool units dropped -- unsupported flavor(s) %s "
                         "(first units: %s)\n"
                         % (new_drops, sorted(dropped)[:5]))
    return pools


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
    "..'|'..ts(g(c,'IsGarrisoned'))..'|'..ts(ch and ch:is_besieging())"
    "..'|'..ts((function() local l=g(mf,'StanceList') if type(l)=='table' then for i=1,#l do "
    "if g(l[i],'IsActive')==true then return g(l[i],'Key') end end end return 'none' end)())"
    "..'|'..ts(ch and ch:performed_action_this_turn())"
    "..'|'..ts(rg and rg:name())"
    "..'|'..ts(ch and ch:logical_position_x())..'|'..ts(ch and ch:logical_position_y())"
    "..'|'..ts(ch and ch:character_subtype_key())"
    "..'|'..ts(ch and ch:is_faction_leader())"
    "..'|'..table.concat(pend,',')"
    "..'|'..ts(g(c,'ActionPointsRemaining'))..'|'..ts(g(c,'ActionPointsPerTurn'))"
    "..'|'..(function() if not (ch and ch:has_military_force()) then return 'nil|' end "
    "local ok,v=pcall(function() local ul=ch:military_force():unit_list() local t=0 local d={} "
    "for i=0,ul:num_items()-1 do local u=ul:item_at(i) "
    "local pc=u:percentage_proportion_of_full_strength() t=t+pc "
    "d[#d+1]=ts(u:unit_key())..'~'..ts(pc)..'~'..ts(u:unit_category())"
    "..'~'..ts(u:experience_level()) end "
    "return (math.floor(t)/100)..'|'..table.concat(d,',') end) "
    "if ok and v~=nil then return v end return 'nil|' end)()"
    "..'|'..ts(ch and ch:is_wounded())"
    "..'|'..ts(ch and ch:loyalty())")


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
            "hp": _num(p[17]) if len(p) > 17 else None,
            "unit_cards": _parse_unit_cards(p[18] if len(p) > 18 else ""),
            "wounded": (p[19] == "true") if len(p) > 19 and p[19] != "" else None,
            "loyalty": _num(p[20]) if len(p) > 20 else None}


def _parse_unit_cards(raw):
    out = []
    for chunk in str(raw or "").split(","):
        bits = chunk.split("~")
        if len(bits) < 4 or not bits[0] or bits[0] in ("nil", "-"):
            continue
        out.append({"key": bits[0], "strength_pct": _num(bits[1]),
                    "category": (bits[2] if bits[2] not in ("nil", "") else None),
                    "xp": _num(bits[3])})
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
    "end end return n end)()"
    "..'|'..ts(m and g(m,'GrowthPerTurn'))..'|'..ts(m and g(m,'GrossIncome'))"
    "..'|'..ts(m and g(m,'DevelopmentPoints'))..'|'..ts(g(s,'Income'))"
    "..'|'..ts(g(s,'HasPort'))..'|'..ts(g(s,'HasWalls'))")


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
            "settlement_level": (_num(p[12]) if len(p) > 12 else None),
            "growth_per_turn": (_num(p[13]) if len(p) > 13 else None),
            "gross_income": (_num(p[14]) if len(p) > 14 else None),
            "development_points": (_num(p[15]) if len(p) > 15 else None),
            "income": (_num(p[16]) if len(p) > 16 else None),
            "has_port": (p[17] == "true") if len(p) > 17 else None,
            "has_walls": (p[18] == "true") if len(p) > 18 else None}


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
    return _LUA_MOVE_CANDIDATES % {"cqi": cqi, "minr": int(MOVE_MIN_R), "n": MOVE_SAMPLES}


def _parse_move_tiles(raw):
    raw = str(raw or "")
    rays_part, _, tiles_part = raw.partition("||")
    rays = [int(float(r)) for r in rays_part.split(",") if r.strip().lstrip("-").isdigit()]
    reach_max = max(rays) if rays else None
    out = []
    for i, tile in enumerate([x for x in tiles_part.split("|") if x]):
        mx, my = tile.split(",")
        out.append({"x": int(mx), "y": int(my), "sample_index": i,
                    "reach_rays": rays or None, "reach_max": reach_max})
    return out


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


_LUA_LORD_OFFERS = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') local mf=g(c,'MilitaryForceContext') "
    "local st={} if mf then local l=g(mf,'StanceList') "
    "if type(l)=='table' then for i=1,#l do local v=l[i] st[#st+1]=ts(g(v,'Key'))"
    "..'~'..ts(g(v,'IsActive'))..'~'..ts(g(v,'CanBeActivated'))..'~'..ts(g(v,'CanAfford')) end end end "
    "local sk={} local s=g(c,'SkillList') "
    "if type(s)=='table' then for i=1,#s do sk[#sk+1]=ts(g(s[i],'Key'))..'~'..ts(g(s[i],'Status'))"
    "..'~'..ts(g(s[i],'Level'))..'~'..ts(g(s[i],'TotalLevels'))..'~'..ts(g(s[i],'Tier')) end end "
    "local hk={} local h=g(c,'HiddenSkillList') "
    "if type(h)=='table' then for i=1,#h do hk[#hk+1]=ts(g(h[i],'Key')) end end "
    "return table.concat(st,',')..'||'..table.concat(sk,',')..'||'..table.concat(hk,',')")


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


_LUA_HERO_OFFERS = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') "
    "local sk={} local s=g(c,'SkillList') "
    "if type(s)=='table' then for i=1,#s do sk[#sk+1]=ts(g(s[i],'Key'))..'~'..ts(g(s[i],'Status'))"
    "..'~'..ts(g(s[i],'Level'))..'~'..ts(g(s[i],'TotalLevels'))..'~'..ts(g(s[i],'Tier')) end end "
    "local tk='' local ch=cm:get_character_by_cqi(%(cqi)s) "
    "if ch then local ok,v=pcall(function() return ch:character_type_key() end) "
    "if ok then tk=ts(v) end end "
    "local hk={} local h=g(c,'HiddenSkillList') "
    "if type(h)=='table' then for i=1,#h do hk[#hk+1]=ts(g(h[i],'Key')) end end "
    "return ts(g(c,'IsAgent'))..'||'..ts(g(c,'CanBeEmbedded'))..'||'..table.concat(sk,',')..'||'..tk"
    "..'||'..table.concat(hk,',')")


_LUA_ANCILLARY_POOL = (_G +
    "local f=cco('CcoCampaignFaction','%(fac)s') local l=g(f,'AncillaryList') "
    "if type(l)~='table' then return '' end local o={} "
    "for i=1,#l do o[#o+1]=i..'~'..ts(g(l[i],'Name'))..'~'..ts(g(l[i],'AncillaryRecordContext.Key')) end "
    "return table.concat(o,'|')")

_LUA_EQUIPPED = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') local l=g(c,'AncillaryList') "
    "if type(l)~='table' then return '' end local o={} "
    "for i=1,#l do o[#o+1]=i..'~'..ts(g(l[i],'Name'))..'~'..ts(g(l[i],'AncillaryRecordContext.Key')) end "
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
    "    o[#o+1]=j..'~'..ts(g(l[j],'Name'))..'~'..ts(g(l[j],'AncillaryRecordContext.Key')) end end end "
    "return table.concat(o,'|')")


def _parse_ancillaries(raw):
    out = []
    for row in str(raw or "").split("|"):
        p = row.split("~")
        if len(p) == 3 and p[0].isdigit():
            out.append({"index": int(p[0]), "name": p[1],
                        "key": p[2] or None})
    return out


def ancillary_pool(bus, faction_cqi):
    return _parse_ancillaries(_ev(bus, _LUA_ANCILLARY_POOL % {"fac": faction_cqi},
                                  timeout=25.0, allow_nil=True))


_LUA_PROVINCE_OFFERS = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%s') local slots=g(s,'BuildingSlotList') "
    "local o={} "
    "if type(slots)=='table' then for i=1,#slots do local sl=slots[i] "
    "if g(sl,'IsActive')==true and not g(sl,'ConstructionItemContext') then "
    "local empty=(g(sl,'IsEmpty')==true) "
    "local canup=(g(sl,'CanUpgrade')==true) "
    "local p=g(sl,'PossibleUpgradeWithoutConversionsList') "
    "if type(p)=='table' then for j=0,#p-1 do "
    "local b='PossibleUpgradeWithoutConversionsList['..j..']' "
    "o[#o+1]=ts(g(sl,'Index'))..'~'..ts(g(sl,b..'.Key'))"
    "..'~'..ts(g(sl,b..'.IsActiveForBuildingBrowser(this)'))"
    "..'~'..ts(empty)..'~'..ts(canup)"
    "..'~'..ts(g(sl,b..'.CreateCost(SettlementContext)'))"
    "..'~'..ts(g(sl,b..'.UpkeepCost'))"
    "..'~'..ts(g(sl,b..'.Level'))"
    "..'~'..ts(g(sl,b..'.CanAffordResourceCostForSlot(this)')) end end end end end "
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
    "..'~'..ts(g(sl,'BuildingContext.IsDamaged'))"
    "..'~'..ts(g(sl,'CanRepair'))..'~'..ts(g(sl,'IsRepairing'))"
    "..'~'..ts(g(sl,'CanDismantle'))"
    "..'~'..ts(g(sl,'BuildingContext.DismantleRefundAmount'))"
    "..'~'..ts(ci~=nil)"
    "..'~'..ts(cl and g(cl,'Key'))"
    "..'~'..ts(g(sl,'IsEmpty'))"
    "..'~'..ts(g(sl,'BuildingContext.BuildingLevelRecordContext.Key'))"
    "..'~'..ts(g(sl,'BuildingContext.Health'))"
    "..'~'..ts(g(sl,'BuildingContext.MaxHealth'))"
    "..'~'..ts(g(sl,'BuildingContext.IsRuined'))"
    "..'~'..ts(g(sl,'RepairCost'))"
    "..'~'..ts(g(sl,'IsUpgrading'))..'~'..ts(g(sl,'IsDismantling')) end end "
    "return table.concat(o,',')")

_SLOT_STATE_FIELDS = 16


def _parse_slot_states(raw):
    out = []
    for row in str(raw or "").split(","):
        p = row.split("~")
        if len(p) < _SLOT_STATE_FIELDS or not p[0] or p[0] == "nil":
            continue
        out.append({"index": _num(p[0]), "damaged": p[1] == "true", "can_repair": p[2] == "true",
                    "repairing": p[3] == "true", "can_dismantle": p[4] == "true",
                    "refund": _num(p[5]), "queued": p[6] == "true",
                    "queued_key": None if p[7] in ("nil", "") else p[7],
                    "empty": p[8] == "true",
                    "key": None if p[9] in ("nil", "") else p[9],
                    "health": _num(p[10]), "max_health": _num(p[11]),
                    "ruined": p[12] == "true", "repair_cost": _num(p[13]),
                    "upgrading": p[14] == "true", "dismantling": p[15] == "true"})
    return out


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
                sys.path.insert(0, common.REFERENCE)
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


TRAITS_UNREAD = "!"

_LUA_LORD_POOLS = (_G +
    "local f=cco('CcoCampaignFaction','%(cqi)s') local out={} "
    "for _,sub in ipairs({%(subs)s}) do "
    "local e='CharacterRecruitmentPoolEntriesForAgentSubtype(DatabaseRecordContext(\"CcoAgentSubtypeRecord\",\"'..sub..'\"))' "
    "local ok,n=pcall(function() return f:Call(e..'.Size') end) "
    "if not ok or not n then n=0 end local o={} "
    "for i=0,n-1 do local base=e..'['..i..']' "
    "local can=ts(f:Call(base..'.CanRecruitCharacter')) "
    "local trs='' local tr={} "
    "local okt,nt=pcall(function() return f:Call(base..'.CharacterContext.TraitsList.Size') end) "
    "if not okt or nt==nil then trs='" + TRAITS_UNREAD + "' else "
    "for j=0,nt-1 do "
    "local okk,k=pcall(function() return f:Call(base..'.CharacterContext.TraitsList['..j..'].TraitRecordContext.Key') end) "
    "if okk and k then tr[#tr+1]=ts(k) end end trs=table.concat(tr,'+') end "
    "local oka,ia=pcall(function() return f:Call(base..'.CharacterContext.IsAgent') end) "
    "local bg=ts(g(f,e..'['..i..'].CharacterContext.BackgroundSkillContext.Key')) "
    "local cq=ts(g(f,e..'['..i..'].CharacterContext.CQI')) "
    "local st=ts(g(f,e..'['..i..'].CharacterContext.AgentSubtypeRecordContext.Key')) "
    "local un=ts(g(f,e..'['..i..'].MainUnitRecordContext.Key')) "
    "local rk=ts(g(f,e..'['..i..'].CharacterContext.Rank')) "
    "o[#o+1]=can..'^'..trs..'^'..ts(oka and ia)..'^'..bg..'^'..cq..'^'..st..'^'..un"
    "..'^'..rk end "
    "out[#out+1]=sub..'='..n..':'..table.concat(o,',') end "
    "return table.concat(out,';;')")


def _pool_cols():
    return {"n": 0, "can": [], "traits": [], "agents": [], "bg_skills": [],
            "cqis": [], "subtypes": [], "units": [], "ranks": []}


def _lord_pools(bus, faction_cqi, subtypes):
    if not subtypes:
        return {}
    return _parse_lord_pools(
        _ev(bus, _LUA_LORD_POOLS % {"cqi": faction_cqi,
                                    "subs": ",".join("'%s'" % s for s in subtypes)},
            timeout=40.0, allow_nil=True))


def _parse_lord_pools(raw):
    out = {}
    for chunk in str(raw or "").split(";;"):
        if "=" not in chunk or ":" not in chunk:
            continue
        sub, rest = chunk.split("=", 1)
        n, flags = rest.split(":", 1)
        col = _pool_cols()
        for f in flags.split(","):
            if not f:
                continue
            b = f.split("^")

            def _s(i):
                v = b[i] if len(b) > i else ""
                return None if v in ("", "nil") else v

            col["can"].append(b[0] == "true")
            tr = b[1] if len(b) > 1 else ""
            col["traits"].append(None if tr == TRAITS_UNREAD else
                                 [t for t in tr.split("+") if t])
            col["agents"].append(b[2] == "true" if len(b) > 2 else None)
            col["bg_skills"].append(_s(3))
            col["cqis"].append(_s(4))
            col["subtypes"].append(_s(5))
            col["units"].append(_s(6))
            col["ranks"].append(_num(_s(7)) if _s(7) is not None else None)
        try:
            col["n"] = int(float(n))
        except ValueError:
            col = _pool_cols()
        out[sub] = col
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
    "if type(r)=='table' then for i=1,#r do rites[#rites+1]="
    "ts(g(r[i],'CanPerformRitual'))..'~'..ts(g(r[i],'RitualContext.Key'))"
    "..'~'..ts(g(r[i],'InvalidRitualReason')) end end "
    "return cur..'||'..table.concat(tech,',')..'||'..table.concat(rites,',')..'||'..pts")


def current_research(bus, faction_cqi):
    v = _ev(bus, _G + "local m=g(cco('CcoCampaignFaction','%s'),'TechnologyManagerContext') "
                      "local c=m and g(m,'CurrentResearchingTechnologyContext') "
                      "if c then return ts(g(c,'NodeKey')) end return 'none'" % faction_cqi,
            timeout=20.0, allow_nil=True)
    return None if v in (None, "none", "nil") else str(v)


DIPLO_SCHEMA = 2


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
    "      local mstr=b(function() return me:is_vassal_of(f) end) "
    "      local st=0 local o2,v2=pcall(function() return me:diplomatic_standing_with(nm) end) "
    "      if o2 and type(v2)=='number' then st=v2 end "
    "      local mila=b(function() return me:military_allies_with(f) end) "
    "      local defa=b(function() return me:defensive_allies_with(f) end) "
    "      local nap=b(function() return me:non_aggression_pact_with(f) end) "
    "      local macc=b(function() return me:military_access_pact_with(f) end) "
    "      out[#out+1]=nm..'~'..war..'~'..ally..'~'..trade..'~'..vas..'~'..st..'~'..excl"
    "..'~'..mila..'~'..defa..'~'..nap..'~'..macc..'~'..mstr "
    "    end "
    "  end "
    "end "
    "return table.concat(out,',')")


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
                        "excluded": len(p) > 6 and p[6] == "1",
                        "mil_ally": len(p) > 7 and p[7] == "1",
                        "def_ally": len(p) > 8 and p[8] == "1",
                        "nap": len(p) > 9 and p[9] == "1",
                        "mil_access": len(p) > 10 and p[10] == "1",
                        "our_master": len(p) > 11 and p[11] == "1"})
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


def _parse_stance_skills(raw):
    parts = str(raw or "").split("||")
    st_raw = parts[0] if parts else ""
    sk_raw = parts[1] if len(parts) > 1 else ""
    hk_raw = parts[2] if len(parts) > 2 else ""
    stances = []
    for row in st_raw.split(","):
        p = row.split("~")
        if len(p) < 4:
            continue
        stances.append({"key": p[0], "active": p[1] == "true",
                        "can_activate": p[2] == "true", "can_afford": p[3] == "true"})
    hidden = [k for k in hk_raw.split(",") if k and k not in ("nil", "-")]
    return stances, _parse_skills(sk_raw), hidden


def _parse_skills(sk_raw):
    out = []
    for row in str(sk_raw or "").split(","):
        p = row.split("~")
        if len(p) < 2 or not p[0]:
            continue
        out.append({"key": p[0], "status": p[1],
                    "level": _num(p[2]) if len(p) > 2 else None,
                    "total_levels": _num(p[3]) if len(p) > 3 else None,
                    "tier": _num(p[4]) if len(p) > 4 else None})
    return out


def _parse_hero_blob(raw):
    parts = str(raw or "").split("||")
    return {"is_agent": (parts[0] == "true") if parts else False,
            "can_embed": (parts[1] == "true") if len(parts) > 1 else False,
            "skills": _parse_skills(parts[2] if len(parts) > 2 else ""),
            "agent_type": parts[3].strip() if len(parts) > 3 else "",
            "hidden_skills": [k for k in (parts[4] if len(parts) > 4 else "").split(",") if k]}


def _parse_buildable(raw):
    cparts = str(raw or "").split("||")
    if len(cparts) < 3:
        raise CollectError("province offers malformed: %r" % str(raw)[:120])
    out = []
    for row in cparts[1].split(","):
        p = row.split("~")
        if len(p) < 9:
            continue
        slot = p[0]
        out.append({"slot_index": int(float(slot)) if slot not in ("nil", "") else None,
                    "key": p[1], "active": p[2] == "true", "empty": p[3] == "true",
                    "can_upgrade": p[4] == "true", "cost": _num(p[5]), "upkeep": _num(p[6]),
                    "level": _num(p[7]),
                    "can_afford_resources": p[8] == "true"})
    return out, [k for k in cparts[2].split(",") if k and k != "nil"]


def _parse_tech_rites(raw):
    parts = str(raw or "").split("||")
    if len(parts) < 3:
        raise CollectError("campaign offers malformed: %r" % str(raw)[:120])
    cur = parts[0]
    tech = []
    for row in parts[1].split(","):
        p = row.split("~")
        if len(p) < 3:
            continue
        tech.append({"key": p[0], "researched": p[1] == "true", "can_research": p[2] == "true",
                     "cost": _num(p[3]) if len(p) > 3 else None})
    rites = []
    for i, row in enumerate(parts[2].split(",")):
        p = row.split("~")
        flag = p[0] if p else ""
        if flag not in ("true", "false"):
            continue
        rites.append({"index": i + 1, "can_perform": flag == "true",
                      "key": (p[1] or None) if len(p) > 1 else None,
                      "invalid_reason": (p[2] or None) if len(p) > 2 else None})
    return {"current_research": (None if cur in ("none", "nil", "") else cur),
            "research_points": _num(parts[3]) if len(parts) > 3 else None,
            "tech": tech, "rites": rites}


def _hero_type_counts(world):
    out = {}
    for a in ((world or {}).get("armies") or []):
        if a.get("has_army"):
            continue
        t = a.get("agent_type")
        if t:
            out[t] = out.get(t, 0) + 1
    return out


def _bres(reply, what, allow_nil=False):
    r = _note_try_fails(reply) or {}
    if r.get("error"):
        _e = str(r["error"])
        raise CollectError("lua error (%s): ...%s" % (what, _e[-240:]) if len(_e) > 240 else
                           "lua error (%s): %s" % (what, _e))
    v = r.get("result")
    if v is None and not allow_nil:
        raise CollectError("eval returned nil: %s" % what)
    return v


def snapshot(bus, active=None):
    prof = {}
    t0 = time.time()
    ra = bus.send_batch([("eval", _LUA_CAMPAIGN), ("eval", _LUA_FACTION_RESOURCES),
                         ("eval", _LUA_REGIONS), ("chars", ""), ("setts", ""), ("hostiles", ""),
                         ("eval", _LUA_STATIONED), ("eval", _LUA_DIPLO_TARGETS),
                         ("eval", _LUA_ENEMY_AGENTS), ("eval", _LUA_AP_ALL),
                         ("eval", _LUA_WAR_GRAPH)], timeout=40.0)
    prof["wave_a_ms"] = int((time.time() - t0) * 1000)
    camp = _parse_campaign(_bres(ra[0], "campaign_state"))
    prof["campaign_state_engine_ms"] = camp.pop("_eval_ms", None)
    camp["resources"] = _parse_resources(_bres(ra[1], "faction_resources", allow_nil=True))
    camp["campaign_map"] = campaign_map(bus, camp.get("campaign_uuid"))
    camp["presave_radius"] = _presave_radius()
    camp["selector"] = _selector()
    regs = _parse_regions(_bres(ra[2], "regions", allow_nil=True))
    rs = _ruins_of(regs)
    ruin_keys = {r["region"] for r in rs}
    world = {"armies": _mask_ruin_owners(ra[3].get("chars") or [], ruin_keys),
             "settlements": ra[4].get("setts") or [],
             "hostiles": _mask_ruin_owners(
                 [h for h in (ra[5].get("hostiles") or [])
                  if not (h.get("kind") == "settlement"
                          and str(h.get("region")) in ruin_keys)], ruin_keys),
             "ruins": rs,
             "regions": regs,
             "enemy_agents": _parse_enemy_agents(_bres(ra[8], "enemy_agents", allow_nil=True)),
             "war_graph": _parse_war_graph(_bres(ra[10], "war_graph", allow_nil=True))}
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
    reach_cqis = ([str(h["cqi"]) for h in world["hostiles"]
                   if h.get("kind") in ("army", "neutral_army") and h.get("cqi")]
                  + [str(a["cqi"]) for a in world["enemy_agents"] if a.get("cqi")]
                  + [str(a["cqi"]) for a in world["armies"] if a.get("cqi")])
    reach_cqis = sorted(set(reach_cqis), key=reach_cqis.index)
    reach_regions = ([str(h["region"]) for h in world["hostiles"]
                      if h.get("kind") == "settlement" and h.get("region")]
                     + [str(s["region"]) for s in world["settlements"] if s.get("region")]
                     + [str(r["region"]) for r in world["ruins"] if r.get("region")])
    reach_regions = sorted(set(reach_regions), key=reach_regions.index)

    wave_b = [("eval", _LUA_ANCILLARY_POOL % {"fac": camp["faction_cqi"]}),
              ("eval", _LUA_EQUIPPED_ALL)]
    for cqi in lords:
        wave_b += [("eval", _LUA_LORD % {"cqi": cqi}),
                   ("eval", _LUA_LORD_OFFERS % {"cqi": cqi}),
                   ("eval", _LUA_RECRUITABLE % {"cqi": cqi}),
                   ("eval", _reach_lua(cqi, reach_cqis, reach_regions)),
                   ("eval", _LUA_EQUIPPED % {"cqi": cqi}),
                   ("eval", _horde_slots_lua(cqi)),
                   ("eval", _LUA_MERC_POOLS % {"cqi": cqi})]
    for cqi in heroes:
        wave_b += [("eval", _LUA_LORD % {"cqi": cqi}),
                   ("eval", _LUA_HERO_OFFERS % {"cqi": cqi}),
                   ("eval", _reach_lua(cqi, reach_cqis, reach_regions)),
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
    anc_pool = _parse_ancillaries(_bres(rb[i], "ancillary_pool", allow_nil=True))
    i += 1
    equipped_all = _parse_ancillaries(_bres(rb[i], "equipped_all"))
    i += 1
    lord_state, hero_state, prov_state = {}, {}, {}
    for cqi in lords:
        st = _parse_lord(_bres(rb[i], "lord_state:%s" % cqi), cqi)
        stances, skills, hidden = _parse_stance_skills(
            _bres(rb[i + 1], "lord_blob:%s" % cqi, allow_nil=True))
        rc, rs_ = _parse_reach(_bres(rb[i + 3], "reach:%s" % cqi, allow_nil=True))
        st.update(stances=stances, skills=skills, hidden_skills=hidden,
                  recruitable=_parse_recruitable(
                      _bres(rb[i + 2], "recruitable:%s" % cqi, allow_nil=True)),
                  reach_chars=rc, reach_setts=rs_,
                  equipped=_parse_ancillaries(
                      _bres(rb[i + 4], "equipped:%s" % cqi, allow_nil=True)),
                  horde_slots=_parse_horde_slots(
                      _bres(rb[i + 5], "horde_slots:%s" % cqi, allow_nil=True)),
                  merc_pools=_parse_merc_pools(
                      _bres(rb[i + 6], "merc_pools:%s" % cqi, allow_nil=True)))
        lord_state[cqi] = st
        i += 7
    for cqi in heroes:
        st = _parse_lord(_bres(rb[i], "hero_state:%s" % cqi), cqi)
        rc, rs_ = _parse_reach(_bres(rb[i + 2], "hero_reach:%s" % cqi, allow_nil=True))
        st.update(_parse_hero_blob(_bres(rb[i + 1], "hero_blob:%s" % cqi, allow_nil=True)))
        st.update(reach_chars=rc, reach_setts=rs_,
                  equipped=_parse_ancillaries(
                      _bres(rb[i + 3], "equipped:%s" % cqi, allow_nil=True)))
        hero_state[cqi] = st
        i += 4
    for reg in regions:
        st = _parse_province(_bres(rb[i], "province_state:%s" % reg), reg)
        buildable, edicts = _parse_buildable(
            _bres(rb[i + 1], "province_offers:%s" % reg, allow_nil=True))
        st.update(buildable=buildable, edicts=edicts,
                  slot_states=_parse_slot_states(
                      _bres(rb[i + 2], "slot_states:%s" % reg, allow_nil=True)))
        prov_state[reg] = st
        i += 3
    camp_raw = _bres(rb[i], "campaign_offers", allow_nil=True) if want_camp else None

    wave_c, move_cqis = [], []
    for cqi in lords + heroes:
        st = lord_state.get(cqi) or hero_state.get(cqi)
        lua = _move_lua(cqi, st)
        if lua is not None:
            wave_c.append(("eval", lua))
            move_cqis.append(cqi)
    t0 = time.time()
    rc_ = bus.send_batch(wave_c, timeout=40.0) if wave_c else []
    prof["wave_c_ms"] = int((time.time() - t0) * 1000)
    for j, cqi in enumerate(move_cqis):
        st = lord_state.get(cqi) or hero_state.get(cqi)
        st["move_tiles"] = _parse_move_tiles(_bres(rc_[j], "moves:%s" % cqi, allow_nil=True))

    t0 = time.time()
    camp = dict(camp, hero_type_counts=_hero_type_counts(world))
    pools = (_lord_pools(bus, camp["faction_cqi"], _lord_subtypes(bus, camp["faction"]))
             if regions else {})
    prof["lord_pools_ms"] = int((time.time() - t0) * 1000)
    world["stationed"] = stationed

    ents = []
    for cqi in lords:
        ents.append({"context_kind": "lord", "context_id": str(cqi),
                     "state": lord_state[cqi]})
    for cqi in heroes:
        ents.append({"context_kind": "hero", "context_id": str(cqi),
                     "state": hero_state[cqi]})
    for reg in regions:
        ents.append({"context_kind": "province", "context_id": reg,
                     "state": prov_state[reg]})
    if want_camp:
        camp_state = dict(camp, anc_pool=anc_pool, equipped_all=equipped_all,
                          lord_pools=pools)
        camp_state.update(_parse_tech_rites(camp_raw))
        ents.append({"context_kind": "campaign", "context_id": camp["faction"],
                     "state": camp_state})
    prof["_entities"] = len(ents)
    prof["_lords"] = len(lords)
    prof["_heroes"] = len(heroes)
    prof["_regions"] = len(regions)
    prof["_wave_b_cmds"] = len(wave_b)
    prof["_wave_c_cmds"] = len(wave_c)
    camp["read_failures"] = _drain_try_fails()
    return {"ts": time.time(), "campaign": camp, "world": world,
            "entities": ents, "profile": prof}
