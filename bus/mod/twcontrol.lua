


local CMD_PATH = "@@BUS_CMD_PATH@@"
local OUT_PATH = "@@BUS_OUT_PATH@@"
local POLL_SECONDS = 0.1


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


local function disarm_faction(g, name)
  local ok = try(function() cm:kill_all_armies_for_faction(g) return true end)
  if not ok then
    ok = try(function() cm:kill_all_armies_for_faction(name) return true end)
  end
  local cl = try(function() return g:character_list() end)
  local cn = cl and try(function() return cl:num_items() end) or 0
  for i = 0, cn - 1 do
    local c = try(function() return cl:item_at(i) end)
    local cqi = c and try(function() return c:command_queue_index() end)
    if cqi then
      local killed = try(function()
        cm:kill_character("character_cqi:" .. tostring(cqi), true) return true end)
      if not killed then
        try(function() cm:kill_character(tostring(cqi), true) return true end)
      end
    end
  end
  return ok == true
end


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
  log({ seq = seq, cmd = "apiprobe", pattern = want, n = #out, functions = out,
        globals = globals, turn = turn() })
end


function handlers.trim(seq, rest)
  local radius = tonumber((rest or ""):match("([%-%d%.]+)") or "")
  local dry = (rest or ""):find("dry") ~= nil
  local f = human_faction()
  local me = f and try(function() return f:name() end)
  local ox, oy = nil, nil
  if f then ox, oy = faction_pos(f) end
  local can_disarm = try(function()
    return type(cm.kill_all_armies_for_faction) == "function" end) or false
  if not radius or radius < 0 or not ox or not oy then
    log({ seq = seq, cmd = "trim", error = "trim needs a radius >= 0 and a locatable "
          .. "human start", radius = or_null(radius), origin_x = or_null(ox),
          origin_y = or_null(oy), me = or_null(me), turn = turn() })
    return
  end
  if not dry and not can_disarm then
    log({ seq = seq, cmd = "trim", error = "cm:kill_all_armies_for_faction is not "
          .. "available in this build; refusing to report removals that did not happen. "
          .. "Re-run with dry to measure the split only.", radius = radius,
          can_disarm = false, turn = turn() })
    return
  end
  local killed, kept, unplaced, failed = {}, {}, {}, {}
  local already = 0
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
          local dx, dy = gx - ox, gy - oy
          if math.sqrt(dx * dx + dy * dy) > radius then
            local done = true
            if not dry then done = disarm_faction(g, name) end
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
        origin_x = ox, origin_y = oy, n_factions = n,
        n_killed = #killed, n_kept = #kept, n_unplaced = #unplaced,
        n_failed = #failed, n_already_dead = already,
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


  if cm then
    pcall(function() cm:callback(poll, POLL_SECONDS) end)
  else
    pcall(function() core:get_tm():real_callback(poll, POLL_MS, "twfrontend_poll") end)
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
