


local CMD_PATH = "@@BUS_CMD_PATH@@"
local OUT_PATH = "@@BUS_OUT_PATH@@"
local POLL_SECONDS = 0.02


local NULL = setmetatable({}, {})
local INF = math.huge or (1 / 0)
local ESCAPES = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
local function esc(s)
  return (tostring(s):gsub('[%c"\\]', function(c)
    local e = ESCAPES[c]; if e then return e end
    if string.byte then return string.format('\\u%04x', string.byte(c)) end
    return " "
  end))
end
local encode
local function encode_table(v)
  if #v > 0 then
    local p = {}; for i = 1, #v do p[i] = encode(v[i]) end
    return "[" .. table.concat(p, ",") .. "]"
  end
  local p = {}; for k, val in pairs(v) do p[#p + 1] = '"' .. esc(k) .. '":' .. encode(val) end
  return "{" .. table.concat(p, ",") .. "}"
end
encode = function(v)
  local t = type(v)
  if v == nil or v == NULL then return "null" end
  if t == "boolean" then return tostring(v) end
  if t == "number" then if v ~= v or v == INF or v == -INF then return "null" end return string.format("%.14g", v) end
  if t == "string" then return '"' .. esc(v) .. '"' end
  if t == "table" then return encode_table(v) end
  return '"<' .. t .. '>"'
end
local function or_null(v) if v == nil then return NULL end return v end

local function say(m) if ModLog then pcall(ModLog, "[twcontrol] " .. m) end end
local function now_epoch()
  local ok, v = pcall(function() return os.time() end)
  if ok and v then return v end
  return nil
end
local function now_clock()
  local ok, v = pcall(function() return os.clock() end)
  if ok and v then return v end
  return nil
end
local TRY_FAILS, TRY_FAIL_N = {}, 0
local TRY_FAIL_CAP = 24

local function drain_try_fails()
  if TRY_FAIL_N == 0 then return nil end
  local out = {}
  for msg, n in pairs(TRY_FAILS) do out[#out + 1] = { err = msg, n = n } end
  TRY_FAILS, TRY_FAIL_N = {}, 0
  return out
end

local function log(tbl)
  if type(tbl) == "table" then
    if tbl.ts == nil then tbl.ts = now_epoch() end
    if tbl.clk == nil then tbl.clk = now_clock() end
    if tbl.try_fails == nil then tbl.try_fails = drain_try_fails() end
  end
  local ok, line = pcall(encode, tbl)
  if not ok then say("encode failed"); return end
  local f = io.open(OUT_PATH, "a"); if not f then return end
  f:write(line, "\n"); f:flush(); f:close()
end


local function try(fn)
  local ok, v = pcall(fn)
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
local function turn() return try(function() return cm:turn_number() end) or -1 end

local function human_faction()
  return try(function() return cm:get_local_faction(true) end)
      or try(function()
        local fl = cm:model():world():faction_list()
        for i = 0, fl:num_items() - 1 do local f = fl:item_at(i); if f:is_human() then return f end end
      end)
end

local function root() return try(function() return core:get_ui_root() end) end


local function descend(parent, name)


  local idx = string.match(name, "^#(%d+)$")
  local c
  if idx then
    c = try(function() return parent:Find(tonumber(idx)) end)
  else
    c = try(function() return parent:Find(name) end)
  end
  if not c then return nil end
  return try(function() return UIComponent(c) end)
end

local function resolve(path_str)
  local parts = {}
  for p in string.gmatch(path_str, "[^|]+") do parts[#parts + 1] = p end
  if #parts == 1 and parts[1] == "@root" then return root(), parts end
  if #parts == 0 then return nil, parts end
  local node = root()
  for _, name in ipairs(parts) do
    node = descend(node, name)
    if not node then return nil, parts end
  end
  return node, parts
end


local function xy(uic)
  local ok, x, y = pcall(function() return uic:Position() end)
  if ok then return x, y end
  return nil, nil
end

local function describe(uic)
  if not uic then return { found = false } end
  local x, y = xy(uic)
  return {
    found = true,
    id = or_null(try(function() return uic:Id() end)),
    visible = or_null(try(function() return uic:Visible() end)),
    state = or_null(try(function() return uic:CurrentState() end)),
    children = or_null(try(function() return uic:ChildCount() end)),
    x = or_null(x), y = or_null(y),
    w = or_null(try(function() return uic:Width() end)),
    h = or_null(try(function() return uic:Height() end)),


    text = or_null(try(function() return uic:GetStateText() end)),
    text_label = or_null(try(function() return uic:GetStateTextLabel() end)),
    tooltip = or_null(try(function() return uic:GetTooltipText() end)),
  }
end


local CCO_TYPES = {
  "CcoCampaignUnit", "CcoCampaignCharacter", "CcoMainUnitRecord",
  "CcoCampaignSettlement", "CcoCampaignModelRegion", "CcoCampaignAncillary",
  "CcoCampaignRitual", "CcoBuildingChainRecord", "CcoCampaignFaction",
  "CcoCampaignProvince",
}


local function context_id(uic)
  if not uic then return nil end
  for _, t in ipairs(CCO_TYPES) do
    local v = try(function() return uic:GetContextObjectId(t) end)
    if v ~= nil and v ~= "" then return t .. ":" .. tostring(v) end
  end
  return nil
end


local handlers = {}

function handlers.snapshot(seq)
  local f = human_faction()
  local leader = f and try(function() return f:faction_leader() end)


  local income = try(function() return f:income() end)
      or try(function() return f:net_income() end)
  log({
    seq = seq, cmd = "snapshot", turn = turn(),
    faction = or_null(f and try(function() return f:name() end)),
    treasury = or_null(f and try(function() return f:treasury() end)),
    income = or_null(income),
    regions = or_null(f and try(function() return f:region_list():num_items() end)),
    leader_cqi = or_null(leader and try(function() return leader:command_queue_index() end)),
    leader_x = or_null(leader and try(function() return leader:logical_position_x() end)),
    leader_y = or_null(leader and try(function() return leader:logical_position_y() end)),
  })
end

function handlers.find(seq, rest)
  local uic, parts = resolve(rest)
  local kids = {}
  local kctx = {}
  if uic then
    local n = try(function() return uic:ChildCount() end) or 0
    for i = 0, n - 1 do
      local child = try(function() return UIComponent(uic:Find(i)) end)

      kids[#kids + 1] = or_null(child and try(function() return child:Id() end))

      kctx[#kctx + 1] = or_null(child and context_id(child))
    end
  end
  local res = describe(uic)
  res.context = or_null(context_id(uic))
  log({ seq = seq, cmd = "find", path = rest, result = res, child_ids = kids,
        child_contexts = kctx })
end


function handlers.tree(seq, rest)
  local path, md, mn = string.match(rest, "^(%S+)%s+(%d+)%s+(%d+)$")
  if not path then
    path = string.match(rest, "^(%S+)") or rest
    md, mn = 16, 500
  else
    md, mn = tonumber(md), tonumber(mn)
  end
  local rootuic = resolve(path)
  local nodes = {}
  local truncated = false
  if rootuic then
    local queue = { { rootuic, path, 0 } }
    local head = 1
    while head <= #queue do
      if #nodes >= mn then truncated = true; break end
      local item = queue[head]; head = head + 1
      local uic, p, depth = item[1], item[2], item[3]
      local d = describe(uic)
      d.path = p
      d.context = or_null(context_id(uic))
      nodes[#nodes + 1] = d
      if depth < md and d.visible ~= false then
        local n = try(function() return uic:ChildCount() end) or 0
        for i = 0, n - 1 do
          local child = try(function() return UIComponent(uic:Find(i)) end)
          if child then
            local cid = try(function() return child:Id() end) or ("#" .. i)
            queue[#queue + 1] = { child, p .. "|" .. cid, depth + 1 }
          end
        end
      end
    end
  end
  log({ seq = seq, cmd = "tree", path = path, count = #nodes, truncated = truncated,
        found = (rootuic ~= nil), nodes = nodes, turn = turn() })
end


local function root_names()
  local r, out = root(), {}
  if not r then return out end
  local n = try(function() return r:ChildCount() end) or 0
  for i = 0, n - 1 do
    local c = try(function() return UIComponent(r:Find(i)) end)
    if c and try(function() return c:Visible() end) then
      out[#out + 1] = try(function() return c:Id() end)
    end
  end
  return out
end


function handlers.click(seq, rest)
  local uic = resolve(rest)
  local info = { seq = seq, cmd = "click", path = rest, found = (uic ~= nil), turn = turn() }
  if uic then
    local x, y = xy(uic)
    info.id = or_null(try(function() return uic:Id() end))
    info.state_before = or_null(try(function() return uic:CurrentState() end))
    info.visible_before = or_null(try(function() return uic:Visible() end))


    info.visible_from_root = or_null(try(function() return uic:VisibleFromRoot() end))
    info.x, info.y = or_null(x), or_null(y)
    info.w = or_null(try(function() return uic:Width() end))
    info.h = or_null(try(function() return uic:Height() end))
    info.roots_before = root_names()
    if info.visible_from_root == false or info.visible_before == false then
      info.clicked = false
      info.refused = "target_not_visible"
    else
      info.clicked = try(function() uic:SimulateLClick(); return true end) == true
    end
    info.state_after = or_null(try(function() return uic:CurrentState() end))
    info.visible_after = or_null(try(function() return uic:Visible() end))
    info.roots_after = root_names()
    info.changed = (info.state_before ~= info.state_after)
                or (info.visible_before ~= info.visible_after)
                or (#info.roots_before ~= #info.roots_after)
  else
    info.clicked = false
  end
  log(info)
end


function handlers.show(seq, rest)
  local uic = resolve(rest)
  local before, after = nil, nil
  local ok = false
  local fronted = false
  if uic then
    before = try(function() return uic:Visible() end)
    ok = try(function() uic:SetVisible(true); return true end) == true


    fronted = try(function() uic:RegisterTopMost(); return true end) == true
    after = try(function() return uic:Visible() end)
  end
  log({ seq = seq, cmd = "show", path = rest, found = (uic ~= nil),
        visible_before = or_null(before), set = ok, topmost = fronted,
        visible_after = or_null(after), roots_after = root_names(), turn = turn() })
end

function handlers.ccmd(seq, rest)
  local before = root_names()
  local ok, err = pcall(function() common.call_context_command(rest) end)
  log({ seq = seq, cmd = "ccmd", expr = rest, dispatched = (ok == true),
        error = or_null(ok and nil or tostring(err)),
        roots_before = before, roots_after = root_names(), turn = turn() })
end

function handlers.move(seq, rest)
  local cqi, x, y = string.match(rest, "^(%d+)%s+(%-?%d+)%s+(%-?%d+)")
  cqi, x, y = tonumber(cqi), tonumber(x), tonumber(y)
  local ok = false
  if cqi and x and y then ok = try(function() cm:move_character(cqi, x, y, false, true); return true end) == true end
  log({ seq = seq, cmd = "move", cqi = or_null(cqi), x = or_null(x), y = or_null(y), ordered = ok, turn = turn() })
end

local assist_watch = {}

local function assist_host(hero)
  local a = try(function() return cm:get_character_by_cqi(hero) end)
  if not a then return nil end
  if try(function() return a:is_embedded_in_military_force() end) ~= true then return nil end
  local f = try(function() return a:embedded_in_military_force() end)
  if not f then return nil end
  return try(function() return f:general_character():command_queue_index() end)
end

local function assist_force_cqi(target)
  local t = try(function() return cm:get_character_by_cqi(target) end)
  if not t then return nil end
  local f = try(function() return t:military_force() end)
  if not f then return nil end
  return try(function() return f:command_queue_index() end)
end

local function assist_cleanup()
  for i = #assist_watch, 1, -1 do
    local w = assist_watch[i]
    if assist_host(w.hero) ~= w.target then
      local shared = false
      for j = 1, #assist_watch do
        local o = assist_watch[j]
        if j ~= i and o.target == w.target and o.bundle == w.bundle
           and assist_host(o.hero) == o.target then
          shared = true
        end
      end
      local force = nil
      if not shared then
        force = assist_force_cqi(w.target)
        if force then try(function() cm:remove_effect_bundle_from_force(w.bundle, force) end) end
      end
      table.remove(assist_watch, i)
      log({ cmd = "assist_cleanup", hero = w.hero, target = w.target, bundle = w.bundle,
            force = or_null(force), shared = shared, turn = turn() })
    end
  end
end

function handlers.assist(seq, rest)
  local hero, target, bundle, turns, actor, aturns, effects = string.match(
    rest, "^(%d+)%s+(%d+)%s+(%S+)%s+(%-?%d+)%s+(%S+)%s+(%-?%d+)%s+(%S+)$")
  hero, target, turns, aturns = tonumber(hero), tonumber(target), tonumber(turns), tonumber(aturns)
  local out = { seq = seq, cmd = "assist", hero = or_null(hero), target = or_null(target),
                bundle = or_null(bundle), turn = turn() }
  if not (hero and target and bundle and turns and actor and aturns and effects) then
    out.error = "unparsed: " .. tostring(rest)
    log(out); return
  end
  local a = try(function() return cm:get_character_by_cqi(hero) end)
  local t = try(function() return cm:get_character_by_cqi(target) end)
  if not a then out.error = "NO-AGENT"; log(out); return end
  if not t or try(function() return t:is_null_interface() end) == true then
    out.error = "NULL-TARGET"; log(out); return
  end
  if try(function() return t:has_military_force() end) ~= true then
    out.error = "TARGET-HAS-NO-FORCE"; log(out); return
  end
  local force = try(function() return t:military_force() end)
  local host = assist_host(hero)
  out.host_before = or_null(host)
  if host ~= nil and host ~= target then out.error = "EMBEDDED-ELSEWHERE"; log(out); return end
  if host == nil then
    out.embedded = try(function() cm:embed_agent_in_force(a, force); return true end) == true
    if not out.embedded then out.error = "EMBED-FAILED"; log(out); return end
  else
    out.embedded = "already"
  end

  local eb = try(function() return cm:create_new_custom_effect_bundle(bundle) end)
  if eb == nil then out.error = "NO-BUNDLE"; log(out); return end
  try(function() eb:set_duration(turns) end)
  local n = 0
  for key, scope, value in string.gmatch(effects, "([^|;]+)|([^|;]+)|([^|;]+)") do
    if try(function() eb:add_effect(key, scope, tonumber(value)); return true end) == true then
      n = n + 1
    end
  end
  out.effects = n
  if n == 0 then out.error = "NO-EFFECTS"; log(out); return end
  out.applied = try(function() cm:apply_custom_effect_bundle_to_force(eb, force); return true end) == true
  out.actor_applied = try(function()
    cm:apply_effect_bundle_to_character(actor, a, aturns); return true end) == true
  out.ap_zeroed = try(function()
    cm:zero_action_points(cm:char_lookup_str(a)); return true end) == true
  out.active = try(function() return force:has_effect_bundle(bundle) end)
  if out.applied then
    assist_watch[#assist_watch + 1] = { hero = hero, target = target, bundle = bundle }
  end
  log(out)
end

function handlers.move_leader(seq, rest)
  local x, y = string.match(rest, "^(%-?%d+)%s+(%-?%d+)")
  x, y = tonumber(x), tonumber(y)
  local f = human_faction()
  local leader = f and try(function() return f:faction_leader() end)
  local cqi = leader and try(function() return leader:command_queue_index() end)
  local ok = false
  if cqi and x and y then ok = try(function() cm:move_character(cqi, x, y, false, true); return true end) == true end
  log({ seq = seq, cmd = "move_leader", cqi = or_null(cqi), x = or_null(x), y = or_null(y), ordered = ok, turn = turn() })
end

function handlers.end_turn(seq)
  local uic = resolve("hud_campaign|faction_buttons_docker|button_end_turn")
  local before = turn()
  local clicked = false
  if uic then clicked = try(function() uic:SimulateLClick(); return true end) == true end
  log({ seq = seq, cmd = "end_turn", button = describe(uic), clicked = clicked, turn_before = before })
end

function handlers.autoresolve(seq)


  local docker = "popup_pre_battle|mid|battle_deployment|pre_battle_deployment_panel|" ..
                 "regular_deployment|button_docker|button_parent_when_no_countdown_active"
  local paths = {
    docker .. "|button_set_siege|button_autoresolve",
    docker .. "|button_set_attack|button_autoresolve",
    docker .. "|button_set_mp|button_autoresolve",
    "popup_pre_battle|mid|battle_deployment|regular_deployment|button_set_attack|button_autoresolve",
    "button_autoresolve",
  }
  local clicked, used, rejected = false, nil, nil
  for _, p in ipairs(paths) do
    local uic = resolve(p)


    if uic then
      local ok = true

      if try(function() return uic:Visible() end) == false then
        ok = false
        rejected = p .. " (not visible)"
      end
      local x, y = xy(uic)
      local rw = try(function() return root():Width() end)
      local rh = try(function() return root():Height() end)
      if x and y and rw and rh and (x < -1 or y < -1 or x > rw or y > rh) then
        ok = false
        rejected = p .. " (rect " .. tostring(x) .. "," .. tostring(y) ..
                   " outside root " .. tostring(rw) .. "x" .. tostring(rh) .. ")"
      end
      if ok then
        used = p
        clicked = try(function() uic:SimulateLClick(); return true end) == true
        break
      end
    end
  end
  log({ seq = seq, cmd = "autoresolve", path = or_null(used), clicked = clicked,
        rejected = or_null(rejected), turn = turn() })
end


function handlers.eval(seq, rest)
  local chunk, cerr = loadstring("return " .. rest)
  if not chunk then chunk, cerr = loadstring(rest) end
  local result, rerr
  if chunk then
    local ok, v = pcall(chunk)
    if ok then result = v else rerr = tostring(v) end
  else
    rerr = tostring(cerr)
  end
  log({ seq = seq, cmd = "eval", result = or_null(result),
        rtype = or_null(type(result)), error = or_null(rerr), turn = turn() })
end


function handlers.modeval(seq, rest)
  local chunk, cerr = loadstring("return " .. rest)
  if not chunk then chunk, cerr = loadstring(rest) end
  local result, rerr
  if chunk then
    local okf = pcall(function() setfenv(chunk, getfenv(1)) end)
    local ok, v = pcall(chunk)
    if ok then result = v else rerr = tostring(v) end
    if not okf and not rerr then rerr = "setfenv failed" end
  else
    rerr = tostring(cerr)
  end
  log({ seq = seq, cmd = "modeval", result = or_null(result),
        rtype = or_null(type(result)), error = or_null(rerr),
        roots_after = root_names(), turn = turn() })
end


function handlers.roots(seq)
  local r = root()
  local out, n = {}, 0
  if r then
    n = try(function() return r:ChildCount() end) or 0
    for i = 0, n - 1 do
      local c = try(function() return UIComponent(r:Find(i)) end)
      if c then
        out[#out + 1] = {
          id = or_null(try(function() return c:Id() end)),
          visible = or_null(try(function() return c:Visible() end)),
          children = or_null(try(function() return c:ChildCount() end)),
        }
      end
    end
  end
  log({ seq = seq, cmd = "roots", count = n, kids = out, turn = turn() })
end


function handlers.children(seq, rest)
  handlers.find(seq, rest)
end


function handlers.dclick(seq, rest)
  local uic = resolve(rest)
  local info = { seq = seq, cmd = "dclick", path = rest, found = (uic ~= nil), turn = turn() }
  if uic then
    info.id = or_null(try(function() return uic:Id() end))
    info.state_before = or_null(try(function() return uic:CurrentState() end))
    info.roots_before = root_names()
    info.method = "SimulateDblLClick"
    info.clicked = try(function() uic:SimulateDblLClick(); return true end) == true
    if not info.clicked then
      info.method = "two_SimulateLClick"
      info.clicked = try(function()
        uic:SimulateLClick(); uic:SimulateLClick(); return true end) == true
    end
    info.state_after = or_null(try(function() return uic:CurrentState() end))
    info.roots_after = root_names()
    info.changed = (info.state_before ~= info.state_after)
                or (#info.roots_before ~= #info.roots_after)
  else
    info.clicked = false
  end
  log(info)
end


local function simulate(seq, cmd, path, fn, extra)
  local uic = resolve(path)
  local info = { seq = seq, cmd = cmd, path = path, found = (uic ~= nil), turn = turn() }
  if extra then for k, v in pairs(extra) do info[k] = v end end
  if uic then
    info.id = or_null(try(function() return uic:Id() end))
    info.state_before = or_null(try(function() return uic:CurrentState() end))
    info.visible_before = or_null(try(function() return uic:Visible() end))
    info.visible_from_root = or_null(try(function() return uic:VisibleFromRoot() end))
    info.roots_before = root_names()
    if info.visible_from_root == false or info.visible_before == false then
      info.sent = false
      info.refused = "target_not_visible"
    else
      info.sent = try(function() fn(uic); return true end) == true
    end
    info.state_after = or_null(try(function() return uic:CurrentState() end))
    info.visible_after = or_null(try(function() return uic:Visible() end))
    info.roots_after = root_names()
    info.changed = (info.state_before ~= info.state_after)
                or (info.visible_before ~= info.visible_after)
                or (#info.roots_before ~= #info.roots_after)
  else
    info.sent = false
  end
  log(info)
end


function handlers.rclick(seq, rest)
  simulate(seq, "rclick", rest, function(uic) uic:SimulateRClick() end)
end


function handlers.hover(seq, rest)
  simulate(seq, "hover", rest, function(uic) uic:SimulateMouseOn() end)
end


function handlers.unhover(seq, rest)
  simulate(seq, "unhover", rest, function(uic) uic:SimulateMouseOff() end)
end


function handlers.key(seq, rest)
  local path, k = string.match(rest, "^(.-)%s+(%S+)%s*$")
  if not k then path, k = "@root", rest end
  if path == "" then path = "@root" end
  k = string.lower(k)
  simulate(seq, "key", path, function(uic) uic:SimulateKey(k) end, { key = k })
end


function handlers.clickidx(seq, rest)
  local path, idx = string.match(rest, "^(.-)%s+(%d+)%s*$")
  idx = tonumber(idx)
  local parent = resolve(path or "")
  local clicked, cid, n = false, nil, nil
  if parent and idx then
    n = try(function() return parent:ChildCount() end)
    local child = try(function() return UIComponent(parent:Find(idx)) end)
    if child then
      cid = try(function() return child:Id() end)
      clicked = try(function() child:SimulateLClick(); return true end) == true
    end
  end
  log({ seq = seq, cmd = "clickidx", path = or_null(path), idx = or_null(idx),
        parent_children = or_null(n), child_id = or_null(cid),
        found = (parent ~= nil), clicked = clicked, turn = turn() })
end


local TRAIT_KEYS = {
  "wh2_dlc09_dummy_trait_dynasty_1", "wh2_dlc09_dummy_trait_dynasty_2", "wh2_dlc09_dummy_trait_dynasty_3", "wh2_dlc09_dummy_trait_dynasty_4",
  "wh2_dlc09_dummy_trait_dynasty_5", "wh2_dlc09_dummy_trait_dynasty_6", "wh2_dlc09_trait_benevolence", "wh2_dlc09_trait_defeated_arkhan",
  "wh2_dlc09_trait_defeated_khalida", "wh2_dlc09_trait_defeated_khatep", "wh2_dlc09_trait_defeated_settra", "wh2_dlc09_trait_defeated_settra_as_surtha",
  "wh2_dlc09_trait_defeated_surtha_as_settra", "wh2_dlc09_trait_defeated_tretch", "wh2_dlc09_trait_merciless", "wh2_dlc09_trait_patience",
  "wh2_dlc09_trait_settra_title", "wh2_dlc09_trait_settra_title_start", "wh2_dlc09_trait_spymaster", "wh2_dlc10_trait_alarielle_chaos",
  "wh2_dlc10_trait_alarielle_chaos_none", "wh2_dlc10_trait_defeated_alarielle", "wh2_dlc10_trait_defeated_alith_anar", "wh2_dlc10_trait_defeated_hellebron",
  "wh2_dlc10_trait_sword_of_khaine", "wh2_dlc11_admiral_01_trait", "wh2_dlc11_admiral_02_trait", "wh2_dlc11_admiral_03_trait",
  "wh2_dlc11_admiral_04_trait", "wh2_dlc11_trait_defeated_aranessa_saltspite", "wh2_dlc11_trait_defeated_count_noctilus", "wh2_dlc11_trait_defeated_cylostra_direfin",
  "wh2_dlc11_trait_defeated_lokhir_fellheart", "wh2_dlc11_trait_defeated_luthor_harkon", "wh2_dlc11_trait_harkon_personality_coward", "wh2_dlc11_trait_harkon_personality_hateful",
  "wh2_dlc11_trait_harkon_personality_mad", "wh2_dlc11_trait_harkon_personality_prideful", "wh2_dlc11_trait_harkon_personality_restored", "wh2_dlc11_trait_incentive",
  "wh2_dlc11_trait_incentive_counter", "wh2_dlc11_trait_legend", "wh2_dlc11_trait_lokhir_black_ark_dummy", "wh2_dlc12_trait_defeated_ikit_claw",
  "wh2_dlc12_trait_defeated_tehenhauin", "wh2_dlc12_trait_defeated_tiktaqto", "wh2_dlc13_trait_defeated_gorrok", "wh2_dlc13_trait_defeated_nakai",
  "wh2_dlc13_trait_defeated_wulfhart", "wh2_dlc13_trait_hertwig_focused", "wh2_dlc13_trait_hertwig_legendary_physician", "wh2_dlc13_trait_hertwig_van_hal_bloodline",
  "wh2_dlc13_trait_jorek_creator", "wh2_dlc13_trait_jorek_magnificent_engineer", "wh2_dlc13_trait_kalara_fervent", "wh2_dlc13_trait_kalara_ishas_blessing",
  "wh2_dlc13_trait_kalara_pursuer", "wh2_dlc13_trait_rodrik_executed_captive", "wh2_dlc13_trait_rodrik_merciful", "wh2_dlc13_trait_rodrik_sworn_to_wulfhart",
  "wh2_dlc13_trait_rodrik_targeted_by_duke_tudual", "wh2_dlc13_trait_rodrik_wolf_hearted", "wh2_dlc14_trait_defeated_gotrek", "wh2_dlc14_trait_defeated_malus",
  "wh2_dlc14_trait_defeated_repanse", "wh2_dlc14_trait_defeated_snikch", "wh2_dlc15_grom_food_collector", "wh2_dlc15_trait_defeated_eltharion",
  "wh2_dlc15_trait_defeated_grom", "wh2_dlc15_trait_defeated_imrik", "wh2_dlc15_trait_dragon_black_imrik", "wh2_dlc15_trait_dragon_forest_imrik",
  "wh2_dlc15_trait_dragon_moon_imrik", "wh2_dlc15_trait_dragon_star_imrik", "wh2_dlc15_trait_dragon_sun_imrik", "wh2_dlc15_trait_mistwalker_sentinel",
  "wh2_dlc15_trait_mistwalker_shadow", "wh2_dlc15_trait_mistwalker_watcher", "wh2_dlc15_trait_white_wolf", "wh2_dlc16_trait_drycha_potion_sacre",
  "wh2_dlc16_trait_sisters_mount_summons", "wh2_dlc17_trait_defeated_oxyotl", "wh2_dlc17_trait_defeated_taurox", "wh2_dlc17_trait_defeated_thorek",
  "wh2_dlc17_trait_kazador_thunderhorn", "wh2_dlc17_trait_kevin_lloyd", "wh2_dlc17_trait_taurox_hidden_rampage_complete", "wh2_dlc17_trait_taurox_hidden_rampage_high_momentum",
  "wh2_dlc17_trait_taurox_hidden_rampage_multiple_wins_same_turn", "wh2_dlc17_trait_taurox_hidden_rampage_solo_army", "wh2_dlc17_trait_unique_expedient_engineer", "wh2_dlc17_trait_unique_loracle",
  "wh2_dlc17_trait_unique_rider_of_typo", "wh2_main_trait_agent_action_assassinate", "wh2_main_trait_agent_action_assault_garrison", "wh2_main_trait_agent_action_assault_unit",
  "wh2_main_trait_agent_action_assault_units", "wh2_main_trait_agent_action_block_army", "wh2_main_trait_agent_action_damage_building", "wh2_main_trait_agent_action_damage_walls",
  "wh2_main_trait_agent_action_hinder_replenishment", "wh2_main_trait_agent_action_steal_technology", "wh2_main_trait_agent_action_wound", "wh2_main_trait_agent_actions_against_beastmen",
  "wh2_main_trait_agent_actions_against_cathay", "wh2_main_trait_agent_actions_against_chaos", "wh2_main_trait_agent_actions_against_chaos_dwarfs", "wh2_main_trait_agent_actions_against_daemons",
  "wh2_main_trait_agent_actions_against_dark_elves", "wh2_main_trait_agent_actions_against_dwarfs", "wh2_main_trait_agent_actions_against_greenskins", "wh2_main_trait_agent_actions_against_high_elves",
  "wh2_main_trait_agent_actions_against_humans", "wh2_main_trait_agent_actions_against_khorne", "wh2_main_trait_agent_actions_against_kislev", "wh2_main_trait_agent_actions_against_lizardmen",
  "wh2_main_trait_agent_actions_against_nurgle", "wh2_main_trait_agent_actions_against_ogre_kingdoms", "wh2_main_trait_agent_actions_against_skaven", "wh2_main_trait_agent_actions_against_slaanesh",
  "wh2_main_trait_agent_actions_against_tomb_kings", "wh2_main_trait_agent_actions_against_tzeentch", "wh2_main_trait_agent_actions_against_vampire_coast", "wh2_main_trait_agent_actions_against_vampires",
  "wh2_main_trait_agent_actions_against_wood_elves", "wh2_main_trait_agent_target_fail", "wh2_main_trait_agent_target_success", "wh2_main_trait_attacking_defeat",
  "wh2_main_trait_attacking_victory", "wh2_main_trait_black_ark_building_wh2_main_horde_def_beasts_1", "wh2_main_trait_black_ark_building_wh2_main_horde_def_beasts_2", "wh2_main_trait_black_ark_building_wh2_main_horde_def_beasts_3",
  "wh2_main_trait_black_ark_building_wh2_main_horde_def_bombardment_a_1", "wh2_main_trait_black_ark_building_wh2_main_horde_def_bombardment_a_2", "wh2_main_trait_black_ark_building_wh2_main_horde_def_bombardment_b_1", "wh2_main_trait_black_ark_building_wh2_main_horde_def_bombardment_b_2",
  "wh2_main_trait_black_ark_building_wh2_main_horde_def_bombardment_c_1", "wh2_main_trait_black_ark_building_wh2_main_horde_def_bombardment_c_2", "wh2_main_trait_black_ark_building_wh2_main_horde_def_entertainment_1", "wh2_main_trait_black_ark_building_wh2_main_horde_def_entertainment_2",
  "wh2_main_trait_black_ark_building_wh2_main_horde_def_entertainment_3", "wh2_main_trait_black_ark_building_wh2_main_horde_def_military_1", "wh2_main_trait_black_ark_building_wh2_main_horde_def_military_2", "wh2_main_trait_black_ark_building_wh2_main_horde_def_military_3",
  "wh2_main_trait_black_ark_building_wh2_main_horde_def_settlement_2", "wh2_main_trait_black_ark_building_wh2_main_horde_def_settlement_3", "wh2_main_trait_black_ark_building_wh2_main_horde_def_settlement_4", "wh2_main_trait_black_ark_building_wh2_main_horde_def_settlement_5",
  "wh2_main_trait_black_ark_building_wh2_main_horde_def_slavery_1", "wh2_main_trait_black_ark_building_wh2_main_horde_def_slavery_2", "wh2_main_trait_black_ark_building_wh2_main_horde_def_slavery_3", "wh2_main_trait_black_ark_building_wh2_main_horde_def_sorcery_1",
  "wh2_main_trait_black_ark_building_wh2_main_horde_def_sorcery_2", "wh2_main_trait_black_ark_building_wh2_main_horde_def_worship_1", "wh2_main_trait_black_ark_building_wh2_main_horde_def_worship_2", "wh2_main_trait_brutally_honest",
  "wh2_main_trait_builder", "wh2_main_trait_casualties", "wh2_main_trait_corrupted_chaos", "wh2_main_trait_corrupted_skaven",
  "wh2_main_trait_corrupted_vampire", "wh2_main_trait_def_assassins_end", "wh2_main_trait_def_favoured", "wh2_main_trait_def_name_of_power_ar_01_lifequencher",
  "wh2_main_trait_def_name_of_power_ar_02_the_tempest_of_talons", "wh2_main_trait_def_name_of_power_ar_03_shadowdart", "wh2_main_trait_def_name_of_power_ar_04_barbstorm", "wh2_main_trait_def_name_of_power_ar_05_beastbinder",
  "wh2_main_trait_def_name_of_power_ar_06_fangshield", "wh2_main_trait_def_name_of_power_ar_07_wrathbringer", "wh2_main_trait_def_name_of_power_ar_08_moonshadow", "wh2_main_trait_def_name_of_power_ar_09_granitestance",
  "wh2_main_trait_def_name_of_power_ar_10_the_grey_vanquisher", "wh2_main_trait_def_name_of_power_ar_11_krakenclaw", "wh2_main_trait_def_name_of_power_ar_12_grimgaze", "wh2_main_trait_def_name_of_power_ca_01_dreadtongue",
  "wh2_main_trait_def_name_of_power_ca_02_darkpath", "wh2_main_trait_def_name_of_power_ca_03_khainemarked", "wh2_main_trait_def_name_of_power_ca_04_the_black_conqueror", "wh2_main_trait_def_name_of_power_ca_05_leviathanrage",
  "wh2_main_trait_def_name_of_power_ca_06_emeraldeye", "wh2_main_trait_def_name_of_power_ca_07_barbedlash", "wh2_main_trait_def_name_of_power_ca_08_pathguard", "wh2_main_trait_def_name_of_power_ca_09_the_dark_marshall",
  "wh2_main_trait_def_name_of_power_ca_10_the_dire_overseer", "wh2_main_trait_def_name_of_power_ca_11_gatesmiter", "wh2_main_trait_def_name_of_power_ca_12_the_tormentor", "wh2_main_trait_def_name_of_power_co_01_blackstone",
  "wh2_main_trait_def_name_of_power_co_02_wyrmscale", "wh2_main_trait_def_name_of_power_co_03_poisonblade", "wh2_main_trait_def_name_of_power_co_04_headreaper", "wh2_main_trait_def_name_of_power_co_05_spiteheart",
  "wh2_main_trait_def_name_of_power_co_06_soulblaze", "wh2_main_trait_def_name_of_power_co_07_bloodscourge", "wh2_main_trait_def_name_of_power_co_08_griefbringer", "wh2_main_trait_def_name_of_power_co_09_the_hand_of_wrath",
  "wh2_main_trait_def_name_of_power_co_10_fatedshield", "wh2_main_trait_def_name_of_power_co_11_drakecleaver", "wh2_main_trait_def_name_of_power_co_12_hydrablood", "wh2_main_trait_defeated_alberic_de_bordeleaux",
  "wh2_main_trait_defeated_archaon_the_everchosen", "wh2_main_trait_defeated_azhag_the_slaughterer", "wh2_main_trait_defeated_balthasar_gelt", "wh2_main_trait_defeated_belegar_ironhammer",
  "wh2_main_trait_defeated_drycha", "wh2_main_trait_defeated_durthu", "wh2_main_trait_defeated_fay_enchantress", "wh2_main_trait_defeated_grimgor_ironhide",
  "wh2_main_trait_defeated_grombrindal", "wh2_main_trait_defeated_heinrich_kemmler", "wh2_main_trait_defeated_helmen_ghorst", "wh2_main_trait_defeated_isabella_von_carstein",
  "wh2_main_trait_defeated_karl_franz", "wh2_main_trait_defeated_khazrak_one_eye", "wh2_main_trait_defeated_kholek_suneater", "wh2_main_trait_defeated_kroq_gar",
  "wh2_main_trait_defeated_lord_mazdamundi", "wh2_main_trait_defeated_lord_skrolk", "wh2_main_trait_defeated_lord_strolk", "wh2_main_trait_defeated_louen_leoncouer",
  "wh2_main_trait_defeated_malagor_the_dark_omen", "wh2_main_trait_defeated_malekith", "wh2_main_trait_defeated_mannfred_von_carstein", "wh2_main_trait_defeated_morathi",
  "wh2_main_trait_defeated_morghur_the_shadowgave", "wh2_main_trait_defeated_orion", "wh2_main_trait_defeated_prince_sigvald", "wh2_main_trait_defeated_queen_headtaker",
  "wh2_main_trait_defeated_sisters_of_twilight", "wh2_main_trait_defeated_skarsnik", "wh2_main_trait_defeated_teclis", "wh2_main_trait_defeated_thorgrim_grudgebearer",
  "wh2_main_trait_defeated_throt", "wh2_main_trait_defeated_tyrion", "wh2_main_trait_defeated_ungrim_ironfist", "wh2_main_trait_defeated_vlad_von_carstein",
  "wh2_main_trait_defeated_volkmar_the_grim", "wh2_main_trait_defeated_wurzzag", "wh2_main_trait_defeats", "wh2_main_trait_defeats_against_beastmen",
  "wh2_main_trait_defeats_against_cathay", "wh2_main_trait_defeats_against_chaos", "wh2_main_trait_defeats_against_chaos_dwarfs", "wh2_main_trait_defeats_against_daemons",
  "wh2_main_trait_defeats_against_dark_elves", "wh2_main_trait_defeats_against_dwarfs", "wh2_main_trait_defeats_against_greenskins", "wh2_main_trait_defeats_against_high_elves",
  "wh2_main_trait_defeats_against_humans", "wh2_main_trait_defeats_against_khorne", "wh2_main_trait_defeats_against_kislev", "wh2_main_trait_defeats_against_lizardmen",
  "wh2_main_trait_defeats_against_nurgle", "wh2_main_trait_defeats_against_ogre_kingdoms", "wh2_main_trait_defeats_against_skaven", "wh2_main_trait_defeats_against_slaanesh",
  "wh2_main_trait_defeats_against_tomb_kings", "wh2_main_trait_defeats_against_tzeentch", "wh2_main_trait_defeats_against_vampire_coast", "wh2_main_trait_defeats_against_vampires",
  "wh2_main_trait_defeats_against_wood_elves", "wh2_main_trait_defeats_at_sea", "wh2_main_trait_defending_defeat", "wh2_main_trait_defending_victory",
  "wh2_main_trait_exploring", "wh2_main_trait_far_from_capital", "wh2_main_trait_fighter", "wh2_main_trait_general_status_all_cursed",
  "wh2_main_trait_innate_dummy_oglok", "wh2_main_trait_innate_dummy_raknik", "wh2_main_trait_lazy", "wh2_main_trait_lone_wolf",
  "wh2_main_trait_non_corrupted", "wh2_main_trait_pacifist", "wh2_main_trait_post_battle_execute", "wh2_main_trait_post_battle_ransom",
  "wh2_main_trait_public_order", "wh2_main_trait_razing", "wh2_main_trait_reinforcing", "wh2_main_trait_reinforcing_beastmen",
  "wh2_main_trait_reinforcing_cathay", "wh2_main_trait_reinforcing_chaos", "wh2_main_trait_reinforcing_chaos_dwarfs", "wh2_main_trait_reinforcing_daemons",
  "wh2_main_trait_reinforcing_dark_elves", "wh2_main_trait_reinforcing_dwarfs", "wh2_main_trait_reinforcing_greenskins", "wh2_main_trait_reinforcing_high_elves",
  "wh2_main_trait_reinforcing_humans", "wh2_main_trait_reinforcing_khorne", "wh2_main_trait_reinforcing_kislev", "wh2_main_trait_reinforcing_lizardmen",
  "wh2_main_trait_reinforcing_nurgle", "wh2_main_trait_reinforcing_ogre_kingdoms", "wh2_main_trait_reinforcing_skaven", "wh2_main_trait_reinforcing_slaanesh",
  "wh2_main_trait_reinforcing_tomb_kings", "wh2_main_trait_reinforcing_tzeentch", "wh2_main_trait_reinforcing_vampire_coast", "wh2_main_trait_reinforcing_vampires",
  "wh2_main_trait_reinforcing_wood_elves", "wh2_main_trait_routed", "wh2_main_trait_ruler_personality_kroq_gar_ancient_spawn", "wh2_main_trait_ruler_personality_malekith_witch_king",
  "wh2_main_trait_ruler_personality_morathi_dark_manipulator", "wh2_main_trait_ruler_personality_tiranoc_chariots", "wh2_main_trait_ruler_personality_tyrion_no_politician", "wh2_main_trait_sacking",
  "wh2_main_trait_sea_legs", "wh2_main_trait_siege_defeat", "wh2_main_trait_siege_victory", "wh2_main_trait_skv_badly_mutated",
  "wh2_main_trait_skv_boom_master", "wh2_main_trait_skv_flayed_tail", "wh2_main_trait_skv_musk_of_the_overlord", "wh2_main_trait_skv_one_eye",
  "wh2_main_trait_slaver", "wh2_main_trait_stance_ambushing", "wh2_main_trait_stance_astromancy", "wh2_main_trait_stance_channeling",
  "wh2_main_trait_stance_forced_march", "wh2_main_trait_stance_raiding", "wh2_main_trait_stance_recruiting", "wh2_main_trait_stance_stalking",
  "wh2_main_trait_stance_tunneling", "wh2_main_trait_wins", "wh2_main_trait_wins_against_beastmen", "wh2_main_trait_wins_against_cathay",
  "wh2_main_trait_wins_against_chaos", "wh2_main_trait_wins_against_chaos_dwarfs", "wh2_main_trait_wins_against_daemons", "wh2_main_trait_wins_against_dark_elves",
  "wh2_main_trait_wins_against_dwarfs", "wh2_main_trait_wins_against_greenskins", "wh2_main_trait_wins_against_high_elves", "wh2_main_trait_wins_against_humans",
  "wh2_main_trait_wins_against_khorne", "wh2_main_trait_wins_against_kislev", "wh2_main_trait_wins_against_lizardmen", "wh2_main_trait_wins_against_nurgle",
  "wh2_main_trait_wins_against_ogre_kingdoms", "wh2_main_trait_wins_against_rogue_armies", "wh2_main_trait_wins_against_skaven", "wh2_main_trait_wins_against_slaanesh",
  "wh2_main_trait_wins_against_tomb_kings", "wh2_main_trait_wins_against_tzeentch", "wh2_main_trait_wins_against_vampire_coast", "wh2_main_trait_wins_against_vampires",
  "wh2_main_trait_wins_against_wood_elves", "wh2_main_trait_wins_at_sea", "wh2_main_trait_wounded", "wh2_pro08_trait_felix",
  "wh2_pro08_trait_gotrek", "wh2_twa03_trait_defeated_rakarth", "wh3_cp1_trait_defeated_bhashiva", "wh3_cp1_trait_defeated_kugath_plague_good",
  "wh3_dlc20_legacy_trait_exalted_hero_to_marked", "wh3_dlc20_legacy_trait_lord_to_daemon_prince", "wh3_dlc20_legacy_trait_lord_undivided_to_marked", "wh3_dlc20_legacy_trait_sorcerer_death",
  "wh3_dlc20_legacy_trait_sorcerer_fire", "wh3_dlc20_legacy_trait_sorcerer_lord_death_nur_to_daemon_prince", "wh3_dlc20_legacy_trait_sorcerer_lord_death_to_death_mnur", "wh3_dlc20_legacy_trait_sorcerer_lord_fire_to_daemon_prince",
  "wh3_dlc20_legacy_trait_sorcerer_lord_metal_tze_to_daemon_prince", "wh3_dlc20_legacy_trait_sorcerer_lord_nurgle_nur_to_daemon_prince", "wh3_dlc20_legacy_trait_sorcerer_lord_tzeentch_tze_to_daemon_prince", "wh3_dlc20_legacy_trait_sorcerer_metal",
  "wh3_dlc20_legacy_trait_sorcerer_metal_to_metal_mtze", "wh3_dlc20_legacy_trait_sorcerer_shadows", "wh3_dlc20_legacy_trait_sorcerer_shadows_to_shadows_msla", "wh3_dlc20_trait_champion_of_zanbaijin_azazel",
  "wh3_dlc20_trait_champion_of_zanbaijin_festus", "wh3_dlc20_trait_champion_of_zanbaijin_valkia", "wh3_dlc20_trait_champion_of_zanbaijin_vilitch", "wh3_dlc20_trait_defeated_azazel",
  "wh3_dlc20_trait_defeated_festus", "wh3_dlc20_trait_defeated_valkia", "wh3_dlc20_trait_defeated_vilitch", "wh3_dlc20_unique_trait_kormak",
  "wh3_dlc20_unique_trait_ograx", "wh3_dlc23_trait_defeated_astragoth", "wh3_dlc23_trait_defeated_drazhoath", "wh3_dlc23_trait_defeated_zhatan",
  "wh3_dlc23_trait_sarthorael_the_everwatcher", "wh3_dlc24_ritual_cth_mos_stone_settlement_generate_elite_astromancer", "wh3_dlc24_trait_defeated_mother_ostankya", "wh3_dlc24_trait_defeated_the_changeling",
  "wh3_dlc24_trait_defeated_yuan_bo", "wh3_dlc25_trait_chieftain_bray_shaman", "wh3_dlc25_trait_chieftain_bray_shaman_antitrait", "wh3_dlc25_trait_chieftain_castellan",
  "wh3_dlc25_trait_chieftain_castellan_antitrait", "wh3_dlc25_trait_chieftain_exalted_hero", "wh3_dlc25_trait_chieftain_exalted_hero_antitrait", "wh3_dlc25_trait_chieftain_fimir_balefiend",
  "wh3_dlc25_trait_chieftain_fimir_balefiend_antitrait", "wh3_dlc25_trait_chieftain_kazyk", "wh3_dlc25_trait_chieftain_kazyk_antitrait", "wh3_dlc25_trait_chieftain_tamurkhan",
  "wh3_dlc25_trait_chieftain_tamurkhan_antitrait", "wh3_dlc25_trait_chieftain_werekin", "wh3_dlc25_trait_chieftain_werekin_antitrait", "wh3_dlc25_trait_defeated_elspeth",
  "wh3_dlc25_trait_defeated_epidemius", "wh3_dlc25_trait_defeated_malakai", "wh3_dlc25_trait_defeated_tamurkhan", "wh3_dlc26_trait_arbaal_lost_battle",
  "wh3_dlc26_trait_defeated_arbaal", "wh3_dlc26_trait_defeated_golgfag", "wh3_dlc26_trait_defeated_gorbad", "wh3_dlc26_trait_defeated_skulltaker",
  "wh3_dlc27_legacy_trait_sorcerer_lord_shadows_sla_to_daemon_prince", "wh3_dlc27_legacy_trait_sorcerer_lord_shadows_to_shadows_msla", "wh3_dlc27_legacy_trait_sorcerer_lord_slaanesh_sla_to_daemon_prince", "wh3_dlc27_nor_monster_hunts_wounded",
  "wh3_dlc27_trait_defeated_aislinn", "wh3_dlc27_trait_defeated_dechala", "wh3_dlc27_trait_defeated_sayl", "wh3_dlc27_trait_defeated_the_masque",
  "wh3_dlc27_trait_hef_aislinn_destroyed_black_arks", "wh3_dlc27_trait_hef_white_tower_summon_mage_beasts_1", "wh3_dlc27_trait_hef_white_tower_summon_mage_beasts_2", "wh3_dlc27_trait_hef_white_tower_summon_mage_death_1",
  "wh3_dlc27_trait_hef_white_tower_summon_mage_death_2", "wh3_dlc27_trait_hef_white_tower_summon_mage_fire_1", "wh3_dlc27_trait_hef_white_tower_summon_mage_fire_2", "wh3_dlc27_trait_hef_white_tower_summon_mage_heavens_1",
  "wh3_dlc27_trait_hef_white_tower_summon_mage_heavens_2", "wh3_dlc27_trait_hef_white_tower_summon_mage_high_1", "wh3_dlc27_trait_hef_white_tower_summon_mage_high_2", "wh3_dlc27_trait_hef_white_tower_summon_mage_life_1",
  "wh3_dlc27_trait_hef_white_tower_summon_mage_life_2", "wh3_dlc27_trait_hef_white_tower_summon_mage_light_1", "wh3_dlc27_trait_hef_white_tower_summon_mage_light_2", "wh3_dlc27_trait_hef_white_tower_summon_mage_metal_1",
  "wh3_dlc27_trait_hef_white_tower_summon_mage_metal_2", "wh3_dlc27_trait_hef_white_tower_summon_mage_shadows_1", "wh3_dlc27_trait_hef_white_tower_summon_mage_shadows_2", "wh3_dlc27_trait_sayl_final",
  "wh3_main_prologue_chosen", "wh3_main_prologue_devious", "wh3_main_prologue_egotist", "wh3_main_prologue_honest",
  "wh3_main_prologue_saviour", "wh3_main_prologue_trait_post_battle_execute", "wh3_main_prologue_vengeful", "wh3_main_prologue_warlord",
  "wh3_main_trait_ataman_a_boon_for_all", "wh3_main_trait_ataman_banisher", "wh3_main_trait_ataman_bolsterer", "wh3_main_trait_ataman_defender",
  "wh3_main_trait_ataman_drawer_of_blood", "wh3_main_trait_ataman_drillmaster", "wh3_main_trait_ataman_favours_cavalry", "wh3_main_trait_ataman_favours_infantry",
  "wh3_main_trait_ataman_fiscally_prudent", "wh3_main_trait_ataman_frost_maiden_ally", "wh3_main_trait_ataman_good_host", "wh3_main_trait_ataman_horse_comrade",
  "wh3_main_trait_ataman_ice_court_informer", "wh3_main_trait_ataman_investor", "wh3_main_trait_ataman_master_builder", "wh3_main_trait_ataman_observant",
  "wh3_main_trait_ataman_orthodoxy_loyalist", "wh3_main_trait_ataman_pioneer", "wh3_main_trait_ataman_pragmatic_defender", "wh3_main_trait_ataman_province_first",
  "wh3_main_trait_ataman_purger", "wh3_main_trait_ataman_recruiter", "wh3_main_trait_ataman_reverent_patriarch", "wh3_main_trait_ataman_reward_the_one",
  "wh3_main_trait_ataman_seeker_of_foes", "wh3_main_trait_ataman_traditionalist", "wh3_main_trait_ataman_training", "wh3_main_trait_belakor_spells_hidden",
  "wh3_main_trait_blessed_by_ind_blades", "wh3_main_trait_blessed_by_ind_riches", "wh3_main_trait_bst_unique_kharak_stoneheart", "wh3_main_trait_caravan_daemon_hunter",
  "wh3_main_trait_carnival_of_chaos", "wh3_main_trait_corrupted_khorne", "wh3_main_trait_corrupted_nurgle", "wh3_main_trait_corrupted_slaanesh",
  "wh3_main_trait_corrupted_tzeentch", "wh3_main_trait_defeated_belakor", "wh3_main_trait_defeated_boris", "wh3_main_trait_defeated_daemon_prince",
  "wh3_main_trait_defeated_greasus_goldtooth", "wh3_main_trait_defeated_kairos", "wh3_main_trait_defeated_katarin", "wh3_main_trait_defeated_kostaltyn",
  "wh3_main_trait_defeated_kugath", "wh3_main_trait_defeated_miao_ying", "wh3_main_trait_defeated_nkari", "wh3_main_trait_defeated_skarbrand",
  "wh3_main_trait_defeated_skrag_the_slaughterer", "wh3_main_trait_defeated_zhao_ming", "wh3_main_trait_dilemma_cth_selfish_leader", "wh3_main_trait_dilemma_cth_selfless_leader",
  "wh3_main_trait_dilemma_dae_ruinous_infusion", "wh3_main_trait_dilemma_dae_the_princes_mark", "wh3_main_trait_dilemma_kho_brutalised_visage", "wh3_main_trait_dilemma_kho_ironhide",
  "wh3_main_trait_dilemma_nur_grandfathers_embrace", "wh3_main_trait_dilemma_nur_living_contagion", "wh3_main_trait_dilemma_ogr_full_oguts", "wh3_main_trait_dilemma_ogr_stomach_reduction_surgery",
  "wh3_main_trait_dilemma_ogr_tapestry_of_wounds", "wh3_main_trait_dilemma_sla_avatar_of_agony", "wh3_main_trait_dilemma_sla_intoxicating_presence", "wh3_main_trait_dilemma_tze_a_thousand_fingers",
  "wh3_main_trait_dilemma_tze_whispering_skull", "wh3_main_trait_dummy_caravan_con_artist", "wh3_main_trait_dummy_caravan_ex_bandit", "wh3_main_trait_dummy_caravan_gambler",
  "wh3_main_trait_dummy_caravan_herbalist", "wh3_main_trait_dummy_caravan_hunter", "wh3_main_trait_dummy_caravan_lingual", "wh3_main_trait_dummy_caravan_navigator",
  "wh3_main_trait_dummy_caravan_obscurer", "wh3_main_trait_dummy_caravan_punctual", "wh3_main_trait_dummy_caravan_risk", "wh3_main_trait_dummy_caravan_soldier",
  "wh3_main_trait_dummy_caravan_starter", "wh3_main_trait_dummy_caravan_weather", "wh3_main_trait_hef_unique_mellindirei", "wh3_main_trait_ice_court_agent_of_brutality",
  "wh3_main_trait_ice_court_artillerist", "wh3_main_trait_ice_court_artillerist_hero", "wh3_main_trait_ice_court_battler", "wh3_main_trait_ice_court_better_bows_for_bodyguards",
  "wh3_main_trait_ice_court_better_bows_for_bodyguards_hero", "wh3_main_trait_ice_court_better_swords_for_shielders", "wh3_main_trait_ice_court_better_swords_for_shielders_hero", "wh3_main_trait_ice_court_blessed_are_the_footsoldiers",
  "wh3_main_trait_ice_court_blessed_cavalry", "wh3_main_trait_ice_court_builder", "wh3_main_trait_ice_court_campaigner", "wh3_main_trait_ice_court_cavalry_focused",
  "wh3_main_trait_ice_court_cavalry_focused_hero", "wh3_main_trait_ice_court_clerical_conspirator", "wh3_main_trait_ice_court_court_controller", "wh3_main_trait_ice_court_economist",
  "wh3_main_trait_ice_court_eldritch_defender", "wh3_main_trait_ice_court_fighter", "wh3_main_trait_ice_court_glacial_blaster", "wh3_main_trait_ice_court_growth_is_great",
  "wh3_main_trait_ice_court_ice_charioteer", "wh3_main_trait_ice_court_ice_charioteer_hero", "wh3_main_trait_ice_court_ice_shard_wielder", "wh3_main_trait_ice_court_infantry_focused",
  "wh3_main_trait_ice_court_infantry_focused_hero", "wh3_main_trait_ice_court_magical_warrior", "wh3_main_trait_ice_court_magical_warrior_hero", "wh3_main_trait_ice_court_martial_encouragement",
  "wh3_main_trait_ice_court_martial_encouragement_hero", "wh3_main_trait_ice_court_martial_magician", "wh3_main_trait_ice_court_ownership_is_theft", "wh3_main_trait_ice_court_perfect_vigour",
  "wh3_main_trait_ice_court_province_protector", "wh3_main_trait_ice_court_quiet_warrior", "wh3_main_trait_ice_court_raider", "wh3_main_trait_ice_court_ranger",
  "wh3_main_trait_ice_court_ranger_hero", "wh3_main_trait_ice_court_sacker", "wh3_main_trait_ice_court_scourge_of_the_corrupt", "wh3_main_trait_ice_court_stalker",
  "wh3_main_trait_ice_court_unbreakable", "wh3_main_trait_ice_court_vanguard_deployer", "wh3_main_trait_ice_court_wounder", "wh3_main_trait_ice_training_architect",
  "wh3_main_trait_ice_training_architect_hero", "wh3_main_trait_ice_training_court_agent", "wh3_main_trait_ice_training_court_agent_hero", "wh3_main_trait_ice_training_economist",
  "wh3_main_trait_ice_training_economist_hero", "wh3_main_trait_ice_training_ice_guard_captain", "wh3_main_trait_ice_training_ice_guard_captain_hero", "wh3_main_trait_ice_training_infantry_commander",
  "wh3_main_trait_ice_training_infantry_commander_hero", "wh3_main_trait_ice_training_logistician", "wh3_main_trait_ice_training_logistician_hero", "wh3_main_trait_ice_training_magic_1",
  "wh3_main_trait_ice_training_magic_1_hero", "wh3_main_trait_ice_training_magic_2", "wh3_main_trait_ice_training_magic_2_hero", "wh3_main_trait_ice_training_protector",
  "wh3_main_trait_ice_training_protector_hero", "wh3_main_trait_ice_training_quartermaster", "wh3_main_trait_ice_training_quartermaster_hero", "wh3_main_trait_ice_training_unbreakable",
  "wh3_main_trait_ice_training_unbreakable_hero", "wh3_main_trait_ice_training_vanguard", "wh3_main_trait_ice_training_vanguard_hero", "wh3_main_trait_realm_khorne",
  "wh3_main_trait_realm_khorne_daemons", "wh3_main_trait_realm_nurgle", "wh3_main_trait_realm_nurgle_daemons", "wh3_main_trait_realm_slaanesh",
  "wh3_main_trait_realm_slaanesh_daemons", "wh3_main_trait_realm_tzeentch", "wh3_main_trait_realm_tzeentch_daemons", "wh3_main_trait_sla_left_slaanesh_fifth",
  "wh3_main_trait_sla_left_slaanesh_first", "wh3_main_trait_sla_left_slaanesh_fourth", "wh3_main_trait_sla_left_slaanesh_second", "wh3_main_trait_sla_left_slaanesh_sixth",
  "wh3_main_trait_sla_left_slaanesh_third", "wh3_main_trait_slann_second_generation_mazdamundi", "wh3_main_trait_tmb_unique_itzi_bitzi", "wh3_main_trait_tmb_unique_nebbetthar",
  "wh3_main_trait_unique_samual_ludenhof", "wh3_trait_assassins_training", "wh3_trait_blessing_of_the_lady", "wh3_trait_draesca",
  "wh3_trait_dreamwine", "wh3_trait_sister_swords", "wh_dlc03_trait_name_dummy_the_dark_omen", "wh_dlc03_trait_name_dummy_the_one_eye",
  "wh_dlc03_trait_name_dummy_todbringer", "wh_dlc04_trait_name_dummy_ghorst", "wh_dlc04_trait_name_dummy_the_grim", "wh_dlc05_trait_name_dummy_morghur",
  "wh_dlc06_clan_angrund_ancestor_master_engineer", "wh_dlc06_clan_angrund_ancestor_runesmith", "wh_dlc06_clan_angrund_ancestor_thane", "wh_dlc06_clan_angrund_ancestor_thane_other",
  "wh_dlc06_trait_name_dummy_da_great_green_prophet", "wh_dlc06_trait_name_dummy_ironhammer", "wh_dlc06_wurrzag_anti_trait", "wh_dlc07_trait_brt_grail_vow_destroy_pledge",
  "wh_dlc07_trait_brt_grail_vow_destroy_pledge_agent", "wh_dlc07_trait_brt_grail_vow_untaint_pledge", "wh_dlc07_trait_brt_grail_vow_untaint_pledge_agent", "wh_dlc07_trait_brt_grail_vow_valour_pledge",
  "wh_dlc07_trait_brt_grail_vow_valour_pledge_agent", "wh_dlc07_trait_brt_knights_vow_chivalry_pledge", "wh_dlc07_trait_brt_knights_vow_chivalry_pledge_agent", "wh_dlc07_trait_brt_knights_vow_knowledge_pledge",
  "wh_dlc07_trait_brt_knights_vow_knowledge_pledge_agent", "wh_dlc07_trait_brt_knights_vow_order_pledge", "wh_dlc07_trait_brt_knights_vow_order_pledge_agent", "wh_dlc07_trait_brt_lord_bad_abducter",
  "wh_dlc07_trait_brt_lord_bad_attacker", "wh_dlc07_trait_brt_lord_bad_coward", "wh_dlc07_trait_brt_lord_bad_defeat", "wh_dlc07_trait_brt_lord_bad_defender",
  "wh_dlc07_trait_brt_lord_bad_kingslayer", "wh_dlc07_trait_brt_lord_bad_lazy", "wh_dlc07_trait_brt_lord_bad_perverted", "wh_dlc07_trait_brt_lord_bad_raider",
  "wh_dlc07_trait_brt_lord_bad_renegade", "wh_dlc07_trait_brt_lord_bad_sacking", "wh_dlc07_trait_brt_lord_bad_scared_of_beastmen", "wh_dlc07_trait_brt_lord_bad_scared_of_chaos",
  "wh_dlc07_trait_brt_lord_bad_scared_of_greenskins", "wh_dlc07_trait_brt_lord_bad_scared_of_vampires", "wh_dlc07_trait_brt_lord_bad_sieging", "wh_dlc07_trait_brt_lord_bad_traitor",
  "wh_dlc07_trait_brt_lord_bad_villain", "wh_dlc07_trait_brt_lord_good_attacker", "wh_dlc07_trait_brt_lord_good_beastmen", "wh_dlc07_trait_brt_lord_good_chaos",
  "wh_dlc07_trait_brt_lord_good_defeated_archaon", "wh_dlc07_trait_brt_lord_good_defeated_grimgor", "wh_dlc07_trait_brt_lord_good_defeated_khazrak", "wh_dlc07_trait_brt_lord_good_defeated_manfred",
  "wh_dlc07_trait_brt_lord_good_defender", "wh_dlc07_trait_brt_lord_good_executing", "wh_dlc07_trait_brt_lord_good_far_from_capital", "wh_dlc07_trait_brt_lord_good_farming",
  "wh_dlc07_trait_brt_lord_good_greenskins", "wh_dlc07_trait_brt_lord_good_industry", "wh_dlc07_trait_brt_lord_good_knightly", "wh_dlc07_trait_brt_lord_good_lone_wolf",
  "wh_dlc07_trait_brt_lord_good_peasants", "wh_dlc07_trait_brt_lord_good_praying", "wh_dlc07_trait_brt_lord_good_public_order", "wh_dlc07_trait_brt_lord_good_reinforcing",
  "wh_dlc07_trait_brt_lord_good_sieging", "wh_dlc07_trait_brt_lord_good_vampires", "wh_dlc07_trait_brt_lord_good_victory", "wh_dlc07_trait_brt_protection_troth_chivalry_pledge",
  "wh_dlc07_trait_brt_protection_troth_chivalry_pledge_agent", "wh_dlc07_trait_brt_protection_troth_knowledge_pledge", "wh_dlc07_trait_brt_protection_troth_knowledge_pledge_agent", "wh_dlc07_trait_brt_protection_troth_order_pledge",
  "wh_dlc07_trait_brt_protection_troth_order_pledge_agent", "wh_dlc07_trait_brt_questing_vow_campaign_pledge", "wh_dlc07_trait_brt_questing_vow_campaign_pledge_agent", "wh_dlc07_trait_brt_questing_vow_heroism_pledge",
  "wh_dlc07_trait_brt_questing_vow_heroism_pledge_agent", "wh_dlc07_trait_brt_questing_vow_protect_pledge", "wh_dlc07_trait_brt_questing_vow_protect_pledge_agent", "wh_dlc07_trait_brt_virtue_troth_destroy_pledge",
  "wh_dlc07_trait_brt_virtue_troth_destroy_pledge_agent", "wh_dlc07_trait_brt_virtue_troth_destroy_pledge_fay", "wh_dlc07_trait_brt_virtue_troth_untaint_pledge", "wh_dlc07_trait_brt_virtue_troth_untaint_pledge_agent",
  "wh_dlc07_trait_brt_virtue_troth_untaint_pledge_fay", "wh_dlc07_trait_brt_virtue_troth_valour_pledge", "wh_dlc07_trait_brt_virtue_troth_valour_pledge_agent", "wh_dlc07_trait_brt_virtue_troth_valour_pledge_fay",
  "wh_dlc07_trait_brt_wisdom_troth_campaign_pledge", "wh_dlc07_trait_brt_wisdom_troth_campaign_pledge_agent", "wh_dlc07_trait_brt_wisdom_troth_heroism_pledge", "wh_dlc07_trait_brt_wisdom_troth_heroism_pledge_agent",
  "wh_dlc07_trait_brt_wisdom_troth_protect_pledge", "wh_dlc07_trait_brt_wisdom_troth_protect_pledge_agent", "wh_dlc08_trait_defeated_throgg", "wh_dlc08_trait_defeated_wulfrik",
  "wh_main_trait_all_personality_all_hates_dwarfs", "wh_main_trait_all_personality_all_hates_greenskins", "wh_main_trait_dummy_title_emperor", "wh_main_trait_general_personality_vmp_black_arts",
  "wh_main_trait_general_personality_vmp_dark_acolyte", "wh_main_trait_general_personality_vmp_summon_creatures", "wh_main_trait_name_dummy_blank", "wh_main_trait_name_dummy_de_bordeleaux",
  "wh_main_trait_name_dummy_franz", "wh_main_trait_name_dummy_gelt", "wh_main_trait_name_dummy_grudgebearer", "wh_main_trait_name_dummy_ironfist",
  "wh_main_trait_name_dummy_ironhide", "wh_main_trait_name_dummy_kemmler", "wh_main_trait_name_dummy_leoncoeur", "wh_main_trait_name_dummy_suneater",
  "wh_main_trait_name_dummy_the_ever-watcher", "wh_main_trait_name_dummy_the_everchosen", "wh_main_trait_name_dummy_the_magnificent", "wh_main_trait_name_dummy_the_slaughterer",
  "wh_main_trait_name_dummy_von_carstein", "wh_main_trait_supreme_patriarch", "wh_trait_dlc04_helman_not_shown", "wh_trait_dlc04_vlad_vanguard_not_shown",
}


function handlers.trait_progress(seq)
  local f = human_faction()
  local out = {}
  if f then
    local cl = try(function() return f:character_list() end)
    local n = cl and try(function() return cl:num_items() end) or 0
    for i = 0, n - 1 do
      local c = try(function() return cl:item_at(i) end)
      local cq = c and try(function() return c:command_queue_index() end)
      if c and cq then
        local pts = {}
        for j = 1, #TRAIT_KEYS do
          local k = TRAIT_KEYS[j]
          local ok, v = pcall(function() return c:trait_points(k) end)
          if ok and v and v > 0 then pts[#pts + 1] = k .. "~" .. tostring(v) end
        end
        if #pts > 0 then out[#out + 1] = { cqi = cq, pts = table.concat(pts, ",") } end
      end
    end
  end
  log({ seq = seq, cmd = "trait_progress", count = #out, chars = out })
end


function handlers.chars(seq)
  local f = human_faction()
  local out = {}
  if f then
    local myname = try(function() return f:name() end)
    local cl = try(function() return f:character_list() end)
    local n = cl and try(function() return cl:num_items() end) or 0
    for i = 0, n - 1 do
      local c = try(function() return cl:item_at(i) end)
      if c then
        local region_owner = try(function()
          local r = c:region(); if r and not r:is_null_interface() then return r:owning_faction():name() end
        end)


        local region_key = try(function()
          local r = c:region(); if r and not r:is_null_interface() then return r:name() end
        end)
        local province_key = try(function()
          local r = c:region(); if r and not r:is_null_interface() then return r:province_name() end
        end)
        out[#out + 1] = {
          cqi = or_null(try(function() return c:command_queue_index() end)),
          subtype = or_null(try(function() return c:character_subtype_key() end)),
          agent_type = or_null(try(function() return c:character_type_key() end)),
          is_leader = or_null(try(function() return c:is_faction_leader() end)),
          has_army = or_null(try(function() return c:has_military_force() end)),
          is_general = or_null(try(function() return c:character_type("general") end)),
          rank = or_null(try(function() return c:rank() end)),
          x = or_null(try(function() return c:logical_position_x() end)),
          y = or_null(try(function() return c:logical_position_y() end)),
          ap_pct = or_null(try(function() return c:action_points_remaining_percent() end)),
          stance = or_null(try(function() return c:military_force():active_stance() end)),
          hp = or_null(try(function()
            local ul = c:military_force():unit_list()
            local t = 0
            for k = 0, ul:num_items() - 1 do
              t = t + ul:item_at(k):percentage_proportion_of_full_strength()
            end
            return math.floor(t) / 100
          end)),


          units = or_null(try(function() return c:military_force():unit_list():num_items() end)),
          region_owner = or_null(region_owner),
          region = or_null(region_key),
          province = or_null(province_key),
          in_own_territory = or_null(region_owner ~= nil and myname ~= nil and region_owner == myname),
        }
      end
    end
  end
  log({ seq = seq, cmd = "chars", count = #out, chars = out })
end


function handlers.setts(seq)
  local f = human_faction()
  local out = {}
  if f then
    local rl = try(function() return f:region_list() end)
    local n = rl and try(function() return rl:num_items() end) or 0
    for i = 0, n - 1 do
      local r = try(function() return rl:item_at(i) end)
      if r then
        local s = try(function() return r:settlement() end)
        out[#out + 1] = {
          region = or_null(try(function() return r:name() end)),
          capital = or_null(try(function() return r:is_province_capital() end)),


          units = or_null(try(function() return r:garrison_residence():unit_count() end)),
          x = or_null(s and try(function() return s:logical_position_x() end)),
          y = or_null(s and try(function() return s:logical_position_y() end)),
        }
      end
    end
  end
  log({ seq = seq, cmd = "setts", count = #out, setts = out })
end


function handlers.hostiles(seq)
  local f = human_faction()
  local out = {}
  local myname = f and try(function() return f:name() end)
  local lx = f and try(function() return f:faction_leader():logical_position_x() end)
  local ly = f and try(function() return f:faction_leader():logical_position_y() end)
  local function dist(x, y)
    if lx and ly and x and y then local dx, dy = x - lx, y - ly; return math.floor(math.sqrt(dx * dx + dy * dy)) end
    return nil
  end
  if f then
    local fl = try(function() return cm:model():world():faction_list() end)
    local nf = fl and try(function() return fl:num_items() end) or 0
    for i = 0, nf - 1 do
      if #out >= 60 then break end
      local fac = try(function() return fl:item_at(i) end)
      local at_war = fac and try(function() return f:at_war_with(fac) end)
      local is_me = fac and myname and try(function() return fac:name() end) == myname


      local neutral = fac and not at_war and not is_me
      if fac and (at_war or neutral) then
        local fname = try(function() return fac:name() end)
        local cl = try(function() return fac:character_list() end)
        local nc = cl and try(function() return cl:num_items() end) or 0
        for j = 0, nc - 1 do
          if #out >= 60 then break end
          local c = try(function() return cl:item_at(j) end)
          local hasforce = c and try(function() return c:has_military_force() end)
          if c and hasforce == false then
            local hvis = try(function() return c:is_visible_to_faction(myname) end)
            if hvis == true then
              local hx = try(function() return c:logical_position_x() end)
              local hy = try(function() return c:logical_position_y() end)


              out[#out + 1] = { kind = (at_war and "hero" or "neutral_hero"), faction = or_null(fname),
                visible = or_null(hvis),
                cqi = or_null(try(function() return c:command_queue_index() end)),
                subtype = or_null(try(function() return c:character_subtype_key() end)),
                agent_type = or_null(try(function() return c:character_type_key() end)),
                province = or_null(try(function()
                  local r = c:region()
                  if r and not r:is_null_interface() then return r:province_name() end end)),
                x = or_null(hx), y = or_null(hy), dist = or_null(dist(hx, hy)) }
            end
          end
          if c and hasforce == true then


            local vis = try(function() return c:is_visible_to_faction(myname) end)
            if vis == true then
              local x = try(function() return c:logical_position_x() end)
              local y = try(function() return c:logical_position_y() end)
              out[#out + 1] = { kind = (at_war and "army" or "neutral_army"), faction = or_null(fname),
                visible = or_null(vis),
                cqi = or_null(try(function() return c:command_queue_index() end)),
                is_armed_citizenry = (try(function() return c:military_force():is_armed_citizenry() end) == true),


                units = or_null(try(function() return c:military_force():unit_list():num_items() end)),
                hp = or_null(try(function()
                  local ul = c:military_force():unit_list()
                  local t = 0
                  for k = 0, ul:num_items() - 1 do
                    t = t + ul:item_at(k):percentage_proportion_of_full_strength()
                  end
                  return math.floor(t) / 100
                end)),
                stance = or_null(try(function() return c:military_force():active_stance() end)),


                province = or_null(try(function()
                  local r = c:region()
                  if r and not r:is_null_interface() then return r:province_name() end end)),
                x = or_null(x), y = or_null(y), dist = or_null(dist(x, y)) }
            end
          end
        end


        local rl = at_war and try(function() return fac:region_list() end) or nil
        local nr = rl and try(function() return rl:num_items() end) or 0
        for j = 0, nr - 1 do
          if #out >= 60 then break end
          local r = try(function() return rl:item_at(j) end)
          local s = r and try(function() return r:settlement() end)
          if s then
            local x = try(function() return s:logical_position_x() end)
            local y = try(function() return s:logical_position_y() end)
            out[#out + 1] = { kind = "settlement", faction = or_null(fname),
              region = or_null(try(function() return r:name() end)),


              units = or_null(try(function() return r:garrison_residence():unit_count() end)),
              x = or_null(x), y = or_null(y), dist = or_null(dist(x, y)) }
          end
        end
      end
    end
  end
  log({ seq = seq, cmd = "hostiles", count = #out, hostiles = out })
end


function handlers.forces(seq)
  local f = human_faction()
  local out = {}
  local myname = f and try(function() return f:name() end)
  local lx = f and try(function() return f:faction_leader():logical_position_x() end)
  local ly = f and try(function() return f:faction_leader():logical_position_y() end)
  local function dist(x, y)
    if lx and ly and x and y then local dx, dy = x - lx, y - ly; return math.floor(math.sqrt(dx * dx + dy * dy)) end
    return nil
  end


  local MAX_DIST = 200
  local CAP = 250
  local function keep(at_war, d)
    if #out >= CAP then return false end
    if at_war then return true end
    return d ~= nil and d <= MAX_DIST
  end
  if f then
    local fl = try(function() return cm:model():world():faction_list() end)
    local nf = fl and try(function() return fl:num_items() end) or 0
    for i = 0, nf - 1 do
      if #out >= CAP then break end
      local fac = try(function() return fl:item_at(i) end)
      local fname = fac and try(function() return fac:name() end)

      if fac and fname and fname ~= myname and try(function() return not fac:is_null_interface() end) then
        local at_war = try(function() return f:at_war_with(fac) end) == true
        local alive = try(function() return not fac:is_dead() end)
        if alive ~= false then
          local cl = try(function() return fac:character_list() end)
          local nc = cl and try(function() return cl:num_items() end) or 0
          for j = 0, nc - 1 do
            if #out >= CAP then break end
            local c = try(function() return cl:item_at(j) end)
            if c and try(function() return c:has_military_force() end) then
              local x = try(function() return c:logical_position_x() end)
              local y = try(function() return c:logical_position_y() end)
              local d = dist(x, y)
              if keep(at_war, d) then
                out[#out + 1] = { kind = "army", faction = fname, at_war = at_war,
                  cqi = or_null(try(function() return c:command_queue_index() end)),
                  x = or_null(x), y = or_null(y), dist = or_null(d) }
              end
            end
          end
          local rl = try(function() return fac:region_list() end)
          local nr = rl and try(function() return rl:num_items() end) or 0
          for j = 0, nr - 1 do
            if #out >= CAP then break end
            local r = try(function() return rl:item_at(j) end)
            local s = r and try(function() return r:settlement() end)
            if s then
              local x = try(function() return s:logical_position_x() end)
              local y = try(function() return s:logical_position_y() end)
              local d = dist(x, y)
              if keep(at_war, d) then
                out[#out + 1] = { kind = "settlement", faction = fname, at_war = at_war,
                  region = or_null(try(function() return r:name() end)),
                  x = or_null(x), y = or_null(y), dist = or_null(d) }
              end
            end
          end
        end
      end
    end
  end
  log({ seq = seq, cmd = "forces", count = #out, forces = out })
end


local function faction_pos(g)
  local x = try(function() return g:faction_leader():logical_position_x() end)
  local y = try(function() return g:faction_leader():logical_position_y() end)
  if x and y then return x, y end
  local rl = try(function() return g:region_list() end)
  local n = rl and try(function() return rl:num_items() end) or 0
  for i = 0, n - 1 do
    local r = try(function() return rl:item_at(i) end)
    local s = r and try(function() return r:settlement() end)
    if s then
      x = try(function() return s:logical_position_x() end)
      y = try(function() return s:logical_position_y() end)
      if x and y then return x, y end
    end
  end
  return nil, nil
end


local function human_anchors(f)
  local pts = {}
  local cl = try(function() return f:character_list() end)
  local nc = cl and try(function() return cl:num_items() end) or 0
  for i = 0, nc - 1 do
    local c = try(function() return cl:item_at(i) end)
    local x = c and try(function() return c:logical_position_x() end)
    local y = c and try(function() return c:logical_position_y() end)
    if x and y then pts[#pts + 1] = { x = x, y = y } end
  end
  local rl = try(function() return f:region_list() end)
  local nr = rl and try(function() return rl:num_items() end) or 0
  for i = 0, nr - 1 do
    local r = try(function() return rl:item_at(i) end)
    local s = r and try(function() return r:settlement() end)
    local x = s and try(function() return s:logical_position_x() end)
    local y = s and try(function() return s:logical_position_y() end)
    if x and y then pts[#pts + 1] = { x = x, y = y } end
  end
  return pts
end


local function remove_faction(g)
  local gi = try(function() return cm.game_interface end)
  if not gi then return false, 0, 0 end
  local nreg, nchar = 0, 0
  local rl = try(function() return g:region_list() end)
  local rn = rl and try(function() return rl:num_items() end) or 0
  local keys = {}
  for i = 0, rn - 1 do
    local r = try(function() return rl:item_at(i) end)
    local k = r and try(function() return r:name() end)
    if k then keys[#keys + 1] = k end
  end
  for _, k in ipairs(keys) do
    if try(function() gi:set_region_abandoned(k) return true end) then nreg = nreg + 1 end
  end
  local cl = try(function() return g:character_list() end)
  local cn = cl and try(function() return cl:num_items() end) or 0
  local cqs = {}
  for i = 0, cn - 1 do
    local c = try(function() return cl:item_at(i) end)
    local q = c and try(function() return c:command_queue_index() end)
    if q then cqs[#cqs + 1] = q end
  end
  for _, q in ipairs(cqs) do
    if try(function()
      gi:kill_character("character_cqi:" .. tostring(q), true) return true end) then
      nchar = nchar + 1
    end
  end
  local left = try(function() return g:region_list():num_items() end) or 0
  return left == 0, nreg, nchar
end


local PROBE_NAMES = {
  "kill_faction", "kill_all_armies_for_faction", "kill_character",
  "transfer_region_to_faction", "set_region_abandoned", "abandon_region",
  "grant_region_to_faction", "make_region_ruined", "region_change",
  "confederate_factions", "force_declare_war", "disable_faction",
  "save_game", "request_save_game",
}


function handlers.apiprobe(seq, rest)
  local want = (rest or ""):lower()
  if want == "" then want = "kill|destroy|remove|region|faction|save|confeder" end
  local out = {}
  local mt = getmetatable(cm)
  local t = (mt and mt.__index) or cm
  for k, v in pairs(t) do
    if type(k) == "string" and type(v) == "function" then
      local lk = k:lower()
      for pat in want:gmatch("[^|]+") do
        if lk:find(pat, 1, true) then out[#out + 1] = k break end
      end
    end
  end
  table.sort(out)
  local globals = {}
  for _, n in ipairs({ "custom_starts", "core", "effect", "CampaignUI", "common" }) do
    globals[#globals + 1] = n .. "=" .. type(_G[n])
  end
  local direct = {}
  local gi = try(function() return cm.game_interface end)
  for _, n in ipairs(PROBE_NAMES) do
    direct[#direct + 1] = "cm." .. n .. "=" ..
      tostring(try(function() return type(cm[n]) end) or "err")
    if gi then
      direct[#direct + 1] = "gi." .. n .. "=" ..
        tostring(try(function() return type(gi[n]) end) or "err")
    end
  end
  for n in (rest or ""):gmatch("[^%s,|]+") do
    if not n:find("|") then
      direct[#direct + 1] = "cm." .. n .. "=" ..
        tostring(try(function() return type(cm[n]) end) or "err")
    end
  end
  log({ seq = seq, cmd = "apiprobe", pattern = want, n = #out, functions = out,
        direct = direct, has_game_interface = (gi ~= nil),
        mt_index_type = type(t), globals = globals, turn = turn() })
end


function handlers.trim(seq, rest)
  local radius = tonumber((rest or ""):match("([%-%d%.]+)") or "")
  local dry = (rest or ""):find("dry") ~= nil
  local f = human_faction()
  local me = f and try(function() return f:name() end)
  local anchors = f and human_anchors(f) or {}
  local can_disarm = try(function()
    return type(getmetatable(cm.game_interface).set_region_abandoned) == "function"
  end) or false
  if not radius or radius < 0 or #anchors == 0 then
    log({ seq = seq, cmd = "trim", error = "trim needs a radius >= 0 and a locatable "
          .. "human start", radius = or_null(radius), n_anchors = #anchors,
          me = or_null(me), turn = turn() })
    return
  end
  if not dry and not can_disarm then
    log({ seq = seq, cmd = "trim", error = "game_interface:set_region_abandoned is not "
          .. "available in this build; refusing to report removals that did not happen. "
          .. "Re-run with dry to measure the split only.", radius = radius,
          can_disarm = false, turn = turn() })
    return
  end
  local killed, kept, unplaced, failed = {}, {}, {}, {}
  local already = 0
  local n_regions, n_chars = 0, 0
  local fl = try(function() return cm:model():world():faction_list() end)
  local n = fl and try(function() return fl:num_items() end) or 0
  for i = 0, n - 1 do
    local g = try(function() return fl:item_at(i) end)
    local name = g and try(function() return g:name() end)
    if g and name and name ~= me then
      if try(function() return g:is_dead() end) then
        already = already + 1
      else
        local gx, gy = faction_pos(g)
        if not gx or not gy then
          unplaced[#unplaced + 1] = name
        else
          local d2min = nil
          for _, a in ipairs(anchors) do
            local dx, dy = gx - a.x, gy - a.y
            local d2 = dx * dx + dy * dy
            if not d2min or d2 < d2min then d2min = d2 end
          end
          if math.sqrt(d2min) > radius then
            local done = true
            if not dry then
              local nr, nc
              done, nr, nc = remove_faction(g)
              n_regions = n_regions + nr
              n_chars = n_chars + nc
            end
            if done then killed[#killed + 1] = name
            else failed[#failed + 1] = name end
          else
            kept[#kept + 1] = name
          end
        end
      end
    end
  end
  log({ seq = seq, cmd = "trim", radius = radius, dry = dry, me = or_null(me),
        n_anchors = #anchors, anchors = anchors, n_factions = n,
        n_killed = #killed, n_kept = #kept, n_unplaced = #unplaced,
        n_failed = #failed, n_already_dead = already,
        n_regions_abandoned = n_regions, n_characters_killed = n_chars,
        killed = killed, kept = kept, unplaced = unplaced, failed = failed,
        turn = turn() })
end


local SAVE_CANDIDATES = {
  { owner = "cm", name = "save_game" },
  { owner = "cm", name = "request_save_game" },
  { owner = "cm", name = "save" },
  { owner = "_G", name = "save_game" },
  { owner = "_G", name = "SaveGame" },
}


local CCO_SAVE_CALLS = { "SaveGame", "Save", "RequestSaveGame", "SaveCampaign" }


local function try_cco_save(name)
  for _, call in ipairs(CCO_SAVE_CALLS) do
    local ok = try(function()
      cco("CcoCampaignRoot", ""):Call(call .. '("' .. name .. '")')
      return true
    end)
    if ok then return "cco:CcoCampaignRoot:" .. call end
  end
  return nil
end


local function try_lua_save(name)
  local tried = {}
  for _, c in ipairs(SAVE_CANDIDATES) do
    local host = (c.owner == "cm") and cm or _G
    local fn = try(function() return host[c.name] end)
    if type(fn) == "function" then
      local ok = pcall(function()
        if c.owner == "cm" then fn(cm, name) else fn(name) end
      end)
      if ok then return c.owner .. ":" .. c.name, tried end
      tried[#tried + 1] = c.owner .. ":" .. c.name .. " raised"
    else
      tried[#tried + 1] = c.owner .. ":" .. c.name .. " absent"
    end
  end
  return nil, tried
end


function handlers.savegame(seq, rest)
  local name = (rest or ""):gsub("^%s+", ""):gsub("%s+$", "")
  if name == "" then
    log({ seq = seq, cmd = "savegame", error = "savegame needs a file name" })
    return
  end
  local used = try_cco_save(name)
  local tried = {}
  if not used then used, tried = try_lua_save(name) end
  log({ seq = seq, cmd = "savegame", name = name, used = or_null(used),
        error = or_null(used and nil or
          "no scripted save answered; drive the save screen by click instead"),
        tried = tried, roots = or_null(used and nil or root_names()), turn = turn() })
end


local last_seq = 0
local last_pos = 0

local function process()
  local f = io.open(CMD_PATH, "r")
  if not f then return end


  local size = f:seek("end")
  if size < last_pos then last_pos = 0 end
  if size == last_pos then f:close(); return end
  f:seek("set", last_pos)
  local lines = {}
  local file_max = 0
  for line in f:lines() do
    lines[#lines + 1] = line
    local s = tonumber(string.match(line, "^%s*(%d+)"))
    if s and s > file_max then file_max = s end
  end
  last_pos = f:seek("cur")
  f:close()


  if #lines > 0 and file_max < last_seq then
    last_seq = 0
    pcall(function() cm:set_saved_value("twcontrol_last_seq", 0) end)
  end
  for _, line in ipairs(lines) do
    local seq, cmd, rest = string.match(line, "^%s*(%d+)%s+(%S+)%s*(.*)$")
    seq = tonumber(seq)
    if seq and seq > last_seq and handlers[cmd] then
      last_seq = seq
      pcall(function() cm:set_saved_value("twcontrol_last_seq", last_seq) end)
      local ok, err = pcall(handlers[cmd], seq, rest or "")
      if not ok then log({ seq = seq, cmd = cmd, error = tostring(err) }) end
    elseif seq and seq > last_seq then
      last_seq = seq
      log({ seq = seq, cmd = cmd, error = "unknown command" })
    end
  end
end

local POLL_MS = POLL_SECONDS * 1000
local function poll()
  pcall(process)
  if cm and #assist_watch > 0 then pcall(assist_cleanup) end


  local armed = false
  pcall(function() core:get_tm():real_callback(poll, POLL_MS, "twfrontend_poll"); armed = true end)
  if not armed and cm then
    pcall(function() cm:callback(poll, POLL_SECONDS) end)
  end
end


local function max_seq_in_file()
  local m = 0
  local f = io.open(CMD_PATH, "r")
  if not f then return 0 end
  for line in f:lines() do
    local s = tonumber(string.match(line, "^%s*(%d+)"))
    if s and s > m then m = s end
  end
  f:close()
  return m
end


local quit_on_defeat_sent = false


local function arm_defeat_listener()
  if not (core and core.add_listener) then
    log({ cmd = "defeat_listener", armed = false, reason = "core:add_listener unavailable" })
    return
  end


  local ok = false
  for _, ev in ipairs({ "FactionDestroyed", "FactionDied", "FactionDeath" }) do
    local armed = pcall(function()
      core:add_listener("twcontrol_" .. ev, ev, true,
        function(context)
          local fn = try(function() return context:faction():name() end)
          local me = try(function() return human_faction():name() end)
          local us = (fn ~= nil and fn == me)
          log({ cmd = "faction_destroyed", event = ev, faction = fn,
                is_us = us, turn = turn() })

          if us and not quit_on_defeat_sent then
            quit_on_defeat_sent = true
            local ok = pcall(function() cm:quit() end)
            log({ cmd = "defeat_quit", dispatched = ok })
          end
        end, true)
    end)
    ok = ok or armed
  end


  pcall(function()
    core:add_listener("twcontrol_turn_start", "FactionTurnStart", true,
      function(context)
        local fn = try(function() return context:faction():name() end)
        local me = try(function() return human_faction():name() end)
        if fn ~= nil and fn == me then
          local n = try(function() return human_faction():region_list():num_items() end)
          log({ cmd = "turn_start", turn = turn(), regions = or_null(n) })


          if n == 0 then log({ cmd = "regions_zero", faction = me, turn = turn() }) end
        end
      end, true)
  end)
  log({ cmd = "defeat_listener", armed = ok })
end


local function arm_event_recorder()
  if not (core and core.add_listener) then
    log({ cmd = "event_recorder", armed = false, reason = "core:add_listener unavailable" })
    return
  end
  local ok = pcall(function()
    core:add_listener("twcontrol_dilemma_issued", "DilemmaIssuedEvent", true,
      function(context)
        log({ cmd = "dilemma_issued", turn = turn(),
              dilemma = or_null(try(function() return context:dilemma() end)),
              faction = or_null(try(function() return context:faction():name() end)) })
      end, true)
    core:add_listener("twcontrol_dilemma_choice", "DilemmaChoiceMadeEvent", true,
      function(context)
        log({ cmd = "dilemma_choice_made", turn = turn(),
              dilemma = or_null(try(function() return context:dilemma() end)),
              choice = or_null(try(function() return context:choice() end)),
              faction = or_null(try(function() return context:faction():name() end)) })
      end, true)
    core:add_listener("twcontrol_incident", "IncidentOccuredEvent", true,
      function(context)


        local key, via = nil, nil
        for _, acc in ipairs({ "incident", "incident_key", "string", "dilemma", "key" }) do
          if key == nil then
            local v = try(function() return context[acc](context) end)
            if v ~= nil then key = v; via = acc end
          end
        end
        log({ cmd = "incident_occured", turn = turn(),
              incident = or_null(key), via = or_null(via),
              faction = or_null(try(function() return context:faction():name() end)) })
      end, true)

    core:add_listener("twcontrol_battle_completed", "BattleCompleted", true,
      function(context)
        log({ cmd = "battle_completed", turn = turn(),
              autoresolved = or_null(try(function()
                return context:model():pending_battle():has_been_autoresolved() end)) })
      end, true)


    for _, ev in ipairs({
      "PositiveDiplomaticEvent", "NegativeDiplomaticEvent",
      "DiplomaticDealMade", "DiplomaticDeal", "DiplomacyDealStruck",
      "FactionDeclaresWar", "WarDeclared", "DeclaredWar",
      "FactionBecomesVassal", "FactionBecomesConfederation", "FactionConfederates",
      "TreatySigned", "TreatyBroken", "AllianceFormed", "MilitaryAllianceFormed",
      "FactionLeaderSignsPeaceTreaty", "DiplomaticOfferReceived", "DiplomacyOfferMade",
      "ForcedDiplomacy", "TributeDemanded", "FactionOffersPeace" }) do
      pcall(function()
        core:add_listener("twcontrol_diplo_" .. ev, ev, true,
          function(context)
            log({ cmd = "diplo_event", event = ev, turn = turn(),
                  a = or_null(try(function() return context:proposer():name() end)),
                  b = or_null(try(function() return context:recipient():name() end)),
                  faction = or_null(try(function() return context:faction():name() end)),
                  me = or_null(try(function() return human_faction():name() end)) })
          end, true)
      end)
    end

    for _, ev in ipairs({ "CharacterGarrisonTargetAction", "CharacterCharacterTargetAction" }) do
      pcall(function()
        core:add_listener("twcontrol_agent_" .. ev, ev, true,
          function(context)
            log({ cmd = "agent_action", event = ev, turn = turn(),
                  cqi = or_null(try(function()
                    return context:character():command_queue_index() end)),
                  action = or_null(try(function() return context:agent_action_key() end)),
                  ability = or_null(try(function() return context:ability() end)),
                  attribute = or_null(try(function() return context:attribute() end)),
                  garrison = or_null(try(function()
                    return context:garrison_residence():region():name() end)),
                  target_cqi = or_null(try(function()
                    return context:target_character():command_queue_index() end)),
                  success = or_null(try(function() return context:mission_result_success() end)),
                  faction = or_null(try(function()
                    return context:character():faction():name() end)) })
          end, true)
      end)
    end

    core:add_listener("twcontrol_panel_opened", "PanelOpenedCampaign", true,
      function(context)
        local pname = or_null(try(function() return context.string end))
        log({ cmd = "panel", opened = true, turn = turn(), name = pname })

        if pname == "campaign_victory" then
          local seen = {}
          local function walk(c, depth)
            if not c or depth > 6 then return end
            local id = try(function() return c:Id() end)
            if id then seen[#seen + 1] = id end
            local n = try(function() return c:ChildCount() end) or 0
            for i = 0, n - 1 do
              walk(try(function() return UIComponent(c:Find(i)) end), depth + 1)
            end
          end
          pcall(function() walk(root(), 0) end)
          log({ cmd = "campaign_end_panel", panel = pname, n_seen = #seen, seen = seen })
        end
      end, true)
    core:add_listener("twcontrol_panel_closed", "PanelClosedCampaign", true,
      function(context)
        log({ cmd = "panel", opened = false, turn = turn(),
              name = or_null(try(function() return context.string end)) })
      end, true)

    pcall(function()
      core:add_listener("twcontrol_character_selected", "CharacterSelected", true,
        function(context)
          log({ cmd = "character_selected", turn = turn(),
                cqi = or_null(try(function()
                  return context:character():command_queue_index() end)) })
        end, true)
    end)
  end)
  log({ cmd = "event_recorder", armed = ok })
end


local started = false
local function start(hook)
  local saved = try(function() return cm:get_saved_value("twcontrol_last_seq") end)
  if not started then
    last_seq = saved or max_seq_in_file()
    local armed = false
    pcall(function() cm:callback(poll, POLL_SECONDS); armed = true end)
    if not armed then
      log({ cmd = "start_deferred", hook = hook,
            reason = "cm:callback not available yet -- leaving it to a later hook" })
      return
    end
    started = true
    pcall(arm_defeat_listener)
    pcall(arm_event_recorder)
    log({ cmd = "event_feed_filter", armed = false, reason = "suppression_removed" })
    say("controller running, last_seq=" .. last_seq .. " -> polling " .. CMD_PATH)
  end

  pcall(function() cm:skip_all_campaign_cutscenes() end)
  pcall(function() if cm:is_intro_cutscene_playing() then cm:skip_all_campaign_cutscenes() end end)

  log({ cmd = "started", hook = hook, turn = turn(), last_seq = last_seq,
        fresh = (saved == nil), ui = (find_uicomponent ~= nil), cmd_path = CMD_PATH })
end


local function start_frontend()
  if started then return end
  started = true
  last_seq = max_seq_in_file()
  log({ cmd = "frontend_armed", last_seq = last_seq, ui = (find_uicomponent ~= nil), cmd_path = CMD_PATH })
  poll()
end

local function suppress_intro_cutscene()
  if type(faction_start) ~= "table" then return "no faction_start" end
  if type(faction_start.register_intro_cutscene_callback) ~= "function" then
    return "no register fn"
  end
  faction_start.register_intro_cutscene_callback = function(self, cb)
    if type(self) == "table" then self.intro_cutscene_callback = nil end
  end
  local n = 0
  for _, v in pairs(_G) do
    if type(v) == "table" and rawget(v, "intro_cutscene_callback") ~= nil then
      v.intro_cutscene_callback = nil
      n = n + 1
    end
  end
  return "suppressed (cleared " .. tostring(n) .. " live)"
end

log({ cmd = "intro_cutscene", result = tostring(try(suppress_intro_cutscene)) })

function twcontrol() start("entry_point") end
if cm and cm.add_ui_created_callback then
  pcall(function() cm:add_ui_created_callback(function() start("ui_created") end) end)
end
if cm and cm.add_first_tick_callback then
  pcall(function() cm:add_first_tick_callback(function() start("first_tick") end) end)
else

  pcall(start_frontend)
end
