-- twstate.lua -- FULL passive campaign-model scrape, once per turn. RECORDS ONLY.
--
-- Packed to script/campaign/mod/twstate.lua; the campaign script loader picks it up.
--
-- ================================ THE STANDING RULE ================================
-- ‼ NO FILTERING. NOT ONE. EVERYTHING GOES IN.
--   "you don't want to prerestrict information. you should keep full logs, and as most data as
--    couple possiblty bve relavant ... then you can patrse through all the raw data and find
--    patterns or categories of actions"
--   "there cannot be anythig missed again" / "ecverything must be there" / "the full cm scraping"
--
--   This has been violated repeatedly and it is what made the first 4 runs unusable as build
--   orders. Two drafts of THIS FILE filtered:
--     draft 1: `if mf:has_general()` dropped generalless forces; `cqi ~= char_cqi` dropped the
--              general from the roster; walking military_force_list only dropped LONE HEROES.
--   Filter at READ time. Never at WRITE time. A byte not written is gone forever, and the whole
--   point of a raw log is that a better question can be asked of it later.
--
-- WHY: a map right-click is move|attack|hero.embed|garrison and the game logs NO ui component
-- for it. A build order with unknowns is not a build order -- "build orders have to be 100%".
-- So the act is resolved BY OUTCOME, from diffing consecutive turns of this dump:
--     hero.embed = a force's character set GAINED a member    army.move = x,y changed
--     hero.detach = it LOST one                               army.attack = a battle followed
-- WH3's own `embedded character subtypes` fires ~25x per 17-turn run, incidentally. Unusable.
-- (Console logging is ALREADY on: data/script/enable_console_logging, Jul 8. Not the fix.)
--
-- SAFETY -- must never affect play:
--   * reads model state only. NO cm:* mutators, no orders, nothing that touches the sim.
--   * one listener on WorldStartRound. No polling, no per-tick work.
--   * every single accessor is pcall-wrapped: an API that does not exist on this patch yields
--     null for that field and the scrape continues. A fault here cannot reach the campaign.
--   * writes via out() to the script log, which the recorder already tails byte-exact.
--   * NO bare-name find_uicomponent, NO whole-tree walks -- the known CTD path.

local function q(s) return '"' .. tostring(s or ""):gsub('"', '\\"') .. '"' end

-- try(f) -> value or nil. Every read goes through this so a missing API is a null field,
-- never a crash and never a silently dropped row.
local function try(f)
  local ok, v = pcall(f)
  if ok then return v end
  return nil
end

-- nn(x) -> x if it is a LIVE (non-null) script interface, else nil.
-- ‼ CRASH CLASS pcall/try CANNOT CATCH: calling a real method on a NULL_SCRIPT_INTERFACE (e.g.
-- general_character() on a generalless force, horde_details() on a non-horde, region() on an
-- at-sea character) dereferences a null pointer C-side and HARD-CRASHES the engine -- there is no
-- Lua error for try() to trap. This is exactly what CTD'd the campaign on load during the full-map
-- baseline scrape. is_null_interface() IS safe to call on a null interface (it is the designated
-- test), so every NEW chained accessor that can yield a null interface routes through nn() before
-- the next method call. Only the newly-added chains below use it; proven-safe original accessors
-- are left untouched. ‼ USE ONLY ON ENTITY interfaces (general_character/horde_details/managers) --
-- NOT on LIST returns (resources()/slot_list()/effect_bundles()/active_rituals()): CA list objects
-- do not all implement is_null_interface(), so nn() would nil them and silently drop every row. A
-- list obtained from an already-nn()-guarded manager is safe to iterate directly.
local function nn(x)
  if x == nil then return nil end
  local ok, isnull = pcall(function() return x:is_null_interface() end)
  if ok and isnull == false then return x end
  return nil
end

-- is_horde_force(mf) -> true iff this military force is a horde army. ‼ On this live patch the
-- HORDE_DETAILS interface does NOT implement is_null_interface() (the archived scripting_doc lists
-- it, but the shipped build omits it), so the usual `not mf:horde_details():is_null_interface()`
-- ERRORS on a real horde and yields null. Detect by that very asymmetry, CONFIRMED LIVE: a horde's
-- horde_details() returns a HORDE_DETAILS (is_null_interface absent -> pcall fails -> horde); a
-- non-horde's returns NULL_SCRIPT_INTERFACE (is_null_interface present, returns true -> not horde).
local function is_horde_force(mf)
  local hd = try(function() return mf:horde_details() end)
  if hd == nil then return false end
  local ok, isnull = pcall(function() return hd:is_null_interface() end)
  if not ok then return true end
  return isnull == false
end

local function jv(v)
  if v == nil then return "null" end
  if type(v) == "number" then return tostring(v) end
  if type(v) == "boolean" then return tostring(v) end
  return q(v)
end

local function emit(t) out("TWSTATE " .. t) end

-- ‼ RECORDER/MOD VERSION. The user: "the data should tell us which recorder version it was taken
-- with, so we can filter." Unversioned data on disk is v1; the 4 gap-closers were v2. THIS build
-- (v3) closes the full-roster audit gaps: pooled maximum_value on the faction+region loops,
-- region effect_bundles, province_pooled corruption dump, foreign_slot buildings, faction/region/
-- force plague state, faction trade_value, the exact Lizardmen/Kislev event names, and the
-- PooledResourceChanged/per-race event-context accessors. A `{"kind":"version","twstate":"dev"}`
-- row is emitted once at arm time so every run self-identifies. Bump on any change to what/how it
-- records. ‼ recorder-v5 Task 1: this is now a STRING ("dev") -- multi-scrape-per-turn cadence
-- (turn-start full + turn-end full + between-player-action light). The version emit uses jv() so a
-- string is valid JSON (jv quotes strings, leaves numbers bare -- see the emit in twstate()).
local TWSTATE_VERSION = "v5"

-- ---------------------------------------------------------------- characters
local function dump_char(turn, fkey, c, role, mf_cqi)
  local cqi = try(function() return c:command_queue_index() end)
  if cqi == nil then return end
  local parts = {
    '"kind":"char"', '"turn":' .. turn, '"faction":' .. q(fkey),
    '"role":' .. q(role), '"mf_cqi":' .. jv(mf_cqi), '"char_cqi":' .. jv(cqi),
    '"subtype":' .. jv(try(function() return c:character_subtype_key() end)),
    '"type":' .. jv(try(function() return c:character_type_key() end)),
    '"forename":' .. jv(try(function() return c:get_forename() end)),
    '"fm_cqi":' .. jv(try(function() return c:family_member():command_queue_index() end)),
    '"x":' .. jv(try(function() return c:logical_position_x() end)),
    '"y":' .. jv(try(function() return c:logical_position_y() end)),
    '"rank":' .. jv(try(function() return c:rank() end)),
    '"wounded":' .. jv(try(function() return c:is_wounded() end)),
    '"has_force":' .. jv(try(function() return c:has_military_force() end)),
    '"embedded":' .. jv(try(function() return c:is_embedded_in_military_force() end)),
    -- FIX(audit): CHARACTER has NO is_garrison_commander() on this patch (invented -> always null).
    -- Doc: has_garrison_residence() "Is the character contained in a garrison residence?" (bool).
    -- is_governor() is a distinct, real fact worth keeping alongside. Old call kept as null fallback.
    '"garrisoned":' .. jv(try(function() return c:has_garrison_residence() end)),
    '"is_governor":' .. jv(try(function() return c:is_governor() end)),
    '"garrisoned_old":' .. jv(try(function() return c:is_garrison_commander() end)),
    '"region":' .. jv(try(function() return c:region():name() end)),
    -- ‼ THE FIELDS BELOW EXIST BECAUSE OF THE USER'S QUESTION:
    --   "if i do another hero action than move or embed, will we have to throw away all the
    --    past data again as we add that to these listers?"
    -- The asymmetry that answers it:
    --   * EVENTS are cheap to add later -- they are named, and all 435 verified names are
    --     already registered, incl. ScriptEventAgentActionSuccess/FailureAgainstCharacter and
    --     ...AgainstGarrison. A hero action already fires and is already logged.
    --   * STATE FIELDS are NOT cheap to add later -- a field not dumped is gone, and adding it
    --     means re-recording. So every field that could witness ANY action goes in NOW.
    -- These five were missing and each one witnesses actions that leave no other trace:
    --   action/movement points -> a hero action consumes ALL remaining movement (wiki), so
    --     spent points are the fingerprint of an action whose target we cannot otherwise see
    --   cooldown/loyalty -> loyalty is build-relevant on its own (Skaven/DE/VCoast lords rebel
    --     at 0, and equipping items/winning battles raises it)
    -- FIX(audit): action_points_remaining() and percentage_of_action_points_remaining() DO NOT EXIST
    -- on CHARACTER (invented -> both null). The doc's only remaining-movement getter is
    -- action_points_remaining_percent() (movement % left); action_points_per_turn() is the per-turn
    -- max. can_move: has_movement_points() also does not exist -> derive from the % (read-only, no
    -- mutation). Old invented calls kept as null fallbacks.
    '"action_points_pct":' .. jv(try(function() return c:action_points_remaining_percent() end)),
    '"action_points_per_turn":' .. jv(try(function() return c:action_points_per_turn() end)),
    '"action_points":' .. jv(try(function() return c:action_points_remaining() end)),
    '"loyalty":' .. jv(try(function() return c:loyalty() end)),
    '"can_move":' .. jv(try(function() return c:action_points_remaining_percent() > 0 end)),
    '"can_move_old":' .. jv(try(function() return c:has_movement_points() end)),
    -- FIX(audit): is_in_settlement() does not exist; the real CHARACTER method is in_settlement().
    '"in_settlement":' .. jv(try(function() return c:in_settlement() end)),
    '"in_settlement_old":' .. jv(try(function() return c:is_in_settlement() end)),
    '"at_sea":' .. jv(try(function() return c:is_at_sea() end)),
    '"faction_leader":' .. jv(try(function() return c:is_faction_leader() end)),
  }
  -- skills / ancillaries / traits: the actual build content for a lord. All of it.
  -- FIX(audit): CHARACTER has NO character_skill_points_list() and the script API exposes NO bulk
  -- skill enumeration at all (only has_skill(key) and background_skill()). skills[] therefore stays
  -- [] as STATE; the skill BUILD is reconstructed from CharacterSkillPointAllocated events
  -- (already listened, ctx dumps skill_point_spent_on). Invented call kept as a null fallback, and
  -- the one enumerable skill fact -- background_skill() (String key) -- is added.
  local sk = {}
  try(function()
    local l = c:character_skill_points_list()
    for i = 0, l:num_items() - 1 do sk[#sk + 1] = q(l:item_at(i)) end
  end)
  parts[#parts + 1] = '"skills":[' .. table.concat(sk, ",") .. "]"
  parts[#parts + 1] = '"background_skill":' .. jv(try(function() return c:background_skill() end))
  -- FIX(audit): CHARACTER has NO ancillary_list() and there is NO ANCILLARY script interface on this
  -- patch, so c:ancillary_list():item_at():ancillary_key() was fully invented -> []. The real
  -- enumeration of a character's equipped items is the ARMORY:
  --   c:family_member():armory():get_currently_registered_armory_items() -> Lua String Table of item
  --   keys (doc: FAMILY_MEMBER.armory() -> ARMORY; ARMORY.get_currently_registered_armory_items()
  --   "Return: String table"). Invented path kept as a null fallback.
  local anc = {}
  try(function()
    local fm = nn(c:family_member()); if not fm then return end
    local ar = nn(fm:armory()); if not ar then return end
    local items = ar:get_currently_registered_armory_items()
    if type(items) == "table" then
      for i = 1, #items do anc[#anc + 1] = q(items[i]) end
    end
  end)
  try(function()
    local l = c:ancillary_list()
    for i = 0, l:num_items() - 1 do
      anc[#anc + 1] = q(try(function() return l:item_at(i):ancillary_key() end))
    end
  end)
  parts[#parts + 1] = '"ancillaries":[' .. table.concat(anc, ",") .. "]"
  -- ADDED(audit, doc-verified): effect bundles active on the character (traits granted as bundles,
  -- item/skill effects, campaign buffs). CHARACTER.effect_bundles() -> EFFECT_BUNDLE_LIST; each
  -- EFFECT_BUNDLE has key() (String record key). Overcollect -- another witness of build state.
  local eb = {}
  try(function()
    local l = c:effect_bundles(); if not l then return end
    for i = 0, l:num_items() - 1 do eb[#eb + 1] = q(try(function() return l:item_at(i):key() end)) end
  end)
  parts[#parts + 1] = '"effect_bundles":[' .. table.concat(eb, ",") .. "]"
  local tr = {}
  try(function()
    -- c:trait_list() is a dead invented method (always [] -- proven: LLs that must have traits
    -- showed empty). c:all_traits() is the verified-candidate; shape unknown (list-interface vs
    -- Lua table), so handle BOTH and confirm live.
    local l = c:all_traits()
    if type(l) == "table" then
      for i = 1, #l do tr[#tr + 1] = q(l[i]) end
    else
      for i = 0, l:num_items() - 1 do tr[#tr + 1] = q(l:item_at(i)) end
    end
  end)
  parts[#parts + 1] = '"traits":[' .. table.concat(tr, ",") .. "]"
  emit("{" .. table.concat(parts, ",") .. "}")
end

-- ---------------------------------------------------------------- forces + units
local function dump_force(turn, fkey, mf)
  local mf_cqi = try(function() return mf:command_queue_index() end)
  if mf_cqi == nil then return end
  -- ‼ HORDE (TASK 2): the ARMY *is* the settlement for hordes (Beastmen/Norsca/WoC/Ogres/Wood Elves).
  -- Detection via is_horde_force() -- the naive is_null_interface() check ERRORS on this patch (see
  -- the helper). Verified live: true for the Beastmen force, false for a non-horde (daemon_prince).
  local is_horde = is_horde_force(mf)
  emit("{" .. table.concat({
    '"kind":"force"', '"turn":' .. turn, '"faction":' .. q(fkey),
    '"mf_cqi":' .. jv(mf_cqi),
    '"has_general":' .. jv(try(function() return mf:has_general() end)),
    '"stance":' .. jv(try(function() return mf:active_stance() end)),
    '"is_army":' .. jv(try(function() return mf:is_army() end)),
    '"upkeep":' .. jv(try(function() return mf:upkeep() end)),
    '"units":' .. jv(try(function() return mf:unit_list():num_items() end)),
    -- systematic enumeration. Movement is the fingerprint of an action: the wiki states a hero
    -- Target action "will always use up any remaining movement", so spent points witness an
    -- action even when its target is invisible to us.
    '"is_navy":' .. jv(try(function() return mf:is_navy() end)),
    -- FIX(audit): percentage_of_action_points_remaining() does not exist on CHARACTER; the real
    -- getter is action_points_remaining_percent(). Old call kept as null fallback.
    '"mp_pct":' .. jv(try(function() local g = nn(mf:general_character()); return g and g:action_points_remaining_percent() end)),
    '"mp_pct_old":' .. jv(try(function() local g = nn(mf:general_character()); return g and g:percentage_of_action_points_remaining() end)),
    -- FIX(audit): MILITARY_FORCE has NO region() (invented -> null). A force's location comes from
    -- its general's region, or -- for generalless/garrison forces -- its garrison residence's region.
    '"region":' .. jv(try(function() local g = nn(mf:general_character()); local rg = g and nn(g:region()); return rg and rg:name() end)),
    '"region_garrison":' .. jv(try(function() local gr = nn(mf:garrison_residence()); local rg = gr and nn(gr:region()); return rg and rg:name() end)),
    '"region_old":' .. jv(try(function() return mf:region():name() end)),
    -- ADDED(audit, doc-verified): systematic MILITARY_FORCE enumeration.
    '"strength":' .. jv(try(function() return mf:strength() end)),
    '"morale":' .. jv(try(function() return mf:morale() end)),
    '"force_type":' .. jv(try(function() local ft = nn(mf:force_type()); return ft and ft:key() end)),
    '"is_armed_citizenry":' .. jv(try(function() return mf:is_armed_citizenry() end)),
    '"contains_mercenaries":' .. jv(try(function() return mf:contains_mercenaries() end)),
    '"recruitment_item_count":' .. jv(try(function() return mf:recruitment_item_count() end)),
    -- ‼ HORDE flag (TASK 2).
    '"is_horde":' .. jv(is_horde),
  }, ",") .. "}")
  -- ‼ HORDE FORCE-BUILDINGS (TASK 2). Hordes build IN the mobile force, not a region. The archived
  -- scripting_doc claims HORDE_DETAILS.slot_list() gives the building slots, but PROBED LIVE on a
  -- Beastmen horde every building/slot accessor is ABSENT: HORDE_DETAILS exposes only model() and
  -- military_force(); MILITARY_FORCE has no building_list/buildings/num_buildings/slot_list/
  -- slot_manager. So the horde building STATE is NOT reachable via script on this patch -- there is
  -- no force_slot dump. Horde construction is instead captured by the registered
  -- MilitaryForceBuildingCompleteEvent (each completion, event-time) -- the same way tech is captured
  -- via ResearchCompleted rather than a state enumeration. Restore a slot loop here iff a future
  -- patch adds the accessor. Horde per-race resources (bst_ruination/bst_dread/bst_herdstone_shard)
  -- are already captured by the faction "pooled" rows; the horde army itself by the force row above.
  -- ‼ HORDE GROWTH / DEVELOPMENT + any per-force pooled currency (TASK 2). MILITARY_FORCE has its
  -- own pooled_resource_manager (doc-verified); horde growth/development points and force-scoped
  -- currencies are pooled resources here -- the STATE counterpart of MilitaryForceDevelopmentPoint
  -- Change. Same shape as the faction pooled dump. Emitted for ALL forces (null-safe); hordes are
  -- where it populates. No filtering.
  try(function()
    local prm = nn(mf:pooled_resource_manager()); if not prm then return end
    local pl = prm:resources(); if not pl then return end
    for i = 0, pl:num_items() - 1 do
      local pr = pl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"force_pooled"', '"turn":' .. turn, '"faction":' .. q(fkey),
        '"mf_cqi":' .. jv(mf_cqi),
        '"resource":' .. jv(try(function() return pr:key() end)),
        '"value":' .. jv(try(function() return pr:value() end)),
        '"max":' .. jv(try(function() return pr:maximum_value() end)),
      }, ",") .. "}")
    end
  end)
  -- ‼ ARMY PLAGUE INFECTION (FIX audit v3, Nurgle). MILITARY_FORCE.get_plague_if_infected() and
  -- try_get_military_force_plague() both return a PLAGUE or NULL (PLAGUE implements is_null_interface
  -- -> nn()-guard). PROBE-CONFIRMED live (both return cleanly, MF_NOT_INFECTED at turn 1 -> populate
  -- when a force is plague-carrying). plague_record() is the plague key; plague_components() the
  -- symptom keys; creator_faction() who cast it. Emitted null-safe for all forces.
  try(function()
    local pg = nn(mf:get_plague_if_infected()) or nn(try(function() return mf:try_get_military_force_plague() end))
    if not pg then return end
    local comps = {}
    try(function()
      local cl = pg:plague_components()
      for i = 0, cl:num_items() - 1 do comps[#comps + 1] = q(try(function() return cl:item_at(i):key() end)) end
    end)
    emit("{" .. table.concat({
      '"kind":"force_plague"', '"turn":' .. turn, '"faction":' .. q(fkey),
      '"mf_cqi":' .. jv(mf_cqi),
      '"plague":' .. jv(try(function() return pg:plague_record() end)),
      '"creator":' .. jv(try(function() local cf = nn(pg:creator_faction()); return cf and cf:name() end)),
      '"components":[' .. table.concat(comps, ",") .. "]",
    }, ",") .. "}")
  end)
  try(function()
    local ul = mf:unit_list()
    for i = 0, ul:num_items() - 1 do
      local u = ul:item_at(i)
      emit("{" .. table.concat({
        '"kind":"unit"', '"turn":' .. turn, '"faction":' .. q(fkey),
        '"mf_cqi":' .. jv(mf_cqi),
        '"unit":' .. jv(try(function() return u:unit_key() end)),
        '"xp":' .. jv(try(function() return u:unit_experience() end)),
        '"men":' .. jv(try(function() return u:number_of_men() end)),
        '"max_men":' .. jv(try(function() return u:max_number_of_men() end)),
        '"category":' .. jv(try(function() return u:unit_category() end)),
        -- systematic enumeration: rank IS the unit's build state (RoR arrive at rank 9; merging
        -- DROPS rank to the lower of the two; Warband upgrades RESET it to 0 -- all invisible
        -- without it).
        '"rank":' .. jv(try(function() return u:rank() end)),
        '"health":' .. jv(try(function() return u:percentage_proportion_of_full_strength() end)),
      }, ",") .. "}")
    end
  end)
  try(function()
    local cl = mf:character_list()
    for i = 0, cl:num_items() - 1 do dump_char(turn, fkey, cl:item_at(i), "in_force", mf_cqi) end
  end)
end

-- ---------------------------------------------------------------- regions + buildings
local function dump_region(turn, r)
  local rname = try(function() return r:name() end)
  if rname == nil then return end
  -- The v2/audit region getters below are DIRECT calls on a valid region, so nn() cannot guard them.
  -- These 6 were breadcrumb-verified crash-safe on the live turn-1 full map (569 regions, no CTD),
  -- and growth/gdp/num_buildings populate non-null. r:last_building_constructed_key() was NOT safe
  -- and is removed (see below).
  local v_growth   = try(function() return r:faction_province_growth() end)
  local v_growthpt = try(function() return r:faction_province_growth_per_turn() end)
  local v_numb     = try(function() return r:num_buildings() end)
  local v_gdp      = try(function() return r:gdp() end)
  -- REMOVED r:last_building_constructed_key(): breadcrumb-pinpointed as a native CTD on turn 1
  -- (nothing constructed yet -> derefs a null building C-side, uncatchable by try()). The "last
  -- building constructed" is covered by the BuildingCompleted event instead.
  local v_aedict   = try(function() return r:get_active_edict_key() end)
  local v_sedict   = try(function() return r:get_selected_edict_key() end)
  emit("{" .. table.concat({
    '"kind":"region"', '"turn":' .. turn, '"region":' .. q(rname),
    '"owner":' .. jv(try(function() return r:owning_faction():name() end)),
    '"public_order":' .. jv(try(function() return r:public_order() end)),
    '"growth":' .. jv(v_growth),
    '"growth_per_turn":' .. jv(v_growthpt),
    '"growth_old":' .. jv(try(function() return r:growth() end)),
    '"pop":' .. jv(try(function() return r:settlement():population() end)),
    '"province":' .. jv(try(function() return r:province_name() end)),
    '"corruption":' .. jv(try(function() return r:corruption() end)),
    '"num_buildings":' .. jv(v_numb),
    '"gdp":' .. jv(v_gdp),
    '"is_capital":' .. jv(try(function() return r:is_province_capital() end)),
    '"is_abandoned":' .. jv(try(function() return r:is_abandoned() end)),
    '"climate":' .. jv(try(function() return r:settlement():get_climate() end)),
    '"garrison_str":' .. jv(try(function()
        return r:garrison_residence():army():unit_list():num_items() end)),
    '"owner_is_human":' .. jv(try(function() return r:owning_faction():is_human() end)),
    '"active_edict":' .. jv(v_aedict),
    '"selected_edict":' .. jv(v_sedict),
  }, ",") .. "}")
  -- every slot, occupied or not: an EMPTY slot is a fact too (it is why a build was possible)
  try(function()
    local sl = r:slot_list()
    for i = 0, sl:num_items() - 1 do
      local s = sl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"slot"', '"turn":' .. turn, '"region":' .. q(rname),
        -- slot_cqi is null on this patch (SLOT has no command_queue_index). slot_index (the list
        -- position) is the reliable per-region slot identity -> (region, slot_index) is unique and
        -- stable; slot_key/id probed as over-collect (null if the getter is absent).
        '"slot_index":' .. i,
        '"slot_key":' .. jv(try(function() return s:key() end)),
        '"slot_id":' .. jv(try(function() return s:slot_id() end)),
        '"slot_cqi":' .. jv(try(function() return s:command_queue_index() end)),
        -- FIX(audit): SLOT has NO template() (invented -> null). Doc: template_key() (slot template
        -- record key) + type() (slot type). building():name() IS correct here: region SLOT:building()
        -- returns a BUILDING object whose name() is the building key. Old call kept as null fallback.
        '"slot_type":' .. jv(try(function() return s:template_key() end)),
        '"slot_type_name":' .. jv(try(function() return s:type() end)),
        '"slot_type_old":' .. jv(try(function() return s:template() end)),
        '"has_building":' .. jv(try(function() return s:has_building() end)),
        '"building":' .. jv(try(function() return s:building():name() end)),
      }, ",") .. "}")
    end
  end)
  -- ‼ REGION POOLED RESOURCES -- captures CORRUPTION (all four flavours) and any per-region pooled
  -- currency. REGION has its own pooled_resource_manager (doc-verified); r:corruption() does not
  -- exist, so this is the real path. Same shape as the faction/force pooled dumps. No filtering.
  try(function()
    local prm = nn(r:pooled_resource_manager()); if not prm then return end
    local pl = prm:resources(); if not pl then return end
    for i = 0, pl:num_items() - 1 do
      local pr = pl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"region_pooled"', '"turn":' .. turn, '"region":' .. q(rname),
        '"resource":' .. jv(try(function() return pr:key() end)),
        '"value":' .. jv(try(function() return pr:value() end)),
        -- FIX(audit v3): maximum_value() -- enables tier/threshold derivation for per-region
        -- pooled currencies. PROBE-CONFIRMED live (POOLED_RESOURCE.maximum_value() returns e.g.
        -- 94/2147483648 on a region grudge pool). Same getter already proven in force_pooled.
        '"max":' .. jv(try(function() return pr:maximum_value() end)),
      }, ",") .. "}")
    end
  end)
  -- ‼ REGION EFFECT BUNDLES (FIX audit v3). Captures VC vampiric-corruption threshold bundles,
  -- Cathay harmony-tier bundles, climate/adjacency bundles -- another witness of local build state.
  -- SAME doc-verified pattern as dump_char/dump_faction effect_bundles (both work): REGION.
  -- effect_bundles() -> EFFECT_BUNDLE_LIST; each EFFECT_BUNDLE has key(). PROBE-CONFIRMED live
  -- (e.g. wh3_dlc20_climate_suitable_desert,wh3_main_adjacent_dwarf_dummy). ‼ effect_bundles() is a
  -- LIST -> do NOT nn() it (see nn() header): call num_items()/item_at() directly under try().
  try(function()
    local l = r:effect_bundles(); if not l then return end
    for i = 0, l:num_items() - 1 do
      emit("{" .. table.concat({
        '"kind":"region_effect_bundle"', '"turn":' .. turn, '"region":' .. q(rname),
        '"effect_bundle":' .. jv(try(function() return l:item_at(i):key() end)),
      }, ",") .. "}")
    end
  end)
  -- ‼ PROVINCE-SCOPED POOLED RESOURCES (FIX audit v3). The mod dumps faction/region/force pooled
  -- managers but never PROVINCE -- which is where the seven CORRUPTION flavours live (r:corruption()
  -- returns null on this patch). PROBE-CONFIRMED live via REGION.province() ->
  -- PROVINCE.pooled_resource_manager(): e.g. wh3_main_corruption_vampiric=25 (VC province),
  -- wh3_main_corruption_skaven=10 (Skaven), wh3_main_corruption_nurgle=20 (Nurgle). PROVINCE has
  -- is_null_interface() -> nn()-guard region:province() before use. Emitted ONCE per province (gated
  -- on the province CAPITAL region) since the pool is province-wide, not per-region -- avoids 569x
  -- duplication. FACTION_PROVINCE_MANAGER.pooled_resource_manager() was probed EMPTY, so the
  -- geographic PROVINCE pool is the correct source.
  try(function()
    if try(function() return r:is_province_capital() end) ~= true then return end
    local p = nn(r:province()); if not p then return end
    local pkey = try(function() return p:key() end)
    local prm = nn(p:pooled_resource_manager()); if not prm then return end
    local pl = prm:resources(); if not pl then return end
    for i = 0, pl:num_items() - 1 do
      local pr = pl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"province_pooled"', '"turn":' .. turn, '"region":' .. q(rname),
        '"province":' .. jv(pkey),
        '"resource":' .. jv(try(function() return pr:key() end)),
        '"value":' .. jv(try(function() return pr:value() end)),
        '"max":' .. jv(try(function() return pr:maximum_value() end)),
      }, ",") .. "}")
    end
  end)
  -- ‼ REGION PLAGUE STATE (FIX audit v3, Nurgle). REGION.get_plague_if_infected() -> PLAGUE or
  -- NULL_SCRIPT_INTERFACE when not infected. PROBE-CONFIRMED live (returns NOT_INFECTED cleanly at
  -- turn 1; PLAGUE implements is_null_interface() so nn() guards it). PLAGUE.plague_record() is the
  -- plague key; plague_components() -> PLAGUE_COMPONENT_LIST (each has key()). creator_faction().
  try(function()
    local pg = nn(r:get_plague_if_infected()); if not pg then return end
    local comps = {}
    try(function()
      local cl = pg:plague_components()
      for i = 0, cl:num_items() - 1 do comps[#comps + 1] = q(try(function() return cl:item_at(i):key() end)) end
    end)
    emit("{" .. table.concat({
      '"kind":"region_plague"', '"turn":' .. turn, '"region":' .. q(rname),
      '"plague":' .. jv(try(function() return pg:plague_record() end)),
      '"creator":' .. jv(try(function() local cf = nn(pg:creator_faction()); return cf and cf:name() end)),
      '"components":[' .. table.concat(comps, ",") .. "]",
    }, ",") .. "}")
  end)
end

-- ---------------------------------------------------------------- factions
local function dump_faction(turn, f)
  local fkey = try(function() return f:name() end)
  if fkey == nil then return end
  emit("{" .. table.concat({
    '"kind":"faction"', '"turn":' .. turn, '"faction":' .. q(fkey),
    '"is_human":' .. jv(try(function() return f:is_human() end)),
    '"treasury":' .. jv(try(function() return f:treasury() end)),
    '"regions":' .. jv(try(function() return f:region_list():num_items() end)),
    '"forces":' .. jv(try(function() return f:military_force_list():num_items() end)),
    '"chars":' .. jv(try(function() return f:character_list():num_items() end)),
    '"subculture":' .. jv(try(function() return f:subculture() end)),
    '"culture":' .. jv(try(function() return f:culture() end)),
    '"at_war":' .. jv(try(function() return f:at_war() end)),
    '"dead":' .. jv(try(function() return f:is_dead() end)),
    -- ‼ ADDED BY SYSTEMATIC ENUMERATION, NOT BY BEING ASKED.
    -- The user: "so these got added onlyt because i asked" / "what didn't i ask about"
    -- He is right and it was the deepest failure here: coverage was REACTIVE, growing only when
    -- interrogated, so the data was shaped by whatever he happened to think to ask. Enumerating
    -- the API surface instead found two catastrophic omissions nobody had asked about:
    --   INCOME  -- his ENTIRE demonstrated win is income 604 -> 1036. It was not being dumped.
    --   TECHNOLOGY -- a core build element he explicitly asked the taxonomy to cover. Not dumped.
    -- Both are the spine of a build order. Neither of us asked. That is exactly why you
    -- enumerate rather than wait for questions.
    '"income":' .. jv(try(function() return f:income() end)),
    '"net_income":' .. jv(try(function() return f:net_income() end)),
    '"expenditure":' .. jv(try(function() return f:expenditure() end)),
    -- FIX(audit): FACTION has NO rank() (invented -> null). Doc: imperium_level() is the faction
    -- rank/tier. Old call kept as null fallback.
    '"rank":' .. jv(try(function() return f:imperium_level() end)),
    '"rank_old":' .. jv(try(function() return f:rank() end)),
    '"leader_cqi":' .. jv(try(function() return f:faction_leader():command_queue_index() end)),
    '"has_home_region":' .. jv(try(function() return f:has_home_region() end)),
    -- FIX(audit): FACTION has NO current_research_key() (invented -> null). The script API exposes
    -- only WHETHER research is happening; the researched KEY comes from the ResearchStarted event
    -- (already listened). Dump the booleans as STATE; keep the invented key as a null fallback.
    '"is_researching":' .. jv(try(function() return f:is_currently_researching() end)),
    '"research_queue_idle":' .. jv(try(function() return f:research_queue_idle() end)),
    '"researching":' .. jv(try(function() return f:current_research_key() end)),
    '"has_rituals":' .. jv(try(function() return f:has_rituals() end)),
    -- ADDED(audit, doc-verified): systematic FACTION enumeration.
    '"num_provinces":' .. jv(try(function() return f:num_provinces() end)),
    '"num_complete_provinces":' .. jv(try(function() return f:num_complete_provinces() end)),
    '"num_generals":' .. jv(try(function() return f:num_generals() end)),
    '"num_allies":' .. jv(try(function() return f:num_allies() end)),
    '"tax_level":' .. jv(try(function() return f:tax_level() end)),
    -- FIX(audit v3): trade dominance magnitude (High Elves). PROBE-CONFIRMED live: FACTION.
    -- trade_value() / trade_value_percent() both exist (return 0/0 for a tradeless AI at turn 1;
    -- populate on a trading faction). Doc-verified real getters.
    '"trade_value":' .. jv(try(function() return f:trade_value() end)),
    '"trade_value_pct":' .. jv(try(function() return f:trade_value_percent() end)),
  }, ",") .. "}")

  -- ‼ FOREIGN-SLOT BUILDINGS (FIX audit v3). dump_region only dumps a region's OWN r:slot_list();
  -- foreign-province constructs -- Tzeentch/Trickster cults, Skaven under-empire, Vampire-Coast
  -- pirate coves -- live on a separate manager and were never state rows. PROBE-CONFIRMED live at
  -- turn 1: Tzeentch has 4 managers (wh3_main_tze_cult slots), Skaven 1 (wh2_dlc12_underempire,
  -- an occupied slot -> wh2_dlc12_under_empire_discovery_deeper_tunnels_1). Doc-verified chain:
  --   FACTION.foreign_slot_managers() -> FOREIGN_SLOT_MANAGER_LIST (num_items/item_at)
  --   FOREIGN_SLOT_MANAGER: region()/num_slots()/gdp()/has_been_discovered()/slots()
  --   FOREIGN_SLOT: template_key()/type_key()/has_building()/building() (a STRING key,
  --                 NOT an interface -- PROBED: building():name() ERRORS, building() IS the key)/active()
  -- The manager+slot interfaces implement is_null_interface() -> nn()-guard the managers; slots() is
  -- a LIST -> iterate directly. One row per foreign slot, keyed by the owning faction + host region.
  try(function()
    local ml = f:foreign_slot_managers(); if not ml then return end
    for i = 0, ml:num_items() - 1 do
      local m = nn(ml:item_at(i)); if m then
        local mregion = try(function() local rg = nn(m:region()); return rg and rg:name() end)
        local mgdp = try(function() return m:gdp() end)
        local mnum = try(function() return m:num_slots() end)
        local mdisc = try(function() return m:has_been_discovered() end)
        local ok = pcall(function()
          local sl = m:slots(); if not sl then return end
          for j = 0, sl:num_items() - 1 do
            local s = sl:item_at(j)
            emit("{" .. table.concat({
              '"kind":"foreign_slot"', '"turn":' .. turn, '"faction":' .. q(fkey),
              '"region":' .. jv(mregion),
              '"mgr_num_slots":' .. jv(mnum), '"mgr_gdp":' .. jv(mgdp),
              '"discovered":' .. jv(mdisc),
              '"slot_type":' .. jv(try(function() return s:type_key() end)),
              '"slot_template":' .. jv(try(function() return s:template_key() end)),
              '"has_building":' .. jv(try(function() return s:has_building() end)),
              '"building":' .. jv(try(function() return s:building() end)),
              '"active":' .. jv(try(function() return s:active() end)),
            }, ",") .. "}")
          end
        end)
        if not ok then end
      end
    end
  end)

  -- ‼ NURGLE CRAFTED-PLAGUE STATE (FIX audit v3). FACTION.plagues() -> FACTION_PLAGUE (implements
  -- is_null_interface -> nn()-guard); plague_component_list() -> PLAGUE_COMPONENT_LIST; each
  -- PLAGUE_COMPONENT has key() + has_state() (unlocked/set). PROBE-CONFIRMED live: 30 components on
  -- the Nurgle faction (all has_state=false at turn 1 -> flip true as symptoms are unlocked). This is
  -- the loadout of the plague the faction is crafting -- one row per component.
  try(function()
    local fp = nn(f:plagues()); if not fp then return end
    local cl = fp:plague_component_list(); if not cl then return end
    for i = 0, cl:num_items() - 1 do
      local c = cl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"faction_plague_component"', '"turn":' .. turn, '"faction":' .. q(fkey),
        '"component":' .. jv(try(function() return c:key() end)),
        '"has_state":' .. jv(try(function() return c:has_state() end)),
      }, ",") .. "}")
    end
  end)

  -- ADDED(audit, doc-verified): faction-wide effect bundles (commandment/tech/campaign buffs).
  -- FACTION.effect_bundles() -> EFFECT_BUNDLE_LIST; each EFFECT_BUNDLE has key() (String). Overcollect.
  try(function()
    local l = f:effect_bundles(); if not l then return end
    for i = 0, l:num_items() - 1 do
      emit("{" .. table.concat({
        '"kind":"faction_effect_bundle"', '"turn":' .. turn, '"faction":' .. q(fkey),
        '"effect_bundle":' .. jv(try(function() return l:item_at(i):key() end)),
      }, ",") .. "}")
    end
  end)

  -- ‼ GAP 2 (v2): ACTIVE RITUALS as durable STATE. Each ACTIVE_RITUAL exposes ritual_key()
  -- ("Returns the ritual record key for this active ritual") -- NOT :key(), which the event probe
  -- used and which returns a pointer string. Diffing these per faction/turn yields WHICH rite is
  -- running, robustly. record_key/key are overcollect fallbacks -> null if absent on this patch.
  -- FIX(audit): active_rituals() is NOT a FACTION method -- it lives on FACTION_RITUALS. The v2
  -- gap-closer called f:active_rituals() directly, which does not exist, so this loop silently
  -- emitted ZERO active_ritual rows. Doc-verified chain:
  --   FACTION.rituals() -> FACTION_RITUALS_SCRIPT_INTERFACE
  --   FACTION_RITUALS.active_rituals() -> ACTIVE_RITUAL_LIST (num_items/item_at)
  -- The ACTIVE_RITUAL getters below (ritual_key/ritual_chain_key/ritual_category/is_part_of_chain)
  -- are all real; record_key()/key() do not exist on ACTIVE_RITUAL -> null fallbacks. cast_time/
  -- cooldown_time/target_faction added as doc-verified overcollect. Old direct call kept as fallback.
  try(function()
    local fr = nn(f:rituals()); if not fr then return end
    local arl = fr:active_rituals(); if not arl then return end
    for i = 0, arl:num_items() - 1 do
      local ar = arl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"active_ritual"', '"turn":' .. turn, '"faction":' .. q(fkey),
        '"ritual_key":' .. jv(try(function() return ar:ritual_key() end)),
        '"ritual_chain_key":' .. jv(try(function() return ar:ritual_chain_key() end)),
        '"ritual_category":' .. jv(try(function() return ar:ritual_category() end)),
        '"is_part_of_chain":' .. jv(try(function() return ar:is_part_of_chain() end)),
        '"cast_time":' .. jv(try(function() return ar:cast_time() end)),
        '"cooldown_time":' .. jv(try(function() return ar:cooldown_time() end)),
        '"target_faction":' .. jv(try(function() local tf = nn(ar:target_faction()); return tf and tf:name() end)),
        '"record_key":' .. jv(try(function() return ar:record_key() end)),
        '"key":' .. jv(try(function() return ar:key() end)),
      }, ",") .. "}")
    end
  end)

  -- TECHNOLOGY. A build order without tech is not a build order.
  -- ‼ `f:technology_list()` DOES NOT EXIST -- I invented it and it silently emitted ZERO tech
  --   rows while looking correct. The verified call, from mod/twlog.lua (a previous agent read
  --   it out of the game's own scripts), is `f:num_completed_technologies()`:
  --       techs_completed = num(function() return f:num_completed_technologies() end)
  --   That gives a COUNT, not the keys. The KEYS come from the events, whose real names are
  --   also in twlog's verified table -- `ResearchCompleted`/`ResearchStarted`, NOT the
  --   `TechnologyResearchCompleted` I guessed. Both are already among the 435 registered.
  --   Count (state) + keys (events) together reconstruct the tech order exactly.
  --   THIRD TIME THIS SESSION that an invented API silently produced nothing while the verified
  --   one sat on disk. Read first.
  emit("{" .. table.concat({
    '"kind":"tech_count"', '"turn":' .. turn, '"faction":' .. q(fkey),
    '"completed":' .. jv(try(function() return f:num_completed_technologies() end)),
  }, ",") .. "}")

  -- POOLED RESOURCES: the per-race currencies -- skulls, souls, food, influence, devotion,
  -- grimoires, meat, prestige, amber, infamy, canopic jars, allegiance. The wiki is explicit
  -- that Money is merely RENAMED per race ("Favour", "Dark Magic") with no mechanical
  -- difference, and that the SECONDARY resource is the real per-race axis. Missing these means
  -- missing why a race-specific action was possible at all.
  try(function()
    local pl = f:pooled_resource_manager():resources()
    for i = 0, pl:num_items() - 1 do
      local pr = pl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"pooled"', '"turn":' .. turn, '"faction":' .. q(fkey),
        '"resource":' .. jv(try(function() return pr:key() end)),
        '"value":' .. jv(try(function() return pr:value() end)),
        -- FIX(audit v3): maximum_value() -- the per-race currency CAP, needed to derive tier/
        -- threshold (Slaanesh influence, TombKings canopic jars, Greenskins waaagh/salvage...).
        -- PROBE-CONFIRMED live: grn_waaagh=50/100, tmb_canopic_jars=100/25000, sla_devotees cap.
        '"max":' .. jv(try(function() return pr:maximum_value() end)),
      }, ",") .. "}")
    end
  end)

  -- ‼ THE FACTION'S UNEQUIPPED ITEM POOL. Added because of the user's question "what about item
  -- equipment?". Per-character `ancillaries[]` already captures what is EQUIPPED (and mounts --
  -- the wiki is explicit that mounts are ancillaries, assigned "on the character info screen
  -- along with items"). Diffing a character's ancillaries across turns yields equip/unequip.
  -- But the wiki is equally explicit that items exist OFF characters:
  --     "Items owned by the player go into a list and can be equipped to or unequipped from
  --      characters at will"  + "generally a 1 turn waiting period before an item will be
  --      transferred from one character to another"
  -- Without the pool we would see an item APPEAR on a lord but never know what else was
  -- available -- which is precisely the counterfactual a build comparison needs ("they had the
  -- Armour of Dawn and chose the Talisman"). A field not dumped is gone; dump it.
  -- NOTE(audit): f:ancillary_list() is NOT a real FACTION method on this patch and there is NO
  -- ANCILLARY script interface, so these rows do NOT populate (the loop errors under try -> no
  -- rows). No script-API getter exists for a faction's UNEQUIPPED item pool. What IS reachable is
  -- per-character EQUIPPED items -- now captured via the ARMORY in dump_char's ancillaries[]. Kept
  -- here as a harmless forward-compatible no-op in case a later patch adds the getter (overcollect).
  try(function()
    local al = f:ancillary_list()
    for i = 0, al:num_items() - 1 do
      local a = al:item_at(i)
      emit("{" .. table.concat({
        '"kind":"faction_ancillary"', '"turn":' .. turn, '"faction":' .. q(fkey),
        '"ancillary":' .. jv(try(function() return a:ancillary_key() end)),
        '"category":' .. jv(try(function() return a:ancillary_category() end)),
        '"equipped_to":' .. jv(try(function() return a:character():command_queue_index() end)),
      }, ",") .. "}")
    end
  end)

  -- ‼ DIPLOMACY STATE (who->whom, by outcome). The FactionLeaderDeclaresWar / SignsPeaceTreaty /
  -- Positive/NegativeDiplomaticEvent events give the INITIATOR; this per-turn relation dump gives
  -- the ground-truth pair, and diffing it across turns yields declare_war / make_peace (and
  -- trade/access/alliance if those getters exist) with an exact counterparty even for treaties the
  -- events don't name. HUMAN faction only (bounded: 1 x ~534 others/turn, all pcall'd).
  -- `f:at_war_with(o)` is verified (twcontrol.lua uses it); the rest are candidates -> null if absent.
  if try(function() return f:is_human() end) == true then
    try(function()
      local wl = cm:model():world():faction_list()
      for i = 0, wl:num_items() - 1 do
        local o = wl:item_at(i)
        local okey = try(function() return o:name() end)
        if okey and okey ~= fkey then
          emit("{" .. table.concat({
            '"kind":"diplo"', '"turn":' .. turn, '"faction":' .. q(fkey), '"toward":' .. q(okey),
            '"at_war":' .. jv(try(function() return f:at_war_with(o) end)),
            -- FIX(audit): has_trade_agreement_with / has_military_access_with_faction / is_ally_of do
            -- NOT exist on FACTION (invented -> null). Doc-verified names:
            --   trade_agreement_with, military_access_pact_with, allied_with, military_allies_with,
            --   defensive_allies_with, non_aggression_pact_with (all take a FACTION, return bool).
            -- Old calls kept as null fallbacks; is_vassal_of() is real and unchanged.
            '"trade":' .. jv(try(function() return f:trade_agreement_with(o) end)),
            '"mil_access":' .. jv(try(function() return f:military_access_pact_with(o) end)),
            '"allied":' .. jv(try(function() return f:allied_with(o) end)),
            '"mil_ally":' .. jv(try(function() return f:military_allies_with(o) end)),
            '"def_ally":' .. jv(try(function() return f:defensive_allies_with(o) end)),
            '"non_aggression":' .. jv(try(function() return f:non_aggression_pact_with(o) end)),
            '"trade_old":' .. jv(try(function() return f:has_trade_agreement_with(o) end)),
            '"mil_access_old":' .. jv(try(function() return f:has_military_access_with_faction(o) end)),
            '"allied_old":' .. jv(try(function() return f:is_ally_of(o) end)),
            '"vassal_of":' .. jv(try(function() return f:is_vassal_of(o) end)),
          }, ",") .. "}")
        end
      end
    end)
  end
end

-- ---------------------------------------------------------------- the round
-- ‼ ONCE PER TURN, NOT ONCE PER FACTION.
-- The listeners are deliberately redundant (immediate + first_tick + WorldStartRound +
-- FactionTurnStart) so a baseline is never missed. But FactionTurnStart fires for EVERY faction
-- -- 534 of them on this map -- and each firing walked every faction, character, force, unit,
-- region and slot. Measured on turn 1: the full scrape ran **65 times**, emitting 34,186 faction
-- rows for a single turn. That is ~65x redundant work WHILE THE USER IS PLAYING, which risks the
-- one thing the mod must never do: "the mod is fine if its recording but not if it messes up
-- play". Gate on the turn number: the first listener to fire for turn N does the work, the rest
-- return instantly. Redundancy is kept (any one of the four can be the first) at 1/65th the cost.
-- ‼ SHARED full all-faction scrape body (no round markers). Extracted VERBATIM from dump_round so
-- the turn-start output is byte-identical to before, and reused unchanged by dump_round_end (TURN
-- END). Same faction/char/force/region rows, same order. The ONLY new code is the marker/gating in
-- the two callers below -- dump_faction/char/force/region output shape is untouched (downstream
-- parsers depend on it). turn is passed in so both callers stamp the row `turn` identically.
local function scrape_all_factions(turn)
  local world = cm:model():world()

  -- EVERY faction. Not just ours: the AI's state is what our decisions react to, and a
  -- faction we ignore today is a pattern we cannot find tomorrow.
  local fl = world:faction_list()
  for i = 0, fl:num_items() - 1 do
    local f = fl:item_at(i)
    local fkey = try(function() return f:name() end) or "?"
    dump_faction(turn, f)
    -- every character, force-leading or not. A LONE HERO has no force -- draft 1 lost them.
    try(function()
      local cl = f:character_list()
      for k = 0, cl:num_items() - 1 do
        local c = cl:item_at(k)
        local mfq = try(function()
          if c:has_military_force() then return c:military_force():command_queue_index() end
          return nil
        end)
        dump_char(turn, fkey, c, "faction_character", mfq)
      end
    end)
    try(function()
      local mfl = f:military_force_list()
      for j = 0, mfl:num_items() - 1 do dump_force(turn, fkey, mfl:item_at(j)) end
    end)
  end

  -- NOTE: no province enumeration here. `world:province_list()` is an INVENTED api -- it
  -- emitted ZERO rows while looking like coverage. It is not needed: this dump contains ALL
  -- 569 regions of the map and each carries its `province` name, so a province's true size and
  -- owner set are DERIVABLE at read time (verified: full-province count matched settlements
  -- exactly, incl. the single-region Great Bastion provinces). Dead code removed rather than
  -- left as a silent no-op.
  -- EVERY region on the map, whoever owns it.
  try(function()
    local rl = world:region_manager():region_list()
    for i = 0, rl:num_items() - 1 do dump_region(turn, rl:item_at(i)) end
  end)
end

-- ‼ TURN-START gate. First listener (immediate / first_tick / WorldStartRound / FactionTurnStart)
-- to fire for turn N does the full scrape; the rest no-op. Unchanged from before.
local last_turn = -1

local function dump_round()
  local ok, err = pcall(function()
    local turn = try(function() return cm:model():turn_number() end) or -1
    if turn == last_turn then return end   -- already scraped this turn; the other listeners no-op
    last_turn = turn
    emit('{"kind":"round_begin","turn":' .. turn .. "}")
    scrape_all_factions(turn)
    emit('{"kind":"round_end","turn":' .. turn .. "}")
  end)
  if not ok then emit('{"kind":"error","msg":' .. q(tostring(err)) .. "}") end
end

-- ‼ TURN-END full scrape (recorder-v5 Task 1). The SAME full all-faction scrape as dump_round, but
-- fired on the HUMAN faction's FactionTurnEnd so we capture end-of-turn state to diff against the
-- next turn's start. Gated by a SEPARATE last_end_turn var so the turn-START gate (last_turn) never
-- blocks it. Emits a `round_end` marker FIRST, then the scrape (a turn-end block is thus a round_end
-- immediately FOLLOWED by faction rows -- distinct from dump_round's round_end, which CLOSES a
-- turn-start block and is followed by no scrape).
local last_end_turn = -1

local function dump_round_end()
  local ok, err = pcall(function()
    local turn = try(function() return cm:model():turn_number() end) or -1
    if turn == last_end_turn then return end   -- already end-scraped this turn
    last_end_turn = turn
    emit('{"kind":"round_end","turn":' .. turn .. "}")
    scrape_all_factions(turn)
  end)
  if not ok then emit('{"kind":"error","msg":' .. q(tostring(err)) .. "}") end
end

-- ‼ IN-PLAYER-TURN gate (recorder-v5 Task 1). TRUE only between the HUMAN faction's FactionTurnStart
-- and FactionTurnEnd (set by the listeners in twstate()). The between-action LIGHT scrape (dump_player)
-- fires ONLY when this is true, so the AI's ~290 per-run turn rotations NEVER trigger it -- this is
-- the avoidance of the 290x explosion that forced the original once-per-turn gate.
local in_player_turn = false

-- ‼ SIGNIFICANT player-action events -> a between-action LIGHT scrape. This is the semantic-action
-- set: every EVENT_VERBS key from tools/twstate.py (the verbs the pipeline resolves) plus the
-- player-issue events. Membership is a set lookup (SIGNIFICANT[ev]); a light scrape fires when a
-- fired event is in here AND in_player_turn is true. Multiple events per action = a few extra
-- player-only light scrapes, which is fine (each is cheap). Kept in sync with EVENT_VERBS.
local SIGNIFICANT = {}
for _, ev in ipairs({
  -- EVENT_VERBS keys (tools/twstate.py):
  "ResearchCompleted", "ResearchStarted", "CharacterSkillPointAllocated",
  "CharacterAncillaryGained", "CharacterArmoryItemEquipped", "CharacterArmoryItemUnequipped",
  "FactionLeaderDeclaresWar", "FactionLeaderSignsPeaceTreaty", "FactionJoinsConfederation",
  "PositiveDiplomaticEvent", "NegativeDiplomaticEvent", "DiplomacyManipulationExecutedEvent",
  "CharacterPostBattleCaptureOption", "ForceAdoptsStance", "CharacterCharacterTargetAction",
  "CharacterGarrisonTargetAction", "ScriptEventAgentActionSuccessAgainstCharacter",
  "FactionLeaderIssuesEdict", "RitualStartedEvent", "RitualCompletedEvent", "CaravanRecruited",
  "WoMCompassUserDirectionSelectedEvent", "DilemmaChoiceMadeEvent", "CharacterRecruited",
  "UnitTrained", "RecruitmentItemIssuedByPlayer", "ScriptEventPlayerCharacterFinishedMovingEvent",
  "BuildingConstructionIssuedByPlayer", "UnitUpgraded", "BuildingCompleted",
  "CharacterRazesSettlement", "CharacterSacksSettlement", "CharacterLootsSettlement",
  "MissionSucceeded", "ScriptEventPlayerAcceptsMission",
  -- extra player-issue / settlement-outcome names called out in the task design:
  "CharacterRazedSettlement", "CharacterSackedSettlement", "CharacterLootedSettlement",
}) do SIGNIFICANT[ev] = true end

-- ‼ BETWEEN-ACTION LIGHT scrape (recorder-v5 Task 1). The human/local faction ONLY: its faction
-- row + its characters + its military forces + the regions it OWNS (+ those regions' slots, emitted
-- by dump_region). Reuses dump_faction/dump_char/dump_force/dump_region UNCHANGED. Emits a
-- `player_snapshot` marker first (tagged with the triggering event as `reason`) so the block is
-- distinguishable and alignable. A player_snapshot block therefore holds EXACTLY ONE faction row
-- (the human's), NOT ~290 -- the proof it is player-only. All pcall/try-wrapped; nn() only on the
-- FACTION entity, never on lists.
local function dump_player(turn, reason)
  local ok, err = pcall(function()
    local f = nn(cm:get_local_faction(true))
    if not f then return end
    local fkey = try(function() return f:name() end) or "?"
    emit('{"kind":"player_snapshot","turn":' .. turn .. ',"reason":' .. q(reason)
         .. ',"faction":' .. q(fkey) .. "}")
    dump_faction(turn, f)
    try(function()
      local cl = f:character_list()
      for k = 0, cl:num_items() - 1 do
        local c = cl:item_at(k)
        local mfq = try(function()
          if c:has_military_force() then return c:military_force():command_queue_index() end
          return nil
        end)
        dump_char(turn, fkey, c, "faction_character", mfq)
      end
    end)
    try(function()
      local mfl = f:military_force_list()
      for j = 0, mfl:num_items() - 1 do dump_force(turn, fkey, mfl:item_at(j)) end
    end)
    -- regions the human OWNS (its own region_list), each with its slots -- NOT the whole map.
    try(function()
      local rl = f:region_list()
      for i = 0, rl:num_items() - 1 do dump_region(turn, rl:item_at(i)) end
    end)
  end)
  if not ok then emit('{"kind":"error","msg":' .. q(tostring(err)) .. "}") end
end

-- ================================ EVENTS: the action channel ================================
-- Per-turn state is SNAPSHOTS. Events are the ACTIONS, at action granularity -- which is the
-- resolution a build order actually needs. Snapshots tell you an embed happened somewhere in
-- turn 5; CharacterJoinsGarrison tells you exactly when and to whom.
--
-- ‼ OVERCOLLECT. The user: "overcollecting is a defence against your sabotage" / "i want to aim
--   for as much overcollection as possible rn". He is right: my judgment about what matters has
--   been wrong repeatedly, so the defence is to record everything and let a LATER, better
--   judgment filter at read time. Listening to an event costs nothing if it never fires.
--
-- Every event is logged with every context field we can reach. An event that turns out to be
-- noise is free to ignore later; an event we did not listen for is gone forever.
-- ‼ MY HAND-WRITTEN LIST WAS REPLACED. It had ~50 names and most were WRONG -- verified against
--   the game's own registrations: BuildingCompleted, CharacterMovedRegion, ForceAdoptsStance and
--   CharacterEmbeddedInMilitaryForce do NOT appear anywhere. I invented them. Again.
--   `events_all.lua` is GENERATED from two evidence sources and carries 435 names:
--     214 the game's own scripts register (grepped from our 4 runs' logs -- proof, not guesswork)
--     249 from the EVENTS table in mod/twlog.lua (extracted from the game's script files)
--   The confirmed set includes the ones that actually resolve a map right-click by OUTCOME:
--     ComponentLClickUp                              -- a click event, WITH the component
--     ScriptEventPlayerCharacterFinishedMovingEvent  -- the move outcome
--     CharacterSelected / SettlementSelected         -- what was selected, and when
--     PooledResourceChanged                          -- the per-race currencies
--     RecruitmentItemIssuedByPlayer                  -- a recruit, issued BY THE PLAYER
local EVENTS = {
  "ActiveContractRefreshEvent", "AgentPlagueDataCreatedEvent", "AreaEntered", "AreaExited",
  "BattleBeingFought", "BattleCompleted", "BattleCompletedCameraMove", "BuildingCancelled",
  "BuildingCompleted", "BuildingConstructionIssuedByPlayer", "BuildingLifecycleDevelops",
  "CampaignArmiesMerge", "CampaignBuildingDamaged", "CampaignCoastalAssaultOnCharacter",
  "CampaignCoastalAssaultOnGarrison", "CampaignEffectsBundleAwarded", "CaravanCompleted",
  "CaravanEvent", "CaravanMoved", "CaravanRecruited", "CaravanReturned", "CaravanSpawned",
  "CaravanWaylaid", "CharacterAncillaryGained", "CharacterArmoryItemEquipped",
  "CharacterArmoryItemEvent", "CharacterArmoryItemUnequipped", "CharacterArmoryItemUnlocked",
  "CharacterAttacksAlly", "CharacterBecomesFactionLeader", "CharacterBesiegesSettlement",
  "CharacterBlockadedPort", "CharacterBrokePortBlockade", "CharacterCanLiberate",
  "CharacterCandidateBecomesMinister", "CharacterCapturedSettlementUnopposed",
  "CharacterCharacterTargetAction", "CharacterComesOfAge", "CharacterCompletedBattle",
  "CharacterConvalescedOrKilled", "CharacterCreated", "CharacterDestroyed",
  "CharacterDiscovered", "CharacterDisembarksNavy", "CharacterEmbarksNavy",
  "CharacterEntersAttritionalArea", "CharacterEntersGarrison", "CharacterEvent",
  "CharacterFactionCompletesResearch", "CharacterFinishedMovingEvent",
  "CharacterGarrisonTargetAction", "CharacterGarrisonTargetEvent", "CharacterInfoPanelOpened",
  "CharacterInitiativeActivationChangedEvent", "CharacterInitiativeEvent",
  "CharacterLeavesGarrison", "CharacterLoanedEvent", "CharacterLootedSettlement",
  "CharacterLootsSettlement", "CharacterMarriage",
  "CharacterMilitaryForceTraditionPointAllocated",
  "CharacterMilitaryForceTraditionPointAvailable",
  "CharacterParticipatedAsSecondaryGeneralInBattle",
  "CharacterPerformsActionAgainstFriendlyTarget",
  "CharacterPerformsSettlementOccupationDecision", "CharacterPostBattleCaptureOption",
  "CharacterPostBattleEnslave", "CharacterPostBattleRelease", "CharacterPostBattleSlaughter",
  "CharacterPromoted", "CharacterRankUp", "CharacterRankUpNeedsAncillary",
  "CharacterRazedSettlement", "CharacterRazesSettlement", "CharacterRecruited",
  "CharacterRelativeKilled", "CharacterSackedSettlement", "CharacterSacksSettlement",
  "CharacterSelected", "CharacterSelectedWithUnitUpgradeUnlockedAndAffordable",
  "CharacterSkillPointAllocated", "CharacterSkillPointAvailable", "CharacterTargetEvent",
  "CharacterTurnEnd", "CharacterTurnStart", "CharacterWaaaghOccurred",
  "CharacterWithdrewFromBattle", "ClanBecomesVassal", "ClimatePhaseChange",
  "ComponentLClickUp", "CorruptionCounterIntervalEvent", "DebugCharacterEvent",
  "DebugFactionEvent", "DebugRegionEvent", "DilemmaChoiceMadeEvent", "DilemmaEvent",
  "DilemmaGenerationFailedEvent", "DilemmaIssued", "DilemmaIssuedEvent",
  "DiplomacyManipulationExecutedEvent", "DiplomaticOfferRejected",
  "EventFeedEventRecordedEvent", "FactionAboutToEndTurn", "FactionBecomesActiveHuman",
  "FactionBecomesIdleHuman", "FactionBecomesLiberationVassal", "FactionBecomesVassal",
  "FactionBeginTurnPhaseNormal", "FactionBribesUnit", "FactionCharacterTagAddedEvent",
  "FactionCharacterTagEntryEvent", "FactionCharacterTagRemovedEvent", "FactionCivilWarOccured",
  "FactionCookedDish", "FactionDeath", "FactionEncountersOtherFaction", "FactionEvent",
  "FactionGainedAncillary", "FactionGovernmentTypeChanged",
  "FactionInitiativeActivationChangedEvent", "FactionInitiativeEvent",
  "FactionJoinsConfederation", "FactionLeaderDeclaresWar", "FactionLeaderIssuesEdict",
  "FactionLeaderSignsPeaceTreaty", "FactionLiberated", "FactionRoundStart",
  "FactionSubjugatesOtherFaction", "FactionTurnEnd", "FactionTurnStart",
  "FirstTickAfterNewCampaignStarted", "FirstTickAfterWorldCreated", "ForceAdoptsStance",
  "ForceRaidingPlayerTerritory", "ForeignBuildingCancelled",
  "ForeignSlotBuildingCompleteEvent", "ForeignSlotBuildingDamagedEvent",
  "ForeignSlotBuildingDismantledEvent", "ForeignSlotManagerCreatedEvent",
  "ForeignSlotManagerDiscoveredEvent", "ForeignSlotManagerRemovedEvent",
  "GarrisonAttackedEvent", "GarrisonOccupiedEvent", "GarrisonResidenceEvent",
  "GarrisonResidenceExposedToFaction", "GorRokDefeated", "GovernorAssignedCharacterEvent",
  "HaveCharacterWithinRangeOfPositionMissionEvaluationResultEvent", "HunterUnlocked",
  "ImprisonmentEvent", "ImprisonmentRejectionEvent", "IncidentEvent", "IncidentFailedEvent",
  "IncidentOccuredEvent", "IngredientUnlocked", "LandTradeRouteRaided",
  "MilitaryForceBuildingCancelled", "MilitaryForceBuildingCompleteEvent",
  "MilitaryForceCreated", "MilitaryForceDevelopmentPointChange", "MilitaryForceInfectionEvent",
  "MissionCancelled", "MissionEvent", "MissionFailed", "MissionGenerationFailed",
  "MissionIssued", "MissionNearingExpiry", "MissionStatusEvent",
  "MissionStringParseErrorEvent", "MissionSucceeded", "MovementPointsExhausted",
  "MultiTurnMove", "NavigableToursStarted", "NegativeDiplomaticEvent", "NewCampaignStarted",
  "NewCharacterEnteredRecruitmentPool", "NominalDifficultyLevelChangedEvent",
  "PanelClosedCampaign", "PanelOpenedCampaign", "PendingBankruptcy", "PendingBattle",
  "PlayerWaghEndedSuccessful", "PlayerWaghEndedUnsuccessful", "PooledResourceChanged",
  "PooledResourceEffectChangedEvent", "PooledResourceEvent",
  "PooledResourceThresholdOperationReached", "PositiveDiplomaticEvent",
  "PrisonActionTakenEvent", "ProvinceGovernorAppointed", "ProvinceGovernorMoved",
  "ProvinceGovernorshipNewDecisionAvailable", "QueryShouldWaylayCaravan",
  "QueryTeleportationNetworkHandoverNodeClosure",
  "QueryTeleportationNetworkShouldHandoverCharacterNodeClosure",
  "RecruitmentItemCancelledByPlayer", "RecruitmentItemIssuedByPlayer",
  "RegionAbandonedWithBuildingEvent", "RegionBuildingCancelled", "RegionEvent",
  "RegionFactionChangeEvent", "RegionInfectionEvent", "RegionIssuesDemands",
  "RegionPlagueStateChanged", "RegionRebels", "RegionRiots", "RegionSelected",
  "RegionSlotEvent", "RegionStrikes", "RegionTurnEnd", "RegionTurnStart", "ResearchCompleted",
  "ResearchStarted", "RitualCancelledEvent", "RitualCompletedEvent", "RitualEvent",
  "RitualStartedEvent", "RitualsCompletedAndDelayedEvent", "ScriptEventAddBlessing",
  "ScriptEventAgentActionFailureAgainstCharacter",
  "ScriptEventAgentActionFailureAgainstGarrison",
  "ScriptEventAgentActionSuccessAgainstCharacter",
  "ScriptEventAgentActionSuccessAgainstGarrison", "ScriptEventArtefactsForgedAll",
  "ScriptEventArtefactsForgedOne", "ScriptEventArtefactsForgedThree",
  "ScriptEventAssassinationAllTargetsKilled", "ScriptEventAssassinationFirstTargetKilled",
  "ScriptEventAssassinationNewTargets", "ScriptEventAthelTamarhaPanelOpened",
  "ScriptEventBloodgroundsHerdstoneCreated", "ScriptEventBretonniaGrailVowCompleted",
  "ScriptEventBretonniaKnightsVowCompleted", "ScriptEventBretonniaQuestingVowCompleted",
  "ScriptEventBretonnianVowsButtonClicked", "ScriptEventCampaignIntroComplete",
  "ScriptEventChangelingGainsForm", "ScriptEventChaosIncursionAgainstFaction",
  "ScriptEventClanButtonClicked", "ScriptEventCommandmentWarningIssued",
  "ScriptEventDeathAlarielleDefeated", "ScriptEventDeathBloodVoyage",
  "ScriptEventDeathBloodVoyageDead", "ScriptEventDeathMorathiDefeated",
  "ScriptEventDeathNightLevel", "ScriptEventDeathNightTriggered",
  "ScriptEventDeeprootsUnlocked", "ScriptEventDefenderOfUlthuanInnerLost",
  "ScriptEventDefenderOfUlthuanInnerRegained", "ScriptEventDefenderOfUlthuanOuterLost",
  "ScriptEventDefenderOfUlthuanUnited", "ScriptEventDiplomacyPanelOpened", "ScriptEventDotGP",
  "ScriptEventDotGPButtonClicked", "ScriptEventDotGPGodCompleted",
  "ScriptEventDwarfForgePanelOpened", "ScriptEventElectorAppointed",
  "ScriptEventElectorCapitalTaken", "ScriptEventElectorCountButtonClicked",
  "ScriptEventElixirButtonClicked", "ScriptEventFactionPerformsMotherlandRitual",
  "ScriptEventFactionTurnStartLowestPublicOrder",
  "ScriptEventFifthTreasureMapMissionSucceeded", "ScriptEventFirstTreasureMapMissionSucceeded",
  "ScriptEventFoodLevelDown", "ScriptEventFoodLevelUp", "ScriptEventFoodMerchantSpawned",
  "ScriptEventFoodValueDown", "ScriptEventFoodValueUp",
  "ScriptEventForbiddenWorkshopButtonClicked", "ScriptEventForestRitualAvailable",
  "ScriptEventForgeArtefactPair", "ScriptEventForgeArtefactPartReceived",
  "ScriptEventForgeOfDaithFirstIncident", "ScriptEventGotrekAndFelixDepart",
  "ScriptEventGotrekAndFelixPubBuilt", "ScriptEventGreenKnightFade",
  "ScriptEventGreenKnightSummoned", "ScriptEventGromsCauldronPanelOpened",
  "ScriptEventHeraldUpgradeChance", "ScriptEventHostilityDecreased",
  "ScriptEventHumanFactionTurnStart", "ScriptEventHumanWinsBattle",
  "ScriptEventHumanWinsFieldBattle", "ScriptEventHunterStoryCompleted",
  "ScriptEventIkitWorkshopPanelOpened", "ScriptEventImmediatelyTriggerNarrativeTrigger",
  "ScriptEventImperialAuthorityElectorKilled",
  "ScriptEventImperialAuthorityWarDeclaredOnElector", "ScriptEventImrikDragonBattleSpawn",
  "ScriptEventImrikDragonBattleWinFive", "ScriptEventImrikDragonBattleWinOne",
  "ScriptEventImrikDragonBattleWinTwo", "ScriptEventImrikDragonDilemmaNonBattle",
  "ScriptEventImrikDragonEncounterGeneric", "ScriptEventImrikDragonMarkerEnter",
  "ScriptEventImrikDragonMarkerSpawn", "ScriptEventIssueTreasureMapMission",
  "ScriptEventIssueTreasureMapMissionFullLog", "ScriptEventMalusPossessedPostBattle",
  "ScriptEventMandateLostSettlement", "ScriptEventMarkerSpawned",
  "ScriptEventMarksofRuinationCompleted", "ScriptEventMarksofRuinationThreshold",
  "ScriptEventMistsOfYvresseUnlocked", "ScriptEventMortalWorldsTorment",
  "ScriptEventMoveOptionsPanelOpened", "ScriptEventNegativePeasantEconomy",
  "ScriptEventNewGodAscendant", "ScriptEventOgreContractsIssued",
  "ScriptEventOxyFirstSanctumConstructed", "ScriptEventOxyFirstStoneGained",
  "ScriptEventOxyThreatMapCreated", "ScriptEventOxyThreatMapSuccess",
  "ScriptEventPieceOfEightCollected", "ScriptEventPlayer", "ScriptEventPlayerAcceptsMission",
  "ScriptEventPlayerBattleSequenceCompleted", "ScriptEventPlayerCharacterFinishedMovingEvent",
  "ScriptEventPlayerClimbsInfamyList", "ScriptEventPlayerFactionTurnStart",
  "ScriptEventPlayerInfamyIncreases", "ScriptEventPlayerNukeReadyToBuy",
  "ScriptEventPlayerOpensDiplomacyPanel", "ScriptEventPlayerRegionWindsOfMagicChanged",
  "ScriptEventPlayerStartsOpenCampaignFromNormal", "ScriptEventPlayerTopsInfamyList",
  "ScriptEventPlayerUnderEmpireEstablished", "ScriptEventPlayerWinsBattle",
  "ScriptEventPlayerWinsFieldBattle", "ScriptEventPlayerWinsSettlementAttackBattle",
  "ScriptEventPlayerWinsSettlementBattle", "ScriptEventPlayerWorkshopUpgraded",
  "ScriptEventPoSStage",
  -- FIX(audit v3): the stub "ScriptEventPoSStage" is TRUNCATED and never matches (CA requires the
  -- EXACT event string). Add the real numbered Prophecy-of-Sotek milestones (Lizardmen/Tehenhauin).
  "ScriptEventPoSStage1Completed", "ScriptEventPoSStage2Completed", "ScriptEventPoSStage3Completed",
  -- FIX(audit v3): Kislev Ice Court training completion -- absent entirely from the table; no other
  -- CA-event witness for the mechanic.
  "ScriptEventIceCourtExpired",
  "ScriptEventPositivePeasantEconomy",
  "ScriptEventPowerOfNatureTriggered", "ScriptEventPreBattlePanelOpened",
  "ScriptEventPreBattlePanelOpenedAmbushPlayerDefender",
  "ScriptEventPreBattlePanelOpenedField", "ScriptEventPreBattlePanelOpenedMinorSettlement",
  "ScriptEventPrestigeGained", "ScriptEventRaiseForceButtonClicked",
  "ScriptEventRakarthBeastIncidentGenerated", "ScriptEventRakarthRiteUnlocked",
  "ScriptEventRecruitLordPanelOpened", "ScriptEventRegionRebels", "ScriptEventRemoveBlessing",
  "ScriptEventRiteUnlocked", "ScriptEventRitualofRuinPerformed",
  "ScriptEventRitualofRuinUnlocked", "ScriptEventSacrificeTier",
  -- FIX(audit v3): the stub "ScriptEventSacrificeTier" is TRUNCATED and never matches. Add the real
  -- numbered Sacrifices-to-Sotek unlock events (Lizardmen). Tier5 confirmed firing live per the audit.
  "ScriptEventSacrificeTier1Unlocked", "ScriptEventSacrificeTier2Unlocked",
  "ScriptEventSacrificeTier3Unlocked", "ScriptEventSacrificeTier4Unlocked",
  "ScriptEventSacrificeTier5Unlocked",
  "ScriptEventShadowyDealingsPanelOpened", "ScriptEventStartTransientIntervention",
  "ScriptEventSwordAvailable", "ScriptEventSwordClaimedByAI",
  "ScriptEventSwordClaimedByPlayer", "ScriptEventSwordDilemmaFirst",
  "ScriptEventSwordReturned", "ScriptEventSwordStuck", "ScriptEventTauroxRampageMomentumLost",
  "ScriptEventTauroxRampageOver", "ScriptEventTauroxRampageSuccess",
  "ScriptEventTauroxRampageThresholdReached", "ScriptEventTechnologyPanelOpened",
  "ScriptEventTreasureSearchFailed", "ScriptEventTriggerAutoresolvingAdvice",
  "ScriptEventTriggerQuestChain", "ScriptEventUlthuanMonsterAttacksSea",
  "ScriptEventUlthuanMonsterAttacksSettlement", "ScriptEventUlthuanMonsterUnderPlayerControl",
  "ScriptEventUnderEmpireAIDiscovered", "ScriptEventUnderEmpireAIDoomsphereCompleted",
  "ScriptEventUnderEmpireAIDoomsphereCompletedFollowUp", "ScriptEventUnderEmpireAIWarCamp",
  "ScriptEventUnderEmpireAgentInRegion", "ScriptEventUnderEmpireDoomsphereCompleted",
  "ScriptEventUnderEmpireDoomsphereStarted", "ScriptEventUnderEmpirePlayerDiscovered",
  "ScriptEventUnderEmpirePlayerWarCamp", "ScriptEventUnderEmpireRemovedByPlayer",
  "ScriptEventWaghBattle", "ScriptEventWaghReminder", "ScriptEventWaghResourceMax",
  "ScriptEventWaghSelect", "ScriptEventWaghTransportedArmies",
  "ScriptEventWarmbloodInvadersHunterKilled", "ScriptEventWitchKingRiteUnlocked",
  "ScriptEventWorldrootsButtonClicked", "ScriptEventWulfhartsHuntersButtonClicked",
  "ScriptEventYvresseDefenceOne", "ScriptEventYvresseDefenceThree",
  "ScriptEventYvresseDefenceTwo", "ScriptedCharacterUnhidden",
  "ScriptedCharacterUnhiddenFailed", "SeaTradeRouteRaided", "SetCutscenePlayingAllowed",
  "SettlementSelected", "SharedStateChanged", "SharedStateRemoved", "SlotOpens",
  "SlotRoundStart", "SlotSelected", "SlotTurnStart", "SpawnableForceCreatedEvent",
  "StreakEffectLevelsEntered", "TeleportationNetworkCharacterInteractionStarted",
  "TeleportationNetworkCharacterNodeClosureHandedOver", "TeleportationNetworkMoveCompleted",
  "TeleportationNetworkMoveStart", "TeleportationNetworkNodeClosed",
  "TeleportationNetworkNodeEvent", "TeleportationNetworkNodeOpened", "TradeNodeConnected",
  "TradeRouteEstablished", "TriggerPostBattleAncillaries", "UITrigger", "UniqueAgentDespawned",
  "UniqueAgentEvent", "UniqueAgentSpawned", "UnitCompletedBattle", "UnitConverted",
  "UnitCreated", "UnitDisbanded", "UnitEffectPurchased", "UnitEffectUnpurchased", "UnitEvent",
  "UnitMergedAndDestroyed", "UnitTrained", "UnitTurnEnd", "UnitUpgraded",
  "WarCoordinationRequestIssued", "WoMCompassUserActionTriggeredEvent",
  "WoMCompassUserDirectionSelectedEvent", "WorldCreated", "WorldStartRound",
}

-- Pull whatever context an event object will give up. NOTHING is assumed to exist: every
-- accessor is probed and yields null if absent, so an unknown event still logs its name+turn.
local function ctx(context)
  local p = {}
  local function add(k, f) p[#p + 1] = '"' .. k .. '":' .. jv(try(f)) end
  -- CLICKED COMPONENT id on UI events (Component*ClickUp): context.string is the id of the clicked
  -- UIComponent (e.g. "button_perform", "btn_dance_excess"). This is what makes a UI-only action --
  -- one the game fires NO scripted event for (Slaanesh Eternal Dance "perform", and others) --
  -- capturable as an EVENT from the state stream, not just the [ui] click-path log. Null on non-UI
  -- events (they have no context.string). GENERALIZES every button-driven mechanic.
  add("component", function() return context.string end)
  add("faction", function() return context:faction():name() end)
  add("char_cqi", function() return context:character():command_queue_index() end)
  add("char_subtype", function() return context:character():character_subtype_key() end)
  add("x", function() return context:character():logical_position_x() end)
  add("y", function() return context:character():logical_position_y() end)
  add("mf_cqi", function() return context:military_force():command_queue_index() end)
  add("target_char", function() return context:target_character():command_queue_index() end)
  add("region", function() return context:region():name() end)
  add("garrison", function() return context:garrison_residence():region():name() end)
  add("building", function() return context:building():name() end)
  add("unit", function() return context:unit():unit_key() end)
  -- UnitTrained/UnitCreated/UnitUpgraded carry the unit key but context:faction() is null on them
  -- (so they escape player attribution). Over-collect the unit's OWNING faction two ways: directly
  -- (if UNIT:faction() exists on this patch) and via its military force (the robust path). This is
  -- what lets the extractor tag a recruited TROOP TYPE to the player.
  add("unit_faction", function() return context:unit():faction():name() end)
  add("unit_force_faction", function() return context:unit():military_force():faction():name() end)
  add("tech", function() return context:technology() end)
  add("stance", function() return context:stance_adopted() end)
  add("mission", function() return context:mission():command_queue_index() end)
  add("dilemma", function() return context:dilemma() end)
  add("choice", function() return context:choice() end)
  add("autoresolved", function() return context:is_autoresolved() end)
  -- ‼ PROBE CANDIDATES -- the identities the existing ctx never reached (skills/equip/ritual/
  -- diplomacy-counterparty/captive-choice/edict). Each is pcall'd -> null if the getter is absent
  -- on this patch, so adding them is harmless (same overcollect discipline as everything else).
  -- The live probe run (god-mode `eval` forcing each action) shows which populate; the nulls are
  -- then dropped. `_key` variants extract a key from an object-returning getter -- one of the pair
  -- will be the clean string. Read first, but here the source is packed, so probe-by-outcome.
  add("skill", function() return context:skill_point_spent_on() end)           -- CharacterSkillPointAllocated
  add("skill_key", function() return context:skill_point_spent_on():key() end)
  add("ancillary", function() return context:ancillary() end)                  -- CharacterAncillaryGained
  add("ancillary_key", function() return context:ancillary():ancillary_key() end)
  add("item_variant", function() return context:item_variant_key() end)        -- CharacterArmoryItem(Un)equipped
  add("ritual", function() return context:ritual() end)                        -- Ritual(Started/Completed)Event
  add("ritual_key", function() return context:ritual():key() end)
  add("performing_faction", function() return context:performing_faction():name() end)
  add("second_faction", function() return context:second_faction():name() end) -- diplomacy counterparty
  add("target_faction", function() return context:target_faction():name() end)
  add("proposer", function() return context:proposing_faction():name() end)
  add("recipient", function() return context:recipient_faction():name() end)
  add("captive_option", function() return context:post_battle_captive_option() end) -- CharacterPostBattleCaptureOption
  add("captive_option2", function() return context:captive_option() end)
  add("char_faction", function() return context:character():faction():name() end)   -- robust faction on char events
  add("commandment", function() return context:commandment() end)              -- FactionLeaderIssuesEdict
  add("commandment_key", function() return context:commandment():key() end)
  add("incident_key", function() return context:incident():key() end)          -- IncidentEvent
  add("mission_key", function() return context:mission():mission_record_key() end)
  -- ‼ v2 GAP-CLOSERS -- the REAL accessors, confirmed against CA's generated scripting_doc.html.
  -- The originals above (commandment_key / ritual_key-via-:key() / captive_option) probed the
  -- WRONG method names and returned null on this patch. These are additive (overcollect); the
  -- next recording's audit resolves which populate. Every one is pcall'd -> null if absent.
  --   GAP 1 edict:   FactionLeaderIssuesEdict -> initiative_key() (String, "the initiative issued")
  --                  + province(); the API has NO :commandment(). Region STATE also dumps it.
  add("initiative_key", function() return context:initiative_key() end)
  add("edict_province", function() return context:province():key() end)
  --   GAP 2 rite:    Ritual*Event -> context:ritual() is an ACTIVE_RITUAL; its key getter is
  --                  ritual_key() ("ritual record key"), NOT :key(). Faction STATE also dumps it.
  add("ritual_rkey", function() return context:ritual():ritual_key() end)
  add("ritual_chain_key", function() return context:ritual():ritual_chain_key() end)
  add("ritual_category", function() return context:ritual():ritual_category() end)
  --   GAP 3 captive: CharacterPostBattleCaptureOption -> get_outcome_key() ("outcome key of the
  --                  selected captive option": release/ransom/enslave/execute) + get_record_key().
  --                  (The dedicated PostBattleEnslave/Release/Slaughter events, if they fire, are
  --                   already logged by name through this same ctx, making the option derivable.)
  add("captive_outcome_key", function() return context:get_outcome_key() end)
  add("captive_record_key", function() return context:get_record_key() end)
  --   GAP 4 ability: Character(Character/Garrison)TargetAction -> ability()/agent_action_key()/
  --                  attribute() (all String keys) + mission_result_* success/failure tiers.
  --                  NB CA's own method names carry the typo "critial" -- use it verbatim, with
  --                  correctly-spelled fallbacks in case a later patch fixes it.
  add("ability", function() return context:ability() end)
  add("agent_action_key", function() return context:agent_action_key() end)
  add("attribute", function() return context:attribute() end)
  add("mr_crit_success", function() return context:mission_result_critial_success() end)
  add("mr_success", function() return context:mission_result_success() end)
  add("mr_opportune_failure", function() return context:mission_result_opportune_failure() end)
  add("mr_failure", function() return context:mission_result_failure() end)
  add("mr_crit_failure", function() return context:mission_result_critial_failure() end)
  add("mr_critical_success", function() return context:mission_result_critical_success() end)
  add("mr_critical_failure", function() return context:mission_result_critical_failure() end)
  -- ‼ v3 GAP-CLOSERS -- event-context identities confirmed against CA's scripting_doc.html. All
  -- are try-wrapped -> null for events that lack them (same overcollect discipline as above).
  --   FIX 4  PooledResourceChanged: fires 42k+/run but recorded no currency identity. Doc-verified
  --   context of PooledResourceChanged: resource() -> POOLED_RESOURCE (key/value/maximum_value),
  --   amount() -> int (delta), factor() -> POOLED_RESOURCE_FACTOR (key -- e.g. changing_of_the_ways),
  --   has_faction() -> bool. PooledResourceThresholdOperationReached ->
  --   pooled_threshold_operation_record() (String). Makes per-race currency deltas attributable at
  --   event time (Tzeentch grimoires, Dwarf oathgold, skulls/souls/devotion...).
  add("pr_resource_key", function() return context:resource():key() end)
  add("pr_resource_value", function() return context:resource():value() end)
  add("pr_resource_max", function() return context:resource():maximum_value() end)
  add("pr_amount", function() return context:amount() end)
  add("pr_factor_key", function() return context:factor():key() end)
  add("pr_has_faction", function() return context:has_faction() end)
  add("pr_threshold_op", function() return context:pooled_threshold_operation_record() end)
  --   FIX 8  per-race event identities (doc-verified):
  add("choice_key", function() return context:choice_key() end)                     -- Bretonnia dilemma (alt to :choice() index)
  add("diplomacy_manipulation_category", function() return context:diplomacy_manipulation_category() end) -- High Elves Intrigue-at-Court
  add("diplomacy_target", function() return context:diplomacy_target_faction():name() end)
  add("dish_recipe", function() return context:dish():recipe() end)                 -- Greenskins FactionCookedDish (COOKING_DISH.recipe() -> String)
  add("converted_unit", function() return context:converted_unit():unit_key() end)  -- Slaanesh UnitConverted
  add("streak_effect", function() return context:streak_effect_record() end)        -- Khorne StreakEffectLevelsEntered (bloodletting level)
  add("teleport_to", function() return context:to_key() end)                        -- Wood Elves TeleportationNetworkMove* destination node
  add("teleport_from", function() return context:from_key() end)
  add("teleport_node", function() return context:node_key() end)
  --   ScriptEventAddBlessing has NO scripting_doc entry (custom DLC event); best-effort candidates,
  --   both try-wrapped -> null if absent, resolved by the next recording's audit.
  add("blessing", function() return context:blessing() end)
  add("blessing_key", function() return context:blessing_key() end)
  -- ‼ v4 GAP-CLOSERS -- confirmed MISSING from real recordings (session_250726, HE Alith), not
  -- just theoretically. The player-attributed *IssuedByPlayer events carry the region/faction but
  -- NOT the thing chosen; the key-bearing sibling event (UnitTrained / BuildingCompleted) fires
  -- faction-less across all 290 factions, so it can't be player-attributed by itself. Recover the
  -- identity DIRECTLY on the player event. All try-wrapped -> null (probe-by-outcome).
  --   GAP R1 recruit unit: RecruitmentItemIssuedByPlayer emits unit=null (context:unit() absent).
  --   ‼ VALIDATED NULL 2026-07-25 (live HE recruit of wh2_main_hef_inf_spearmen): ALL THREE below
  --   returned null -- this event's context does NOT expose the unit via unit_record()/
  --   main_unit_record(). context.string (component) = the FACTION name, not the unit. So the event
  --   carries NO recruited-unit identity on this patch. RECRUITMENT IS COVERED BY THE CLICK STREAM
  --   instead (clicks.csv lens=recruitment leaf <unit>_0_recruitable -> target_key = the unit key,
  --   player-attributed) -- see [[replication-two-channel-audit]]. Kept null-harmless in case a
  --   later patch adds them, but do NOT rely on them; the click channel is the real signal.
  add("recruit_unit_key", function() return context:unit_record():key() end)
  add("recruit_main_unit_key", function() return context:main_unit_record():key() end)
  add("recruit_unit_key2", function() return context:unit_record():unit_key() end)
  --   GAP R2 build key: BuildingConstructionIssuedByPlayer emits building=null (context:building()
  --   :name() absent on the ISSUED event). Try the record/key spellings + the queued-building
  --   accessor. (BuildingCompleted already carries it later; this makes the QUEUE action itself
  --   replayable without waiting for completion.)
  add("build_key", function() return context:building():key() end)
  add("build_record_key", function() return context:building_record():key() end)
  --   GAP R3 occupation decision: CharacterPerformsSettlementOccupationDecision emits char+region
  --   but NOT which option (occupy / loot / sack / raze / colonise). Cover the decision accessors;
  --   the settlement-captured UI is positional, so a direct key here is the robust signal.
  add("occupation_decision", function() return context:occupation_decision() end)
  add("occupation_decision_key", function() return context:occupation_decision_key() end)
  add("settlement_option", function() return context:settlement_occupation_decision() end)
  --   GAP R4 Cathay compass: WoMCompassUserDirectionSelectedEvent is faction-attributed but the
  --   docs found the chosen direction only in the CCO CcoCampaignWomCompassDirection<key>. These
  --   are UNVALIDATED candidate accessors -- resolved by probing a live Cathay (Miao Ying) turn.
  --   context.string (the `component` getter above) ALSO captures the clicked compass segment id as
  --   a passive fallback, since direction is chosen by clicking a compass UI button.
  add("compass_direction", function() return context:direction() end)
  add("compass_direction_key", function() return context:direction():key() end)
  add("compass_wom_direction", function() return context:wind_of_magic_direction() end)
  --   GAP R5 Cathay caravan: CaravanRecruited/Spawned/Completed carry the faction but the docs found
  --   destination + value missing. UNVALIDATED candidate accessors -- resolved by probing a live
  --   Cathay caravan dispatch. The caravan object's destination region + trade value are what a
  --   replay needs (which route was sent, worth how much).
  add("caravan_dest", function() return context:caravan():final_destination_region():name() end)
  add("caravan_dest_key", function() return context:caravan():destination_region_key() end)
  add("caravan_value", function() return context:caravan():value() end)
  add("caravan_master", function() return context:caravan():caravan_master():character_subtype_key() end)
  return table.concat(p, ",")
end

-- ‼ THE ENTRY POINT MUST BE A GLOBAL FUNCTION NAMED AFTER THE FILE.
--   WH3's mod loader loads script/campaign/mod/<name>.lua and then CALLS a global <name>().
--   The first version did its work at file scope, so the log said:
--       Mod [script\campaign\mod\twstate.lua] loaded successfully
--       twstate() not found, continuing              <- loaded, then did NOTHING
--   while every other mod showed `siege_logging() executed successfully`.
--   "loaded successfully" is NOT "ran" -- verify by the executed line, never by the load line.
function twstate()
  -- ‼ VERSION STAMP -- emitted once so every run self-identifies which mod scraped it (see
  -- TWSTATE_VERSION). Unversioned logs on disk are v1; this build stamps "dev". Filter runs on this.
  -- ‼ TWSTATE_VERSION is now a STRING -- jv() quotes it so the emitted line is valid JSON
  -- (`{"kind":"version","twstate":"dev"}`); a bare .. of a string would produce invalid JSON.
  emit('{"kind":"version","twstate":' .. jv(TWSTATE_VERSION) .. '}')
  -- ‼ THE BASELINE DUMP MUST NOT DEPEND ON first_tick ALONE.
  --   Verified live: on a LOADED SAVE (turn 15) the mod armed and 46 events fired, but
  --   `round_begin`/`char` stayed at ZERO -- add_first_tick_callback never fired, because the
  --   first tick had long passed. A build order needs the turn-N baseline to diff against, so a
  --   baseline that only exists on fresh campaigns is a baseline that is missing exactly when
  --   we resume. Belt and braces:
  --     1. dump NOW (pcall'd -- if the model is not ready yet this is a no-op, not a crash)
  --     2. dump on first tick   (fresh campaign)
  --     3. dump every WorldStartRound (the steady state)
  --   Redundant dumps are free: the consumer keys on `turn` and the last write wins.
  --   Omission is not free. Overcollect.
  pcall(function() dump_round() end)
  pcall(function() cm:add_first_tick_callback(function() dump_round() end) end)
  core:add_listener("twstate_round", "WorldStartRound", true, function() dump_round() end, true)
  -- FactionTurnStart also ticks every turn and fires on loaded saves where WorldStartRound may
  -- not; gated to one dump per turn by the consumer, which dedupes on `turn`.
  core:add_listener("twstate_fts", "FactionTurnStart", true, function() dump_round() end, true)

  -- ‼ recorder-v5 Task 1: the multi-scrape cadence listeners.
  --  (a) IN-PLAYER-TURN gate ON: the HUMAN faction's turn just started. Only then may the
  --      between-action light scrape fire (AI turns never flip this -> the 290x avoidance).
  core:add_listener("twstate_player_ts", "FactionTurnStart", true, function(context)
    pcall(function()
      if try(function() return context:faction():is_human() end) == true then
        in_player_turn = true
      end
    end)
  end, true)
  --  (b) TURN-END full scrape + gate OFF: the HUMAN faction's turn is ending. Do the end-of-turn
  --      full all-faction scrape (separate last_end_turn gate), then close the player-turn window.
  core:add_listener("twstate_fte", "FactionTurnEnd", true, function(context)
    pcall(function()
      if try(function() return context:faction():is_human() end) == true then
        dump_round_end()
        in_player_turn = false
      end
    end)
  end, true)

  -- Listen for EVERY event in the list. An event name that does not exist on this patch simply
  -- never fires -- registering it is harmless. That asymmetry is exactly why we overcollect:
  -- a wrong guess costs nothing, a missing listener costs the run.
  local armed = 0
  for _, ev in ipairs(EVENTS) do
    local ok = pcall(function()
      core:add_listener(
        "twstate_ev_" .. ev, ev, true,
        function(context)
          pcall(function()
            local turn = try(function() return cm:model():turn_number() end) or -1
            -- in_player_turn on EVERY event: many player actions fire as faction-less ScriptEvent*
            -- (e.g. ScriptEventRitualofRuinPerformed) that the player-only filter would otherwise drop.
            -- True iff fired during the human's own turn -> the extractor keeps them as the player's.
            emit('{"kind":"event","event":' .. q(ev) .. ',"turn":' .. turn ..
                 ',"in_player_turn":' .. tostring(in_player_turn) ..
                 "," .. ctx(context) .. "}")
            -- ‼ recorder-v5 Task 1: a SIGNIFICANT player-action event during the PLAYER's own turn
            -- triggers a between-action LIGHT (human-only) scrape so each action's state delta is
            -- isolatable. in_player_turn gates out every AI turn (the 290x avoidance).
            if in_player_turn and SIGNIFICANT[ev] then
              pcall(function() dump_player(turn, ev) end)
            end
          end)
        end,
        true
      )
    end)
    if ok then armed = armed + 1 end
  end

  emit('{"kind":"armed","note":"multi-scrape cadence (turn-start full + turn-end full + '
       .. 'between-player-action light) + ' .. armed .. ' event listeners, no filtering"}')
end
