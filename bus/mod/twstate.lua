


local function q(s) return '"' .. tostring(s or ""):gsub('"', '\\"') .. '"' end


local TRY_FAILS, TRY_FAIL_N = {}, 0
local TRY_FAIL_CAP = 24

local function try(f)
  local ok, v = pcall(f)
  if ok then return v end
  local msg = tostring(v)
  if #msg > 160 then msg = msg:sub(1, 160) end
  if TRY_FAILS[msg] == nil then
    if TRY_FAIL_N >= TRY_FAIL_CAP then return nil end
    TRY_FAIL_N = TRY_FAIL_N + 1
    TRY_FAILS[msg] = 0
  end
  TRY_FAILS[msg] = TRY_FAILS[msg] + 1
  return nil
end


local function nn(x)
  if x == nil then return nil end
  local ok, isnull = pcall(function() return x:is_null_interface() end)
  if ok and isnull == false then return x end
  return nil
end


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

local function flush_try_fails(turn, where)
  if TRY_FAIL_N == 0 then return end
  local rows = {}
  for msg, n in pairs(TRY_FAILS) do
    rows[#rows + 1] = '{"err":' .. q(msg) .. ',"n":' .. n .. '}'
  end
  TRY_FAILS, TRY_FAIL_N = {}, 0
  emit('{"kind":"try_fails","turn":' .. jv(turn) .. ',"where":' .. q(where) ..
    ',"fails":[' .. table.concat(rows, ",") .. ']}')
end


local TWSTATE_VERSION = "v5"


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


    '"garrisoned":' .. jv(try(function() return c:has_garrison_residence() end)),
    '"is_governor":' .. jv(try(function() return c:is_governor() end)),
    '"garrisoned_old":' .. jv(try(function() return c:is_garrison_commander() end)),
    '"region":' .. jv(try(function() return c:region():name() end)),


    '"action_points_pct":' .. jv(try(function() return c:action_points_remaining_percent() end)),
    '"action_points_per_turn":' .. jv(try(function() return c:action_points_per_turn() end)),
    '"action_points":' .. jv(try(function() return c:action_points_remaining() end)),
    '"loyalty":' .. jv(try(function() return c:loyalty() end)),
    '"can_move":' .. jv(try(function() return c:action_points_remaining_percent() > 0 end)),
    '"can_move_old":' .. jv(try(function() return c:has_movement_points() end)),

    '"in_settlement":' .. jv(try(function() return c:in_settlement() end)),
    '"in_settlement_old":' .. jv(try(function() return c:is_in_settlement() end)),
    '"at_sea":' .. jv(try(function() return c:is_at_sea() end)),
    '"faction_leader":' .. jv(try(function() return c:is_faction_leader() end)),
  }


  local sk = {}
  try(function()
    local l = c:character_skill_points_list()
    for i = 0, l:num_items() - 1 do sk[#sk + 1] = q(l:item_at(i)) end
  end)
  parts[#parts + 1] = '"skills":[' .. table.concat(sk, ",") .. "]"
  parts[#parts + 1] = '"background_skill":' .. jv(try(function() return c:background_skill() end))


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


  local eb = {}
  try(function()
    local l = c:effect_bundles(); if not l then return end
    for i = 0, l:num_items() - 1 do eb[#eb + 1] = q(try(function() return l:item_at(i):key() end)) end
  end)
  parts[#parts + 1] = '"effect_bundles":[' .. table.concat(eb, ",") .. "]"
  local tr = {}
  try(function()


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


local function dump_force(turn, fkey, mf)
  local mf_cqi = try(function() return mf:command_queue_index() end)
  if mf_cqi == nil then return end


  local is_horde = is_horde_force(mf)
  emit("{" .. table.concat({
    '"kind":"force"', '"turn":' .. turn, '"faction":' .. q(fkey),
    '"mf_cqi":' .. jv(mf_cqi),
    '"has_general":' .. jv(try(function() return mf:has_general() end)),
    '"stance":' .. jv(try(function() return mf:active_stance() end)),
    '"is_army":' .. jv(try(function() return mf:is_army() end)),
    '"upkeep":' .. jv(try(function() return mf:upkeep() end)),
    '"units":' .. jv(try(function() return mf:unit_list():num_items() end)),


    '"is_navy":' .. jv(try(function() return mf:is_navy() end)),


    '"mp_pct":' .. jv(try(function() local g = nn(mf:general_character()); return g and g:action_points_remaining_percent() end)),
    '"mp_pct_old":' .. jv(try(function() local g = nn(mf:general_character()); return g and g:percentage_of_action_points_remaining() end)),


    '"region":' .. jv(try(function() local g = nn(mf:general_character()); local rg = g and nn(g:region()); return rg and rg:name() end)),
    '"region_garrison":' .. jv(try(function() local gr = nn(mf:garrison_residence()); local rg = gr and nn(gr:region()); return rg and rg:name() end)),
    '"region_old":' .. jv(try(function() return mf:region():name() end)),

    '"strength":' .. jv(try(function() return mf:strength() end)),
    '"morale":' .. jv(try(function() return mf:morale() end)),
    '"force_type":' .. jv(try(function() local ft = nn(mf:force_type()); return ft and ft:key() end)),
    '"is_armed_citizenry":' .. jv(try(function() return mf:is_armed_citizenry() end)),
    '"contains_mercenaries":' .. jv(try(function() return mf:contains_mercenaries() end)),
    '"recruitment_item_count":' .. jv(try(function() return mf:recruitment_item_count() end)),

    '"is_horde":' .. jv(is_horde),
  }, ",") .. "}")


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


local function dump_region(turn, r)
  local rname = try(function() return r:name() end)
  if rname == nil then return end


  local v_growth   = try(function() return r:faction_province_growth() end)
  local v_growthpt = try(function() return r:faction_province_growth_per_turn() end)
  local v_numb     = try(function() return r:num_buildings() end)
  local v_gdp      = try(function() return r:gdp() end)


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

  try(function()
    local sl = r:slot_list()
    for i = 0, sl:num_items() - 1 do
      local s = sl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"slot"', '"turn":' .. turn, '"region":' .. q(rname),


        '"slot_index":' .. i,
        '"slot_key":' .. jv(try(function() return s:key() end)),
        '"slot_id":' .. jv(try(function() return s:slot_id() end)),
        '"slot_cqi":' .. jv(try(function() return s:command_queue_index() end)),


        '"slot_type":' .. jv(try(function() return s:template_key() end)),
        '"slot_type_name":' .. jv(try(function() return s:type() end)),
        '"slot_type_old":' .. jv(try(function() return s:template() end)),
        '"has_building":' .. jv(try(function() return s:has_building() end)),
        '"building":' .. jv(try(function() return s:building():name() end)),
      }, ",") .. "}")
    end
  end)


  try(function()
    local prm = nn(r:pooled_resource_manager()); if not prm then return end
    local pl = prm:resources(); if not pl then return end
    for i = 0, pl:num_items() - 1 do
      local pr = pl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"region_pooled"', '"turn":' .. turn, '"region":' .. q(rname),
        '"resource":' .. jv(try(function() return pr:key() end)),
        '"value":' .. jv(try(function() return pr:value() end)),


        '"max":' .. jv(try(function() return pr:maximum_value() end)),
      }, ",") .. "}")
    end
  end)


  try(function()
    local l = r:effect_bundles(); if not l then return end
    for i = 0, l:num_items() - 1 do
      emit("{" .. table.concat({
        '"kind":"region_effect_bundle"', '"turn":' .. turn, '"region":' .. q(rname),
        '"effect_bundle":' .. jv(try(function() return l:item_at(i):key() end)),
      }, ",") .. "}")
    end
  end)


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


    '"income":' .. jv(try(function() return f:income() end)),
    '"net_income":' .. jv(try(function() return f:net_income() end)),
    '"expenditure":' .. jv(try(function() return f:expenditure() end)),


    '"rank":' .. jv(try(function() return f:imperium_level() end)),
    '"rank_old":' .. jv(try(function() return f:rank() end)),
    '"leader_cqi":' .. jv(try(function() return f:faction_leader():command_queue_index() end)),
    '"has_home_region":' .. jv(try(function() return f:has_home_region() end)),


    '"is_researching":' .. jv(try(function() return f:is_currently_researching() end)),
    '"research_queue_idle":' .. jv(try(function() return f:research_queue_idle() end)),
    '"researching":' .. jv(try(function() return f:current_research_key() end)),
    '"has_rituals":' .. jv(try(function() return f:has_rituals() end)),

    '"num_provinces":' .. jv(try(function() return f:num_provinces() end)),
    '"num_complete_provinces":' .. jv(try(function() return f:num_complete_provinces() end)),
    '"num_generals":' .. jv(try(function() return f:num_generals() end)),
    '"num_allies":' .. jv(try(function() return f:num_allies() end)),
    '"tax_level":' .. jv(try(function() return f:tax_level() end)),


    '"trade_value":' .. jv(try(function() return f:trade_value() end)),
    '"trade_value_pct":' .. jv(try(function() return f:trade_value_percent() end)),
  }, ",") .. "}")


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


  try(function()
    local l = f:effect_bundles(); if not l then return end
    for i = 0, l:num_items() - 1 do
      emit("{" .. table.concat({
        '"kind":"faction_effect_bundle"', '"turn":' .. turn, '"faction":' .. q(fkey),
        '"effect_bundle":' .. jv(try(function() return l:item_at(i):key() end)),
      }, ",") .. "}")
    end
  end)


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


  emit("{" .. table.concat({
    '"kind":"tech_count"', '"turn":' .. turn, '"faction":' .. q(fkey),
    '"completed":' .. jv(try(function() return f:num_completed_technologies() end)),
  }, ",") .. "}")


  try(function()
    local pl = f:pooled_resource_manager():resources()
    for i = 0, pl:num_items() - 1 do
      local pr = pl:item_at(i)
      emit("{" .. table.concat({
        '"kind":"pooled"', '"turn":' .. turn, '"faction":' .. q(fkey),
        '"resource":' .. jv(try(function() return pr:key() end)),
        '"value":' .. jv(try(function() return pr:value() end)),


        '"max":' .. jv(try(function() return pr:maximum_value() end)),
      }, ",") .. "}")
    end
  end)


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


local function scrape_all_factions(turn)
  local world = cm:model():world()


  local fl = world:faction_list()
  for i = 0, fl:num_items() - 1 do
    local f = fl:item_at(i)
    local fkey = try(function() return f:name() end) or "?"
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
  end


  try(function()
    local rl = world:region_manager():region_list()
    for i = 0, rl:num_items() - 1 do dump_region(turn, rl:item_at(i)) end
  end)
end


local last_turn = -1

local function dump_round()
  local ok, err = pcall(function()
    local turn = try(function() return cm:model():turn_number() end) or -1
    if turn == last_turn then return end
    last_turn = turn
    emit('{"kind":"round_begin","turn":' .. turn .. "}")
    scrape_all_factions(turn)
    emit('{"kind":"round_end","turn":' .. turn .. "}")
    flush_try_fails(turn, "dump_round")
  end)
  if not ok then emit('{"kind":"error","msg":' .. q(tostring(err)) .. "}") end
end


local last_end_turn = -1

local function dump_round_end()
  local ok, err = pcall(function()
    local turn = try(function() return cm:model():turn_number() end) or -1
    if turn == last_end_turn then return end
    last_end_turn = turn
    emit('{"kind":"round_end","turn":' .. turn .. "}")
    scrape_all_factions(turn)
    flush_try_fails(turn, "dump_round_end")
  end)
  if not ok then emit('{"kind":"error","msg":' .. q(tostring(err)) .. "}") end
end


local in_player_turn = false


local SIGNIFICANT = {}
for _, ev in ipairs({

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

  "CharacterRazedSettlement", "CharacterSackedSettlement", "CharacterLootedSettlement",
}) do SIGNIFICANT[ev] = true end


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

    try(function()
      local rl = f:region_list()
      for i = 0, rl:num_items() - 1 do dump_region(turn, rl:item_at(i)) end
    end)
    flush_try_fails(turn, "dump_player")
  end)
  if not ok then emit('{"kind":"error","msg":' .. q(tostring(err)) .. "}") end
end


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


  "ScriptEventPoSStage1Completed", "ScriptEventPoSStage2Completed", "ScriptEventPoSStage3Completed",


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


local function ctx(context)
  local p = {}
  local function add(k, f) p[#p + 1] = '"' .. k .. '":' .. jv(try(f)) end


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


  add("unit_faction", function() return context:unit():faction():name() end)
  add("unit_force_faction", function() return context:unit():military_force():faction():name() end)
  add("tech", function() return context:technology() end)
  add("stance", function() return context:stance_adopted() end)
  add("mission", function() return context:mission():command_queue_index() end)
  add("dilemma", function() return context:dilemma() end)
  add("choice", function() return context:choice() end)
  add("autoresolved", function() return context:is_autoresolved() end)


  add("skill", function() return context:skill_point_spent_on() end)
  add("skill_key", function() return context:skill_point_spent_on():key() end)
  add("ancillary", function() return context:ancillary() end)
  add("ancillary_key", function() return context:ancillary():ancillary_key() end)
  add("item_variant", function() return context:item_variant_key() end)
  add("ritual", function() return context:ritual() end)
  add("ritual_key", function() return context:ritual():key() end)
  add("performing_faction", function() return context:performing_faction():name() end)
  add("second_faction", function() return context:second_faction():name() end)
  add("target_faction", function() return context:target_faction():name() end)
  add("proposer", function() return context:proposing_faction():name() end)
  add("recipient", function() return context:recipient_faction():name() end)
  add("captive_option", function() return context:post_battle_captive_option() end)
  add("captive_option2", function() return context:captive_option() end)
  add("char_faction", function() return context:character():faction():name() end)
  add("commandment", function() return context:commandment() end)
  add("commandment_key", function() return context:commandment():key() end)
  add("incident_key", function() return context:incident():key() end)
  add("mission_key", function() return context:mission():mission_record_key() end)


  add("initiative_key", function() return context:initiative_key() end)
  add("edict_province", function() return context:province():key() end)


  add("ritual_rkey", function() return context:ritual():ritual_key() end)
  add("ritual_chain_key", function() return context:ritual():ritual_chain_key() end)
  add("ritual_category", function() return context:ritual():ritual_category() end)


  add("captive_outcome_key", function() return context:get_outcome_key() end)
  add("captive_record_key", function() return context:get_record_key() end)


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


  add("pr_resource_key", function() return context:resource():key() end)
  add("pr_resource_value", function() return context:resource():value() end)
  add("pr_resource_max", function() return context:resource():maximum_value() end)
  add("pr_amount", function() return context:amount() end)
  add("pr_factor_key", function() return context:factor():key() end)
  add("pr_has_faction", function() return context:has_faction() end)
  add("pr_threshold_op", function() return context:pooled_threshold_operation_record() end)

  add("choice_key", function() return context:choice_key() end)
  add("diplomacy_manipulation_category", function() return context:diplomacy_manipulation_category() end)
  add("diplomacy_target", function() return context:diplomacy_target_faction():name() end)
  add("dish_recipe", function() return context:dish():recipe() end)
  add("converted_unit", function() return context:converted_unit():unit_key() end)
  add("streak_effect", function() return context:streak_effect_record() end)
  add("teleport_to", function() return context:to_key() end)
  add("teleport_from", function() return context:from_key() end)
  add("teleport_node", function() return context:node_key() end)


  add("blessing", function() return context:blessing() end)
  add("blessing_key", function() return context:blessing_key() end)


  add("recruit_unit_key", function() return context:unit_record():key() end)
  add("recruit_main_unit_key", function() return context:main_unit_record():key() end)
  add("recruit_unit_key2", function() return context:unit_record():unit_key() end)


  add("build_key", function() return context:building():key() end)
  add("build_record_key", function() return context:building_record():key() end)


  add("occupation_decision", function() return context:occupation_decision() end)
  add("occupation_decision_key", function() return context:occupation_decision_key() end)
  add("settlement_option", function() return context:settlement_occupation_decision() end)


  add("compass_direction", function() return context:direction() end)
  add("compass_direction_key", function() return context:direction():key() end)
  add("compass_wom_direction", function() return context:wind_of_magic_direction() end)


  add("caravan_dest", function() return context:caravan():final_destination_region():name() end)
  add("caravan_dest_key", function() return context:caravan():destination_region_key() end)
  add("caravan_value", function() return context:caravan():value() end)
  add("caravan_master", function() return context:caravan():caravan_master():character_subtype_key() end)
  return table.concat(p, ",")
end


function twstate()


  emit('{"kind":"version","twstate":' .. jv(TWSTATE_VERSION) .. '}')


  pcall(function() dump_round() end)
  pcall(function() cm:add_first_tick_callback(function() dump_round() end) end)
  core:add_listener("twstate_round", "WorldStartRound", true, function() dump_round() end, true)


  core:add_listener("twstate_fts", "FactionTurnStart", true, function() dump_round() end, true)


  core:add_listener("twstate_player_ts", "FactionTurnStart", true, function(context)
    pcall(function()
      if try(function() return context:faction():is_human() end) == true then
        in_player_turn = true
      end
    end)
  end, true)


  core:add_listener("twstate_fte", "FactionTurnEnd", true, function(context)
    pcall(function()
      if try(function() return context:faction():is_human() end) == true then
        dump_round_end()
        in_player_turn = false
      end
    end)
  end, true)


  local armed = 0
  for _, ev in ipairs(EVENTS) do
    local ok = pcall(function()
      core:add_listener(
        "twstate_ev_" .. ev, ev, true,
        function(context)
          pcall(function()
            local turn = try(function() return cm:model():turn_number() end) or -1


            emit('{"kind":"event","event":' .. q(ev) .. ',"turn":' .. turn ..
                 ',"in_player_turn":' .. tostring(in_player_turn) ..
                 "," .. ctx(context) .. "}")


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
