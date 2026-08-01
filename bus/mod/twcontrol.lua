-- twcontrol -- command-driven player controller for WH3 campaign.
--
-- Installed once, then driven live: it polls a plain-text command file and executes
-- rules-respecting actions, logging every result as JSON. No rebuild/relaunch needed
-- to try new commands -- append a line to the command file and the mod runs it.
--
-- Command file:  D:/totalwar_runner/data/commands.txt   (one command per line)
-- Result log:    D:/totalwar_runner/data/twcontrol.jsonl (one JSON object per line)
--
-- Line format:   <seq> <cmd> <args...>
--   3 snapshot
--   4 move_leader 1071 486          -- cm:move_character, AP-respecting (legal)
--   5 move 17 1071 486
--   6 click hud_campaign|faction_buttons_docker|button_end_turn
--   7 find hud_campaign|faction_buttons_docker
--   8 end_turn
--   9 autoresolve
-- seq must strictly increase; the mod persists the last seq it ran (survives reload)
-- so a reloaded save never re-executes old commands.

local CMD_PATH = "D:/totalwar_runner/data/commands.txt"       -- MUST match api.py DATA
local OUT_PATH = "D:/totalwar_runner/data/twcontrol.jsonl"    -- MUST match api.py OUT_PATH
local POLL_SECONDS = 0.1   -- read latency floor. Was 1.0 (every bus read cost ~1-1.25s, and a panel
                           -- enumeration of N options cost N*1s). 0.1 makes reads ~10x faster; the
                           -- file-size guard in process() keeps idle polls O(1) so the higher cadence
                           -- is cheap even as commands.txt grows.

-- ------------------------------------------------------------------ JSON output
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
local function log(tbl)
  local ok, line = pcall(encode, tbl)
  if not ok then say("encode failed"); return end
  local f = io.open(OUT_PATH, "a"); if not f then return end
  f:write(line, "\n"); f:flush(); f:close()
end

-- ------------------------------------------------------------------- helpers
local function try(fn) local ok, v = pcall(fn); if ok then return v end return nil end
local function turn() return try(function() return cm:turn_number() end) or -1 end

local function human_faction()
  return try(function() return cm:get_local_faction(true) end)
      or try(function()
        local fl = cm:model():world():faction_list()
        for i = 0, fl:num_items() - 1 do local f = fl:item_at(i); if f:is_human() then return f end end
      end)
end

local function root() return try(function() return core:get_ui_root() end) end

-- Resolve a pipe-separated UI path to a component (or nil).
-- ‼ HISTORY, so nobody re-derives this the expensive way:
-- The engine dies (0xc0000409 STATUS_STACK_BUFFER_OVERRUN, WER bucket
-- 2066979503227050398) whenever this mod walks the UI tree. I blamed RECURSION
-- first and swapped find_uicomponent for an iterative queue -- IT CRASHED AGAIN,
-- same bucket, with the iterative walk verified inside the installed pack. The
-- stack was never it: the cost is TOUCHING THOUSANDS OF COMPONENTS, and that is
-- fatal however you loop. Two dead runs were logged as 'bus timed out' -- the bus
-- was fine, the GAME WAS GONE. Check Get-Process before theorising.
-- ‼‼ DO NOT WALK THE TREE. AT ALL. NOT EVEN ITERATIVELY.
-- I first blamed RECURSION for the 0xc0000409 crashes and replaced find_uicomponent with an
-- iterative queue. The game CRASHED AGAIN, same WER fault bucket (2066979503227050398), with that
-- iterative walk installed and verified in the pack. So the stack was never the problem: TOUCHING
-- THOUSANDS OF COMPONENTS is. Calling ChildCount()/Find()/Id() across the whole tree kills the
-- engine however you loop. The earlier agent wrote it plainly at handlers.children -- "no
-- whole-tree walk (that can hard-crash the engine)" -- and I have now proved it twice, the
-- expensive way, in the same file.
-- THE WAY OUT: never search. `uic:Find("name")` returns a DIRECT CHILD by name -- one engine call,
-- no traversal. The recorder gives us the human's FULL path for every action
-- (root > settlement_panel > settlement_list > ... > square_building_button), so a direct
-- child-by-name at each level resolves everything we actually need, and a miss is a cheap nil.
-- If a caller passes a partial path, that is the CALLER's bug -- fix the path, do not reintroduce
-- a search that takes the process down.
local function descend(parent, name)
  -- INDEX addressing: a segment "#N" selects the parent's N-th DIRECT child by index (UIComponent:Find
  -- accepts a numeric index), reaching children that are unnamed OR whose name collides with another
  -- component that Find(name) resolves first (e.g. dlc27_hef_intrigue_court: a small resources-bar
  -- anchor shares the id and shadows the real panel). Name lookup is unchanged for non-"#" segments.
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
  if #parts == 0 then return nil, parts end
  local node = root()
  for _, name in ipairs(parts) do
    node = descend(node, name)
    if not node then return nil, parts end
  end
  return node, parts
end

-- Screen position (top-left) of a component; Position() returns two values.
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
    -- ‼ #1 LINCHPIN: the component's DISPLAYED text (GetStateText = localised text of the current
    -- state). This is what lets the recorder identify options by LABEL and read costs/rewards/resource
    -- values -- the only robust identity when structure/position/count varies (the session's north
    -- star). Over-collect on every find; null when a component has no text. GetStateTextLabel is a
    -- doc-verified sibling (some components put the value there) -- kept as an additive probe.
    text = or_null(try(function() return uic:GetStateText() end)),
    text_label = or_null(try(function() return uic:GetStateTextLabel() end)),
    tooltip = or_null(try(function() return uic:GetTooltipText() end)),
  }
end

-- ------------------------------------------------------------------ context objects
-- WH3 UI cards bind a CONTEXT OBJECT (a "Cco*" record) that the game reads to draw them.
-- POSITIONAL cards (army-box LandUnit/QueuedLandUnit, occupation cards, intrigue rows) carry
-- NO game key in their component id or any child id -- their identity lives ONLY in that bound
-- context object. context_id(uic) reads it: a FIXED, CURATED list of Cco types, ONE cheap
-- pcall-wrapped API call each (NOT a tree walk), returning the FIRST non-empty id as
-- "<CcoType>:<id>".
-- ‼ Bounded exactly like every other read here: a fixed small list, direct calls on the
-- already-resolved component, everything pcall'd -- a component with no context returns nil.
--
-- DISCOVERED LIVE (Alith Anar / Nagarythe, turn 1, army-box units_panel|main_units_panel|units):
--   * The real API is uic:GetContextObjectId("<CcoType>") -- returns the bound context object's
--     id STRING (GetContextObject also exists; the ...TypeId / ContextObject* names do not).
--   * The unit CARDS bind by numeric handle: an active `LandUnit N` -> CcoCampaignUnit:<num>
--     (and CcoCampaignCharacter:<general cqi>); a `QueuedLandUnit N` (an in-progress recruit)
--     binds ONLY CcoCampaignCharacter:<general cqi> -- the card id gives NO unit identity.
--   * The unit KEY lives one level down, on each card's `card_image_holder` child, bound as
--     CcoMainUnitRecord whose id IS THE UNIT KEY VERBATIM (e.g. wh2_main_hef_inf_spearmen_0) --
--     for BOTH active and queued cards. So CcoMainUnitRecord is in the curated list: because
--     card_image_holder is a DIRECT child of the card, one `find` on the card returns the unit
--     key in child_contexts (the card_image_holder slot). A CcoCampaignUnit's numeric id also
--     resolves to its key via cco("CcoCampaignUnit",id):Call("UnitRecordContext.Key") -- the
--     downstream model-join lever, not needed when CcoMainUnitRecord is present.
local CCO_TYPES = {
  "CcoCampaignUnit", "CcoCampaignCharacter", "CcoMainUnitRecord",
  "CcoCampaignSettlement", "CcoCampaignRegion", "CcoCampaignAncillary", "CcoCampaignRitual",
  "CcoCampaignBuildingChainRecord", "CcoCampaignFaction", "CcoCampaignProvince",
}

-- Return the component's context-object id as "<CcoType>:<id>" (self-describing so the caller
-- knows WHICH context object matched), or nil if the component binds none of the curated types.
local function context_id(uic)
  if not uic then return nil end
  for _, t in ipairs(CCO_TYPES) do
    local v = try(function() return uic:GetContextObjectId(t) end)
    if v ~= nil and v ~= "" then return t .. ":" .. tostring(v) end
  end
  return nil
end

-- --------------------------------------------------------------- command impls
local handlers = {}

function handlers.snapshot(seq)
  local f = human_faction()
  local leader = f and try(function() return f:faction_leader() end)
  -- Income: no confirmed faction getter; try a couple of candidates (safe via try()).
  -- If these stay null in-game, income is read from the top-bar UI instead (verified live).
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
  local kids = {}            -- UNCHANGED shape: parallel array of direct-child Ids (downstream depends on this)
  local kctx = {}            -- ADDITIVE: parallel array of each direct child's context id (same order/length as kids)
  if uic then
    local n = try(function() return uic:ChildCount() end) or 0
    for i = 0, n - 1 do
      local child = try(function() return UIComponent(uic:Find(i)) end)
      -- keep child_ids EXACTLY as before (Id of the i-th direct child, or null)
      kids[#kids + 1] = or_null(child and try(function() return child:Id() end))
      -- alongside it, the child's bound context-object id (nil/null when it binds none)
      kctx[#kctx + 1] = or_null(child and context_id(child))
    end
  end
  local res = describe(uic)
  res.context = or_null(context_id(uic))   -- ADDITIVE: resolved node's own context id
  log({ seq = seq, cmd = "find", path = rest, result = res, child_ids = kids,
        child_contexts = kctx })
end

-- ‼ CAPTURE EVERYTHING (menu scraping): describe an ENTIRE bounded subtree in ONE reply, so the
-- recorder can grab a whole open menu (every option + its label/cost/reward/state via GetStateText)
-- in a single bus round-trip instead of hundreds. This is NOT the CTD path: it never SEARCHES by
-- bare name -- it walks EXPLICIT children (ChildCount + Find(i), the exact bounded pattern the find
-- handler already uses), and it is hard-bounded by BOTH max_depth AND max_nodes so a pathological
-- tree can never run the game into the ground. Visible-only descent (a hidden subtree is a
-- ghost/virtualized dead-end) keeps the node count near the on-screen option set. Each node carries
-- its full pipe-path so the caller can see the source (e.g. recruitment local vs global pool).
--   rest = "<path>"  |  "<path> <max_depth> <max_nodes>"   (defaults: depth 16, nodes 500)
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
      d.context = or_null(context_id(uic))         -- positional cards' key lives only in context
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

-- Names of every visible root, cheap, for before/after comparison around an input.
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

-- ‼ `clicked` ONLY EVER MEANT "SimulateLClick DID NOT THROW". It never meant the component reacted,
-- and it is returned as true for components that ignore the call completely -- which is how a click
-- that did nothing has repeatedly been read as evidence that it worked. The fix is not a better
-- flag, it is REPORTING THE OBSERVABLE FACTS either side of the input so the caller can judge:
-- what was actually targeted (id/state/visibility/geometry), and what changed (component state,
-- visibility, and the visible root set). A click that changes none of those did nothing, whatever
-- SimulateLClick returned.
function handlers.click(seq, rest)
  local uic = resolve(rest)
  local info = { seq = seq, cmd = "click", path = rest, found = (uic ~= nil), turn = turn() }
  if uic then
    local x, y = xy(uic)
    info.id = or_null(try(function() return uic:Id() end))
    info.state_before = or_null(try(function() return uic:CurrentState() end))
    info.visible_before = or_null(try(function() return uic:Visible() end))
    -- VisibleFromRoot is the one that matters: a component whose ancestor is detached from the UI
    -- root reports Visible()==true while being undisplayable, and clicks on it are silent no-ops.
    info.visible_from_root = or_null(try(function() return uic:VisibleFromRoot() end))
    info.x, info.y = or_null(x), or_null(y)
    info.w = or_null(try(function() return uic:Width() end))
    info.h = or_null(try(function() return uic:Height() end))
    info.roots_before = root_names()
    info.clicked = try(function() uic:SimulateLClick(); return true end) == true
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

-- ‼ A CONTEXT COMMAND REPORTED NOTHING AT ALL. Callers ran it through `eval` and returned the
-- literal string "sent", so a command that was malformed, referred to a missing context, or was
-- simply ignored looked identical to one that worked -- and that is precisely what was being used
-- to judge whether the UI's own commands can replace a hardware click.
-- This dispatches it and reports the pcall result plus the root set either side, so "dispatched"
-- and "had an effect" stay separate facts.
-- ‼ THE HUD IS HIDDEN, NOT DESTROYED, AND ONLY THE MOD CAN UNHIDE IT.
-- Measured on a wedged campaign: `hud_campaign` was still a child of the UI root with 24 children
-- of its own and Visible()==false, while cm:is_any_cutscene_running(), cm:is_cinematic_ui_enabled()
-- were both false -- so it is not a cutscene and none of the campaign-script recoveries
-- (skip_all_campaign_cutscenes / steal_user_input(false) / enable_ui(true)) touch it. It is a plain
-- UI-visibility problem.
-- It cannot be fixed from `eval`: those chunks compile into a sandbox where `core` and
-- `find_uicomponent` are nil, so the component is unreachable there. Hence a handler.
function handlers.show(seq, rest)
  local uic = resolve(rest)
  local before, after = nil, nil
  local ok = false
  local fronted = false
  if uic then
    before = try(function() return uic:Visible() end)
    ok = try(function() uic:SetVisible(true); return true end) == true
    -- Visible is not the same as reachable: a shown component can still sit behind another, so
    -- bring it forward too. Guarded -- if the build has no such method this is simply skipped.
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
  -- ‼ THERE ARE THREE BUTTON SETS AND EACH HAS ITS OWN button_autoresolve:
  --     button_set_siege  (visible for a SIEGE)
  --     button_set_attack (visible for a field battle)
  --     button_set_mp
  -- find_uicomponent returns the FIRST match, so targeting button_set_attack blindly clicks a
  -- HIDDEN set's autoresolve during a siege: the mod logged clicked=true and NOTHING happened,
  -- the battle stayed pending, and capture_settlement reported outcome='none'. A textbook
  -- click-flag lie (POINTS.md P4). Try the SIEGE set first, then attack, then mp -- and the
  -- geometry guard below still rejects an out-of-root false positive from the bare fallback.
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
    -- The last path is the BARE name "button_autoresolve". A bare-name find that misses does
    -- not return nil -- find_uicomponent walks the WHOLE tree and hands back an unrelated
    -- component. That happened for real: it returned a button at y=1304 inside a 1116-tall UI
    -- root and clicking it STARTED THE REAL-TIME BATTLE instead of autoresolving.
    -- So sanity-check the geometry: anything outside the root's own rect is a false positive.
    if uic then
      local ok = true
      -- a hidden button set still RESOLVES; clicking it fires nothing. Require visibility.
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

-- eval: run an arbitrary Lua chunk and return its value as JSON. Diagnostic/dev lever so
-- new queries and experiments (enemy stance, reachability, camera scroll, cutscene-skip
-- probing) need no rebuild/restart. `rest` is compiled as "return <rest>" first, then as
-- a plain statement block if that fails. Non-encodable results (game userdata) log as a
-- type string. Line: <seq> eval <lua>
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

-- (A temporary DEV-ONLY `handlers.dev` god-mode-over-bus helper lived here during v5 testing to force
-- devotees/an ally to reach cult/war-coordination screens; REMOVED -- the shipped recorder is passive/
-- read-only and must never carry a god-mode bus command. force_alliance was safe; faction_add_pooled_
-- resource soft-hangs this build via the PooledResourceChanged cascade, so devotees were not forced.)

-- roots: dump the ROOT's direct children with visibility. Exists because `core` is NIL inside
-- eval chunks (they compile into a sandboxed env), so `core:get_ui_root()` cannot be reached from
-- the outside -- every such eval returns None and looks like a broken resolver. It is not: the
-- mod's own functions see `core` fine, which is why `find` works.
-- WHY THIS MATTERS: a full-screen modal (the faction INTRO CINEMATIC) sat over the campaign all
-- session and blocked every verb -- button_end_turn read state=inactive the whole time. I could not
-- dismiss it because I was GUESSING component names (button_continue: absent; button_close:
-- found+clicked=true and the screen did not change by one byte -- a click-flag lie). Enumerate,
-- do not guess.
-- Line: <seq> roots
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

-- children: enumerate the direct children of a component found by pipe-path (or of a
-- name resolved recursively by the engine). Safe drill-down for UI discovery -- no
-- whole-tree walk (that can hard-crash the engine). Use find/click with the paths.
function handlers.children(seq, rest)
  handlers.find(seq, rest)
end

-- clickidx: SimulateLClick the Nth direct child of a container (0-based). Solves the
-- "cloned list items share the template's Id" problem: dilemma choices and recruitment
-- unit cards are index-addressable children whose Id equals the hidden template's, so
-- find_uicomponent(name) always returns the prototype. Index access clicks the real one.
-- Line: <seq> clickidx <pipe-path> <index>
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

-- chars: list this faction's characters (lords/heroes) with cqi, type, position,
-- movement, and whether standing in own territory. Foundation for move/recruit/attack.
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
        -- WHICH region/province this character stands in. Positions alone cannot answer "how many
        -- lords are in THIS province", because nothing maps an (x,y) to a province -- so the
        -- province-level force counts were impossible to build without it. Null-guarded: a
        -- character at sea or in transit has a NULL region interface, which is truthy in Lua.
        local region_key = try(function()
          local r = c:region(); if r and not r:is_null_interface() then return r:name() end
        end)
        local province_key = try(function()
          local r = c:region(); if r and not r:is_null_interface() then return r:province_name() end
        end)
        out[#out + 1] = {
          cqi = or_null(try(function() return c:command_queue_index() end)),
          subtype = or_null(try(function() return c:character_subtype_key() end)),
          is_leader = or_null(try(function() return c:is_faction_leader() end)),
          has_army = or_null(try(function() return c:has_military_force() end)),
          is_general = or_null(try(function() return c:character_type("general") end)),
          x = or_null(try(function() return c:logical_position_x() end)),
          y = or_null(try(function() return c:logical_position_y() end)),
          ap_pct = or_null(try(function() return c:action_points_remaining_percent() end)),
          stance = or_null(try(function() return c:military_force():active_stance() end)),
          -- STRENGTH. Without this nothing in the feature row distinguishes attacking 1v10 from
          -- 10v1, so the model cannot learn that an attack is suicide. nil for a hero (no force).
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

-- setts: list owned settlements with region key, position, and whether capital.
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
          -- OUR OWN garrison strength. The hostiles channel has carried enemy garrison size from
          -- the start, so the agent could see how well defended every enemy settlement was and
          -- nothing at all about its own -- it could not tell an empty capital from a full one, and
          -- so could never learn when leaving home undefended is what loses the campaign.
          -- Same accessor as the hostile side: unit_count() is on the GARRISON RESIDENCE, not on
          -- its army(), which is a NULL interface for an ordinary garrison.
          units = or_null(try(function() return r:garrison_residence():unit_count() end)),
          x = or_null(s and try(function() return s:logical_position_x() end)),
          y = or_null(s and try(function() return s:logical_position_y() end)),
        }
      end
    end
  end
  log({ seq = seq, cmd = "setts", count = #out, setts = out })
end

-- hostiles: enemy armies + settlements (factions at war with us) with position and
-- distance to our leader. Feeds attack target selection.
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
      -- NEUTRALS TOO. A faction we are not at war with can walk an army up to our border and
      -- declare later; excluding them meant the model never saw it coming. Same visibility gate.
      local neutral = fac and not at_war and not is_me
      if fac and (at_war or neutral) then
        local fname = try(function() return fac:name() end)
        local cl = try(function() return fac:character_list() end)
        local nc = cl and try(function() return cl:num_items() end) or 0
        for j = 0, nc - 1 do
          if #out >= 60 then break end
          local c = try(function() return cl:item_at(j) end)
          if c and try(function() return c:has_military_force() end) then
            -- ⚠ VISIBILITY GATE -- PASS THE FACTION KEY, NOT THE INTERFACE.
            -- is_visible_to_faction(<interface>) silently returns FALSE for every character. That
            -- is why this gate was tried before, appeared to null every enemy, and was reverted --
            -- the gate was never actually wrong, the argument type was. Verified live: with the KEY
            -- STRING exactly two hostiles returned true, matching the two the player could see on
            -- screen, while the interface form returned false for all 60+.
            -- An army we cannot see contributes NOTHING: not a count, not a position. Emitting a
            -- position for an unscouted stack is the same cheat as emitting its size.
            local vis = try(function() return c:is_visible_to_faction(myname) end)
            if vis == true then
              local x = try(function() return c:logical_position_x() end)
              local y = try(function() return c:logical_position_y() end)
              out[#out + 1] = { kind = (at_war and "army" or "neutral_army"), faction = or_null(fname),
                cqi = or_null(try(function() return c:command_queue_index() end)),
                visible = true,
                -- Troop count of a VISIBLE enemy stack is legitimate: the campaign banner's
                -- strength bar height IS the unit count (measured live -- a 13-unit army rendered
                -- a bar of h=13 against a frame of h=20, one pixel per unit of a full stack), so
                -- this is a number the player reads straight off the map.
                units = or_null(try(function() return c:military_force():unit_list():num_items() end)),
                stance = or_null(try(function() return c:military_force():active_stance() end)),
                -- province of a VISIBLE hostile/neutral, so province-level force counts can be
                -- built (how many of theirs vs ours are standing in the same province)
                province = or_null(try(function()
                  local r = c:region()
                  if r and not r:is_null_interface() then return r:province_name() end end)),
                x = or_null(x), y = or_null(y), dist = or_null(dist(x, y)) }
            end
          end
        end
        -- Settlements: HOSTILE ONLY. Neutral armies matter (they move, and they can declare war),
        -- but enumerating every neutral faction's settlements would flood the 60-entry budget with
        -- towns on the far side of the world and starve the entries that actually matter.
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
              -- garrison strength: the defender count an attack_settlement is actually up against.
              -- ⚠ unit_count() lives on the GARRISON RESIDENCE, not on its army. army() returns a
              -- NULL interface for an ordinary garrison (WH3's garrison proper is the armed
              -- citizenry), and calling any method on a null interface errors -- which is why
              -- garrison_residence():army():unit_list() silently yielded nil for every settlement.
              -- Garrison size is information the game shows the player, so no visibility gate here.
              units = or_null(try(function() return r:garrison_residence():unit_count() end)),
              x = or_null(x), y = or_null(y), dist = or_null(dist(x, y)) }
          end
        end
      end
    end
  end
  log({ seq = seq, cmd = "hostiles", count = #out, hostiles = out })
end

-- forces: ALL other factions' armies + settlements (not just enemies), each tagged with
-- at_war and distance to our leader. Superset of `hostiles`; needed to find at-PEACE
-- neighbours as declare-war-by-attack targets. Capped to keep the payload bounded.
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
  -- Only report forces within this radius of our leader. This bounds the payload by
  -- RELEVANCE (nearby forces we could actually reach) instead of a blind global cap that
  -- filled with distant peaceful factions and crowded out a nearby enemy. Enemies at war
  -- are always kept regardless of range so attack targets never get dropped.
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
      -- skip our own faction and dead/null factions
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

-- ------------------------------------------------------------------- polling
local last_seq = 0
local last_pos = 0       -- byte offset in commands.txt we have already read up to (tail cursor)

local function process()
  local f = io.open(CMD_PATH, "r")
  if not f then return end
  -- BYTE-OFFSET TAILING: read only the bytes appended since last poll (not the whole growing file),
  -- so POLL_SECONDS=0.1 stays cheap. A SHRINK (size < last_pos) unambiguously means commands.txt was
  -- cleared/rotated -> rewind to 0 and reprocess (this also catches a clear-then-refill to the same
  -- size, which a size-equality guard would miss).
  local size = f:seek("end")
  if size < last_pos then last_pos = 0 end
  if size == last_pos then f:close(); return end   -- nothing new
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
  -- ‼ TRUNCATION RESYNC (reliability fix): if the file's MAX seq is now BELOW last_seq, commands.txt
  -- was CLEARED/rotated (mode.py bus-clear, launcher reset, a new client run) while last_seq stayed
  -- high (persisted via saved_value). The client re-seeds its seq counter from the now-short file and
  -- sends LOW seqs -- which this loop would silently SKIP (seq <= last_seq), timing out EVERY read
  -- until the client happened to exceed the stale last_seq. Detect the drop and reset so the fresh
  -- low-seq commands process. Safe: a truncated file holds only post-clear commands, so nothing stale
  -- is replayed (that was the fresh-campaign concern the max_seq baseline already handles at arm time).
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
      last_seq = seq   -- unknown command: skip but do not stall
      log({ seq = seq, cmd = cmd, error = "unknown command" })
    end
  end
end

local POLL_MS = POLL_SECONDS * 1000   -- frontend timer works in milliseconds
local function poll()
  pcall(process)
  -- reschedule: campaign uses the model-time callback (cm); the FRONTEND has no cm, so use the
  -- real-time timer manager (core:get_tm()), which exists in every environment. real_callback is a
  -- one-shot, so we re-arm each poll (mirrors the cm:callback pattern).
  if cm then
    pcall(function() cm:callback(poll, POLL_SECONDS) end)
  else
    pcall(function() core:get_tm():real_callback(poll, POLL_MS, "twfrontend_poll") end)
  end
end

-- On a FRESH campaign there is no saved twcontrol_last_seq, so last_seq would be 0 and process()
-- would REPLAY every pre-existing line in commands.txt -- INCLUDING stale god-mode commands left by
-- a prior session or agent test (e.g. a cm:teleport_to that moved the player's lord on EVERY fresh
-- start -- a real bug). Baseline last_seq to the CURRENT MAX seq in the file instead, so a fresh
-- campaign IGNORES all pre-existing commands and only runs ones appended AFTER the mod armed.
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

-- ⚠ DEFEAT MUST BE RECORDED FROM INSIDE THE GAME, WHILE THE TICK IS STILL ALIVE.
-- When our last settlement falls the engine raises the Defeat modal, and a modal PAUSES the script
-- tick -- so the mod stops polling, every bus eval times out, and the advisor's own check
-- (campaign_state -> settlements <= 0) can never run because it needs a working bus. Measured: 8
-- campaigns ended this way and all 8 were filed as harness failures ("stuck"), which is the exact
-- opposite of the truth. Survival is part of the reward, so a lost campaign and a broken harness
-- must never share a label. The listener fires on the engine event, BEFORE the modal, which is the
-- only moment the fact is still observable.
local function arm_defeat_listener()
  if not (core and core.add_listener) then
    log({ cmd = "defeat_listener", armed = false, reason = "core:add_listener unavailable" })
    return
  end
  local ok = pcall(function()
    core:add_listener("twcontrol_faction_destroyed", "FactionDestroyed", true,
      function(context)
        local fn = try(function() return context:faction():name() end)
        local me = try(function() return human_faction():name() end)
        log({ cmd = "faction_destroyed", faction = fn, is_us = (fn ~= nil and fn == me),
              turn = turn() })
      end, true)
  end)
  -- Belt and braces: our own region count at every turn start. If the listener never fires (event
  -- name differs by patch), the last turn_start row still shows the count going to zero.
  pcall(function()
    core:add_listener("twcontrol_turn_start", "FactionTurnStart", true,
      function(context)
        local fn = try(function() return context:faction():name() end)
        local me = try(function() return human_faction():name() end)
        if fn ~= nil and fn == me then
          local n = try(function() return human_faction():region_list():num_items() end)
          log({ cmd = "turn_start", turn = turn(), regions = or_null(n) })
          if n == 0 then log({ cmd = "faction_destroyed", faction = me, is_us = true, turn = turn() }) end
        end
      end, true)
  end)
  log({ cmd = "defeat_listener", armed = ok })
end

-- EVENT/DILEMMA RECORDER: log every dilemma and incident the ENGINE issues, at issue time, with
-- its record key -- independent of any UI walk. A dilemma the interrupt handler cannot parse is
-- otherwise unidentifiable (its choice buttons are cloned templates carrying no key), and there
-- are ~900 dilemmas, so identifying broken ones by screenshot does not scale. Accessors are the
-- ones CA's own shipped scripts use on these events (context:dilemma(), context:choice(),
-- context:incident()); each is try()-wrapped so a patch renaming one logs NULL loudly instead of
-- killing the listener.
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
        log({ cmd = "incident_occured", turn = turn(),
              incident = or_null(try(function() return context:incident() end)),
              faction = or_null(try(function() return context:faction():name() end)) })
      end, true)
    -- wait signals: python tails these rows instead of sleeping/polling the UI
    core:add_listener("twcontrol_battle_completed", "BattleCompleted", true,
      function(context)
        log({ cmd = "battle_completed", turn = turn(),
              autoresolved = or_null(try(function()
                return context:model():pending_battle():has_been_autoresolved() end)) })
      end, true)
    core:add_listener("twcontrol_panel_opened", "PanelOpenedCampaign", true,
      function(context)
        log({ cmd = "panel", opened = true, turn = turn(),
              name = or_null(try(function() return context.string end)) })
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
local function start()
  if started then return end
  started = true
  pcall(arm_defeat_listener)
  pcall(arm_event_recorder)
  -- Skip the scripted campaign intro cinematics (story panel + narrated cindyscene +
  -- camera pan) to cut ~20s off new-campaign startup. Documented global lever; also try
  -- to skip any cutscene already running. Cosmetic only (not a gameplay cheat).
  pcall(function() cm:skip_all_campaign_cutscenes() end)
  pcall(function() if cm:is_intro_cutscene_playing() then cm:skip_all_campaign_cutscenes() end end)
  -- Continuing campaign: use the saved seq (skips already-run commands). FRESH campaign (no saved
  -- value): baseline to the file's current max so stale/leftover commands are NOT replayed.
  local saved = try(function() return cm:get_saved_value("twcontrol_last_seq") end)
  last_seq = saved or max_seq_in_file()
  log({ cmd = "started", turn = turn(), last_seq = last_seq, fresh = (saved == nil),
        ui = (find_uicomponent ~= nil), cmd_path = CMD_PATH })
  say("controller running, last_seq=" .. last_seq .. " -> polling " .. CMD_PATH)
  pcall(function() cm:callback(poll, POLL_SECONDS) end)
end

-- FRONTEND arming: no cm, no saved_value, no cutscene-skip. Baseline last_seq to the file max (skip
-- pre-existing commands) and kick off the real-time poll. Logs "frontend_armed" (NOT "started", so the
-- launcher's campaign-loaded wait is not falsely tripped by the menu bus).
local function start_frontend()
  if started then return end
  started = true
  last_seq = max_seq_in_file()
  log({ cmd = "frontend_armed", last_seq = last_seq, ui = (find_uicomponent ~= nil), cmd_path = CMD_PATH })
  poll()
end

function twcontrol() start() end
if cm and cm.add_first_tick_callback then
  pcall(function() cm:add_first_tick_callback(start) end)
else
  -- FrontEnd environment (cm is nil): arm the bus immediately when this file loads.
  pcall(start_frontend)
end
