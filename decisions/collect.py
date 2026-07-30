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
    return {"campaign_id": p[0], "campaign_uuid": campaign_uuid(bus),
            "turn": _num(p[1]), "income": _num(p[2]), "settlements": _num(p[3]),
            "allies": _num(p[4]), "vassals": _num(p[5]), "power_rank": _num(p[6]), "ts": time.time()}


# ============================================================ CAMPAIGN IDENTITY
# WH3 exposes NO unique per-playthrough id. Enumerated exhaustively: all 515 CCO contexts / 6347
# functions (the only `Guid` is CcoComponent, a UI widget), and episodic_scripting/model_hierarchy
# contain no seed/guid/uuid at all. cm:get_campaign_name() is the campaign TYPE ("main_warhammer")
# and the faction name repeats on every restart -- so keying on either would merge two separate
# Nagarythe playthroughs into ONE turn series and silently corrupt the reward target.
#
# The engine clearly HAS one internally (autosave filenames carry a per-campaign uint32 that differs
# between playthroughs of the same faction) but does not expose it. So we MINT our own, using CA's
# own persistence primitive.
#
# LIVE-VERIFIED (all four, this build):
#   cm:random_number(1000000,1)                 -> 62008 then 5982   (varies; NOT bare math.random,
#                                                  which is deterministic ANSI rand and would mint
#                                                  the SAME id in two fresh campaigns)
#   cm:set_saved_value / cm:get_saved_value      -> round-trips
#   cm:get_cached_value(key, generator)          -> minted_24644 on the first call and the SAME
#                                                  value on the second -> mint-once semantics hold
# ⚠ the key must not contain ':' (hard script_error in lib_campaign_manager.lua).
CAMPAIGN_UUID_KEY = "tw_stack_campaign_uuid"

_LUA_UUID_EXPR = ("local ok,v=pcall(function() return cm:get_cached_value('%s', function() "
                  "local t={} for i=1,4 do t[i]=string.format('%%04x', cm:random_number(65535,0)) end "
                  "return cm:get_local_faction_name(true)..'_'..table.concat(t) end) end) "
                  "if ok and v then return tostring(v) end return 'NO-UUID'" % CAMPAIGN_UUID_KEY)
_LUA_UUID = _LUA_UUID_EXPR


def campaign_uuid(bus):
    """This playthrough's stable unique id, minted on first read and cached in the campaign.

    Returns None if the mechanism is unavailable -- the caller then falls back to the run-dir key
    rather than silently reusing a non-unique faction name.
    """
    v = _ev(bus, _LUA_UUID, timeout=25.0, allow_nil=True)
    return None if v in (None, "NO-UUID", "nil", "") else str(v)


# ============================================================ CAMPAIGN + WORLD (raw)
_LUA_CAMPAIGN = (_G + "local f=cm:get_local_faction(true) "
                 "return f:name()..'|'..cm:model():turn_number()..'|'..f:income()..'|'"
                 "..f:region_list():num_items()..'|'..cm:get_faction(cm:get_local_faction_name(true)):treasury()"
                 "..'|'..tostring(f:is_currently_researching())"
                 "..'|'..tostring(f:command_queue_index())"
                 # the uuid mint rides along: same round-trip, so identity is free
                 "..'|'..(function() " + _LUA_UUID_EXPR + " end)()")


def campaign_state(bus, with_uuid=True):
    p = str(_ev(bus, _LUA_CAMPAIGN)).split("|")
    if len(p) < 8:
        raise CollectError("campaign state malformed: %r" % p)
    uid = p[7]
    return {"faction": p[0], "turn": _num(p[1]), "income": _num(p[2]), "settlements": _num(p[3]),
            "treasury": _num(p[4]), "is_researching": p[5] == "true", "faction_cqi": p[6],
            "campaign_uuid": (None if uid in ("NO-UUID", "nil", "") else uid)}


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


# ⚠ THE HUD STANCE STACK IS NOT A LEGALITY WHITELIST. It was used as one and that was wrong:
# live, with an army selected, the stack reports ALL 13 stances as `active` -- TUNNELING included,
# which High Elves can never use. (The earlier reading where MARCH/DEFAULT looked `inactive` was an
# artefact of nothing being selected, not legality.) Reading it cost 14 bus round-trips -- 40% of
# every collect -- and contributed nothing, because the offer was AND-ed with CanBeActivated, which
# was silently carrying the entire gate.
#
# cco StanceList.CanBeActivated IS the real signal. On one lord at one AP it discriminates per
# stance (DEFAULT/MARCH/AMBUSH/CHANNELING true; TUNNELING/SET_CAMP/MUSTER/DOUBLE_TIME false), and it
# tracks the dynamic locks too -- movement exhausted, or stuck in a stance once recruitment starts.
# So it is neither "just an AP gate" nor cacheable per turn; it is read fresh in the eval
# lord_offers already makes, for zero extra round-trips.


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


def _reach(bus, cqi, target_cqis, regions):
    """({cqi: bool}, {region: bool}) -- BOTH reachability sweeps in ONE round-trip.

    Uses the campaign_manager WRAPPERS: the raw model predicates return FALSE POSITIVES when the
    character has no action points. Was two evals; a bus round-trip is a flat ~101ms whatever it
    carries, so merging the loops is free.
    """
    if not target_cqis and not regions:
        return {}, {}
    raw = _ev(bus, "local a=cm:get_character_by_cqi(%s) local o={} "
                   "for t in string.gmatch('%s','[^,]+') do local c=cm:get_character_by_cqi(tonumber(t)) "
                   "local ok,v=pcall(function() return cm:character_can_reach_character(a,c) end) "
                   "o[#o+1]='C'..t..'='..tostring(ok and v) end "
                   "for t in string.gmatch('%s','[^,]+') do local r=cm:get_region(t) "
                   "local s=r and r:settlement() "
                   "local ok,v=pcall(function() return cm:character_can_reach_settlement(a,s) end) "
                   "o[#o+1]='R'..t..'='..tostring(ok and v) end return table.concat(o,',')"
              % (cqi, ",".join(str(t) for t in target_cqis), ",".join(regions)), timeout=40.0,
              allow_nil=True)
    chars, setts = {}, {}
    for part in str(raw or "").split(","):
        if "=" not in part:
            continue
        k, v = part.rsplit("=", 1)
        (chars if k.startswith("C") else setts)[k[1:]] = (v == "true")
    return chars, setts


# stances AND skills for one character in ONE round-trip (both are cco reads on the same object).
_LUA_LORD_OFFERS = (_G +
    "local c=cco('CcoCampaignCharacter','%(cqi)s') local mf=g(c,'MilitaryForceContext') "
    "local st={} if mf then local l=g(mf,'StanceList') "
    "if type(l)=='table' then for i=1,#l do local v=l[i] st[#st+1]=ts(g(v,'Key'))"
    "..'~'..ts(g(v,'IsActive'))..'~'..ts(g(v,'CanBeActivated'))..'~'..ts(g(v,'CanAfford')) end end end "
    "local sk={} local s=g(c,'SkillList') "
    "if type(s)=='table' then for i=1,#s do sk[#sk+1]=ts(g(s[i],'Key'))..'~'..ts(g(s[i],'Status')) end end "
    "return table.concat(st,',')..'||'..table.concat(sk,',')")


def lord_offers(bus, cqi, state, world):
    """Every lord-context offer, available or not (unavailable ones carry the game's gate reason).

    MOVES cover EVERY enemy army, EVERY enemy settlement and EVERY own settlement the game reports,
    each with the engine's own reachability verdict -- no "nearest N" pre-selection here, because
    choosing between local targets is the advisor's job, not the recorder's.
    """
    offers = []
    acted = state.get("acted")
    # -- stances. CanBeActivated is the game's own per-stance verdict and already covers faction
    # legality AND the dynamic locks (no movement left, stuck mid-recruitment) -- see the note above
    # the deleted legal_stances().
    raw = str(_ev(bus, _LUA_LORD_OFFERS % {"cqi": cqi}, timeout=25.0, allow_nil=True) or "")
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
    # -- recruitable units (units_panel must be open; an empty list is a legitimate answer)
    for c in recruitable_units(bus):
        offers.append(_offer("recruit_unit", c["key"], c.get("state") == "active",
                             None if c.get("state") == "active" else c.get("state")))
    # -- skills
    has_pts = (state.get("skill_points") or 0) >= 1
    for row in sk_raw.split(","):
        if "~" not in row:
            continue
        key, status = row.rsplit("~", 1)
        ok = (status == "active" and has_pts)
        offers.append(_offer("skills", key, ok, None if ok else ("no_points" if not has_pts else status)))
    # -- MOVES: attack armies / attack settlements / garrison
    armies = [h for h in world["hostiles"] if h.get("kind") == "army" and h.get("cqi")]
    esetts = [h for h in world["hostiles"] if h.get("kind") == "settlement" and h.get("region")]
    osetts = [s for s in world["settlements"] if s.get("region")]
    # BOTH reachability sweeps in ONE round-trip (they were two evals, ~202ms)
    reach_c, reach_s = _reach(bus, cqi, [a["cqi"] for a in armies],
                              [s["region"] for s in esetts] + [s["region"] for s in osetts])
    for a in armies:
        ok = bool(reach_c.get(str(a["cqi"]))) and not acted
        offers.append(_offer("attack_army", "cqi:%s" % a["cqi"], ok,
                             None if ok else ("already_acted_this_turn" if acted else "cannot_reach"),
                             target_cqi=a["cqi"], target_faction=a.get("faction"),
                             x=a.get("x"), y=a.get("y")))
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
    # leave_garrison is DELIBERATELY NOT OFFERED until it has a real destination.
    # ⚠ cm:leave_garrison(char, x, y) is a MOVE ORDER, not a toggle. Offering it with the lord's
    # OWN position as the destination (which is what "just leave" naively looks like) made the
    # engine resolve a move into adjacent enemy ground and raise the modal "Declare War?" dialog --
    # which then blocked every subsequent action for the rest of the turn. Live-verified: lord 56
    # captured Shrine of Ladrielle at (224,777), was offered leave_garrison to (224,777), and the
    # whole run stalled behind that popup.
    # Re-add only with an explicit, validated destination (a chosen adjacent tile), never self.
    offers.append(_offer("noop", "noop", True))
    return offers


def province_offers(bus, region, state, campaign):
    """Every province-context offer (buildings across free slots, edicts, lord recruitment)."""
    offers = []
    # ONE round-trip for the whole province: the buildable list and the province's edict records.
    #
    # ⚠ A SLOT MUST BE **IsActive** OR EVERY Construct ON IT IS SILENTLY REFUSED.
    # A locked slot is NOT distinguishable from a free one by the obvious properties: it is in
    # BuildingSlotList, reports IsEmpty=true, and returns a full PossibleUpgradeWithoutConversions
    # list with BuildingRequirementsMet=true for EVERY entry -- and then eats the command. CA's own
    # UI gates the slot on exactly this (settlement_building_slot.twui.xml:
    # <property name="locked" value="IsActive == false"/>, tooltip "This slot will unlock when you
    # upgrade your main settlement chain building to level %d").
    # Live-confirmed: SlotActivateLevel <= primary settlement level == IsActive.
    #   The Monoliths (settlement_minor_2):  idx0/1/2 active, idx3 (unlock 3) LOCKED
    #   Shrine of Ladrielle (minor_1):       idx0/1 active, idx2+idx3 LOCKED
    # This cost ~5 wasted actions per turn, all reported as command_silently_refused.
    #
    # NB there is NO "one construction per settlement per turn" rule -- that earlier theory is
    # ruled out by full enumeration of the shipped API (no such property, loc string or UI gate).
    # Genuine in-progress state lives on slot.ConstructionItemContext (TurnsToCompletion etc.).
    combo = str(_ev(bus, _G +
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
              "return 'false||'..table.concat(o,',')..'||'..table.concat(ed,',')"
              % region, timeout=30.0, allow_nil=True) or "")
    cparts = combo.split("||")
    if len(cparts) < 3:
        raise CollectError("province offers malformed for %s: %r" % (region, combo[:120]))
    raw = cparts[1]
    edicts = [k for k in cparts[2].split(",") if k and k != "nil"]
    # ⚠ THE GATE IS `IsActiveForBuildingBrowser(slot)`, NOT `BuildingRequirementsMet`.
    # CA's shipped UI never calls BuildingRequirementsMet anywhere (0 uses across all 867 ui3.pack
    # files); it decides lit-vs-greyed with, verbatim from building_construction_popup.twui.xml:
    #     normal = level.IsActiveForBuildingBrowser(slot) && level.IsBuiltInSlot(slot) == false
    # BuildingRequirementsMet only checks dependency buildings -- necessary, never sufficient --
    # which is exactly why our offers leaked and Construct kept being silently refused.
    # IsActiveForBuildingBrowser is the aggregate: it folds in affordability, growth/development
    # points, damage, caps, tech locks and the rest.
    #
    # ARGUMENT SYNTAX: `this` is the ROOT of the Call (the slot). Do NOT write `Context` -- that is
    # a TYPE NAME in the docs, not a value, and evaluating it CTD'd the game once.
    #
    # An occupied ACTIVE slot is still actionable: upgrading it is how a settlement grows, and
    # upgrading the PRIMARY building is the only way to unlock the level-locked slots. Offering
    # construction on empty slots alone left the advisor structurally unable to develop a province.
    seen = set()
    for row in str(raw or "").split(","):
        p = row.split("~")
        if len(p) < 5:
            continue
        slot, key, active, empty, canup = p[0], p[1], p[2] == "true", p[3] == "true", p[4] == "true"
        if key in seen:
            continue
        seen.add(key)
        ok = active                      # the game's own lit-vs-greyed verdict
        gate = None if ok else ("not_buildable_now" if empty else "not_upgradeable_now")
        offers.append(_offer("building", key, ok, gate,
                             slot_index=int(float(slot)) if slot not in ("nil", "") else None,
                             is_upgrade=(not empty)))
    # -- edicts: the province must be FULLY OWNED for the commandment stack to exist at all.
    # Live-proven: on a partly-owned province InitiativeList still lists the 5 edict records, but the
    # HUD stack has no buttons and nothing can be clicked -- offering them would hand the advisor
    # options it can never take.
    complete = bool(state.get("complete_owner"))
    sel = state.get("selected_edict")
    for key in edicts:
        ok = complete and key != sel
        offers.append(_offer("edict", key, ok,
                             None if ok else ("province_not_complete" if not complete else "already_selected")))
    # -- lord recruitment: the pool is read data-side (no panel needed) via DatabaseRecordContext,
    # all subtypes in ONE round-trip
    for sub, (n, can) in _lord_pools(bus, campaign["faction_cqi"],
                                     _lord_subtypes(campaign["faction"])).items():
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


def _lord_pools(bus, faction_cqi, subtypes):
    """{subtype: (pool_size, [CanRecruitCharacter...])} for ALL subtypes in ONE round-trip.

    Was one eval per subtype (4 trips, ~400ms). Every bus round-trip costs a flat ~101ms whatever
    it carries, so looping subtypes inside the Lua is free. The Call arguments are still inline
    expression TEXT built by string concatenation -- never a Lua cco wrapper, which would hang the
    engine.
    """
    if not subtypes:
        return {}
    lst = ",".join("'%s'" % s for s in subtypes)
    raw = _ev(bus, _G + "local f=cco('CcoCampaignFaction','%s') local out={} "
                        "for _,sub in ipairs({%s}) do "
                        "local e='CharacterRecruitmentPoolEntriesForAgentSubtype(DatabaseRecordContext(\"CcoAgentSubtypeRecord\",\"'..sub..'\"))' "
                        "local ok,n=pcall(function() return f:Call(e..'.Size') end) "
                        "if not ok or not n then n=0 end local o={} "
                        "for i=0,n-1 do o[#o+1]=ts(f:Call(e..'['..i..'].CanRecruitCharacter')) end "
                        "out[#out+1]=sub..'='..n..':'..table.concat(o,',') end "
                        "return table.concat(out,';;')" % (faction_cqi, lst), timeout=30.0,
             allow_nil=True)
    out = {}
    for chunk in str(raw or "").split(";;"):
        if "=" not in chunk or ":" not in chunk:
            continue
        sub, rest = chunk.split("=", 1)
        n, flags = rest.split(":", 1)
        try:
            out[sub] = (int(float(n)), [f == "true" for f in flags.split(",") if f])
        except ValueError:
            out[sub] = (0, [])
    return out


# ⚠ CanResearch is the GAME'S OWN verdict and the only correct availability test. IsResearched
# alone is not: it ignores PREREQUISITES, so every locked tier-2..5 node reads "available" and
# StartResearching then silently refuses. Live-verified on a real faction: of 73 techs, 72 have
# CanResearch=false and exactly ONE is researchable -- the old test offered 75.
# ONE round-trip for the whole campaign context: current research + every tech + every rite.
# Split across three evals this cost ~303ms; a bus round-trip is a flat ~101ms whatever it carries,
# so the loops are free and only the trip count mattered.
_LUA_CAMPAIGN_OFFERS = (_G +
    "local f=cco('CcoCampaignFaction','%(fac)s') local m=g(f,'TechnologyManagerContext') "
    "local cur='none' if m then local c=g(m,'CurrentResearchingTechnologyContext') "
    "if c then cur=ts(g(c,'NodeKey')) end end "
    "local tech={} local l=m and g(m,'TechnologyList') "
    "if type(l)=='table' then for i=1,#l do tech[#tech+1]=ts(g(l[i],'NodeKey'))"
    "..'~'..ts(g(l[i],'IsResearched'))..'~'..ts(g(l[i],'CanResearch')) end end "
    "local rites={} local r=g(f,'AvailableRitualList') "
    "if type(r)=='table' then for i=1,#r do rites[#rites+1]=ts(g(r[i],'CanPerformRitual')) end end "
    "return cur..'||'..table.concat(tech,',')..'||'..table.concat(rites,',')")


def current_research(bus, faction_cqi):
    """The tech actually being researched right now, or None.

    Kept as its own call for the LAUNCHER's confirm path (campaign_offers gets it inline). It
    matters because StartResearching on a prerequisite-locked node does not simply fail: the engine
    starts the first researchable node on the path instead (live -- asking for hef_5_01 left
    hef_5_00 in progress), so what IS researching has to stay visible rather than looking inert.
    """
    v = _ev(bus, _G + "local m=g(cco('CcoCampaignFaction','%s'),'TechnologyManagerContext') "
                      "local c=m and g(m,'CurrentResearchingTechnologyContext') "
                      "if c then return ts(g(c,'NodeKey')) end return 'none'" % faction_cqi,
            timeout=20.0, allow_nil=True)
    return None if v in (None, "none", "nil") else str(v)


def campaign_offers(bus, campaign):
    """Faction-wide offers: research, rites, and END TURN.

    end_turn is a first-class prediction, not the loop's own act: the turn ends when the advisor
    ranks end_turn top, which is why it has to be in the offer set like everything else.
    """
    offers = []
    fac = campaign["faction_cqi"]
    raw = str(_ev(bus, _LUA_CAMPAIGN_OFFERS % {"fac": fac}, timeout=30.0, allow_nil=True) or "")
    parts = raw.split("||")
    if len(parts) < 3:
        raise CollectError("campaign offers malformed: %r" % raw[:120])
    current, tech_raw, rites_raw = parts[0], parts[1], parts[2]
    current = None if current in ("none", "nil", "") else current
    # CanResearch alone decides. Do NOT also require "not currently researching": the game allows
    # SWITCHING the active tech, and it says so -- live-verified with hef_0_00 in progress while
    # hef_5_00 still reported CanResearch=true. Adding a not-researching clause would suppress a
    # legal action, and whether switching is a good idea is the model's call, not a gate's.
    for row in tech_raw.split(","):
        p = row.split("~")
        if len(p) < 3:
            continue
        key, done, can = p[0], p[1] == "true", p[2] == "true"
        gate = None if can else ("researched" if done else
                                 "in_progress" if key == current else "prerequisites_not_met")
        offers.append(_offer("research", key, can, gate, in_progress=(key == current)))
    for i, flag in enumerate(rites_raw.split(",")):
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
    prof = {}                       # per-phase ms; the loop's biggest cost lives in here

    def timed(name, fn, *a, **k):
        t = time.time()
        try:
            return fn(*a, **k)
        finally:
            prof[name] = prof.get(name, 0) + int((time.time() - t) * 1000)

    camp = timed("campaign_state", campaign_state, bus)
    world = timed("world_state", world_state, bus)
    lords = [str(c.get("cqi")) for c in world["armies"] if c.get("has_army") and c.get("is_general")]
    regions = [s["region"] for s in world["settlements"] if s.get("region")]
    want_camp = True
    if active is not None:
        lords = [c for c in lords if c in set(str(x) for x in (active.get("lords") or []))]
        regions = [r for r in regions if r in set(active.get("regions") or [])]
        want_camp = bool(active.get("campaign", True))
    ents = []
    for cqi in lords:
        st = timed("lord_state", lord_state, bus, cqi)
        ents.append({"context_kind": "lord", "context_id": str(cqi), "state": st,
                     "offers": timed("lord_offers", lord_offers, bus, cqi, st, world)})
    for reg in regions:
        st = timed("province_state", province_state, bus, reg)
        ents.append({"context_kind": "province", "context_id": reg, "state": st,
                     "offers": timed("province_offers", province_offers, bus, reg, st, camp)})
    if want_camp:
        ents.append({"context_kind": "campaign", "context_id": camp["faction"], "state": dict(camp),
                     "offers": timed("campaign_offers", campaign_offers, bus, camp)})
    prof["_entities"] = len(ents)
    prof["_lords"] = len(lords)
    prof["_regions"] = len(regions)
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
