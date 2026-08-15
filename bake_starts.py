from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)

import bake_saves as B
import hw_input as HW
import interrupts as I
import nav

MENU_BUTTON = "menu_bar|buttongroup|button_menu"
MENU_SAVE_XY = (181, 530)
CONFIRM_SAVE_XY = (1120, 1342)
MENU_RESUME_XY = (183, 379)
MENU_EXIT_MAIN_XY = (183, 987)
CONFIRM_EXIT_XY = (1242, 842)
FRONTEND_ROOTS = ("hud_frontend", "campaign_select_new", "sp_frame", "main")

UNKILLABLE = frozenset((
    "wh3_dlc24_tze_the_deceivers",
))

SAVE_WAIT_S = 60.0
MENU_SETTLE_S = 1.6
PLAYABLE_TIMEOUT_S = 300
HUD_TIMEOUT_S = 200
QUIT_TIMEOUT_S = 120

_LUA_END_CAMPAIGN_TOUR = "cm:skip_all_campaign_cutscenes() return 'ok'"

_LUA_WORLD = (
    "local f=cm:get_local_faction(true) if not f then return 'NO-FACTION' end "
    "local me=f:name() "
    "local function pos(g) "
    "  local ok,x=pcall(function() return g:faction_leader():logical_position_x() end) "
    "  local ok2,y=pcall(function() return g:faction_leader():logical_position_y() end) "
    "  if ok and ok2 and x and y then return x,y end "
    "  local okr,rl=pcall(function() return g:region_list() end) "
    "  if okr and rl then for i=0,rl:num_items()-1 do "
    "    local okk,r=pcall(function() return rl:item_at(i) end) "
    "    if okk and r then local oks,s=pcall(function() return r:settlement() end) "
    "      if oks and s then local a,b=s:logical_position_x(),s:logical_position_y() "
    "        if a and b then return a,b end end end end end "
    "  return nil,nil end "
    "local ox,oy=pos(f) if not ox then return 'NO-ORIGIN' end "
    "local out={} "
    "local fl=cm:model():world():faction_list() "
    "for i=0,fl:num_items()-1 do "
    "  local g=fl:item_at(i) local n=g:name() "
    "  if n~=me then "
    "    local dead=0 local okd,d=pcall(function() return g:is_dead() end) "
    "    if okd and d then dead=1 end "
    "    local nr=0 local okr,rl=pcall(function() return g:region_list() end) "
    "    if okr and rl then nr=rl:num_items() end "
    "    local nc=0 local okc,cl=pcall(function() return g:character_list() end) "
    "    if okc and cl then nc=cl:num_items() end "
    "    local gx,gy=pos(g) "
    "    local dist=-1 "
    "    if gx and gy then dist=math.sqrt((gx-ox)^2+(gy-oy)^2) end "
    "    out[#out+1]=n..','..string.format('%.1f',dist)..','..nr..','..nc..','..dead "
    "  end end "
    "return table.concat(out,';')")


def world_state(bus, timeout=120.0):
    raw = bus.send("eval", _LUA_WORLD, timeout=timeout) or {}
    txt = raw.get("result")
    if not txt or txt in ("NO-FACTION", "NO-ORIGIN"):
        raise RuntimeError("bake_starts: world read failed -> %r" % txt)
    rows = []
    for part in str(txt).split(";"):
        if not part.strip():
            continue
        bits = part.split(",")
        if len(bits) != 5:
            continue
        rows.append({"faction": bits[0], "dist": float(bits[1]),
                     "regions": int(bits[2]), "chars": int(bits[3]),
                     "dead": bits[4] == "1"})
    if not rows:
        raise RuntimeError("bake_starts: world read returned no factions")
    return rows


def verify_trim(bus, radius, log=print):
    rows = world_state(bus)
    placed = [r for r in rows if r["dist"] >= 0]
    outside = [r for r in placed if r["dist"] > radius]
    inside = [r for r in placed if r["dist"] <= radius]
    survivors = [r for r in outside
                 if not r["dead"] and (r["regions"] > 0 or r["chars"] > 0)
                 and r["faction"] not in UNKILLABLE]
    exempt = [r for r in outside
              if r["faction"] in UNKILLABLE and (r["regions"] > 0 or r["chars"] > 0)]
    gutted_inside = [r for r in inside
                     if not r["dead"] and r["regions"] == 0 and r["chars"] == 0]
    log("  verify: %d factions read, %d placed, %d inside r=%g, %d outside, %d exempt"
        % (len(rows), len(placed), len(inside), radius, len(outside), len(exempt)))
    for e in exempt:
        log("  verify: exempt %s (d=%.0f, r=%d, c=%d) -- holds no settlements to lose"
            % (e["faction"], e["dist"], e["regions"], e["chars"]))
    if survivors:
        raise RuntimeError(
            "bake_starts: trim did NOT hold. %d factions beyond r=%g still own regions or "
            "characters: %s" % (len(survivors), radius,
                                ", ".join("%s(d=%.0f,r=%d,c=%d)"
                                          % (s["faction"], s["dist"], s["regions"],
                                             s["chars"]) for s in survivors[:6])))
    if not inside:
        raise RuntimeError(
            "bake_starts: nothing survived inside r=%g -- a world with no neighbours is "
            "not a usable start" % radius)
    log("  verify: PASS -- every faction beyond r=%g holds 0 regions and 0 characters "
        "(%d gutted inside the radius, which is allowed)" % (radius, len(gutted_inside)))
    return {"n_read": len(rows), "n_inside": len(inside), "n_outside": len(outside),
            "n_gutted_inside": len(gutted_inside), "n_exempt": len(exempt),
            "exempt": [e["faction"] for e in exempt]}


def clear_screen(bus, log=print, rounds=3):
    for i in range(rounds):
        try:
            steps = I.resolve(bus)
        except Exception as e:
            log("  clear: resolve raised %s" % repr(e)[:120])
            steps = None
        roots = [k.get("id") for k in (bus.send("roots", "", timeout=15) or {}).get("kids", [])
                 if k.get("visible")]
        blocking = [r for r in roots
                    if r not in nav.BASE_ROOTS and r not in nav.BENIGN_PANELS]
        log("  clear %d: steps=%s blocking=%s" % (i + 1, steps or "none", blocking or "none"))
        if not blocking:
            return roots
        log("%s  sleep 1.0s start -- clear_screen round %d blocked by %s"
            % (datetime.now().strftime("%H:%M:%S.%f")[:-3], i + 1, blocking))
        time.sleep(1.0)
        log("%s  sleep 1.0s done -- clear_screen"
            % datetime.now().strftime("%H:%M:%S.%f")[:-3])
    raise RuntimeError(
        "bake_starts: screen still blocked before save: %s -- refusing to click blind"
        % blocking)


def end_campaign_tour(bus, log=print):
    """End the game's opening campaign tour; blocker handling is deliberately separate."""
    r = bus.send("eval", _LUA_END_CAMPAIGN_TOUR, timeout=5.0) or {}
    if r.get("error"):
        raise RuntimeError("bake_starts: could not end campaign tour -- %s" % r["error"])
    log("  campaign tour ended: %s" % (r.get("result") or "ok"))
    return r


def prepare_for_trim(bus, log=print):
    """Stabilize either campaign map before mutating its world state."""
    end_campaign_tour(bus, log=log)
    clear_screen(bus, log=log)
    end_campaign_tour(bus, log=log)
    return clear_screen(bus, log=log)


def game_is_alive(bus):
    """Prefer the live bus; Windows may deny tasklist to a background controller."""
    try:
        bus.send("roots", "", timeout=3)
        return True
    except Exception:
        import bus as _bus_mod
        return _bus_mod._game_alive()


def save_via_clicks(bus, ex, target_name, log=print):
    clear_screen(bus, log=log)
    before = set(os.listdir(B.save_dir()))
    r = bus.send("click", MENU_BUTTON, timeout=25) or {}
    if not r.get("clicked"):
        raise RuntimeError("bake_starts: menu button did not click -> %s" % json.dumps(r)[:200])
    time.sleep(MENU_SETTLE_S)
    HW.click(*MENU_SAVE_XY, settle=1.5)
    HW.click(*CONFIRM_SAVE_XY, settle=1.5)

    t0 = time.time()
    new = []
    while time.time() - t0 < SAVE_WAIT_S:
        new = [f for f in os.listdir(B.save_dir()) if f not in before]
        if new:
            break
        time.sleep(1.0)
    if not new:
        raise RuntimeError("bake_starts: no save file appeared within %.0fs" % SAVE_WAIT_S)

    real = [f for f in new if f.lower().endswith(".save") and not B._is_decoy(f)]
    if not real:
        raise RuntimeError("bake_starts: only decoy files appeared: %s" % new)
    src = real[0]
    want = target_name + ".save"
    sp = os.path.join(B.save_dir(), src)
    dp = os.path.join(B.save_dir(), want)
    if os.path.exists(dp):
        os.remove(dp)
    os.replace(sp, dp)
    for f in new:
        if f == src:
            continue
        stray = os.path.join(B.save_dir(), f)
        if B._is_decoy(f) and os.path.exists(stray):
            os.remove(stray)
    if not os.path.isfile(dp) or os.path.getsize(dp) <= 0:
        raise RuntimeError("bake_starts: renamed save missing or empty: %s" % dp)
    log("  saved %r -> %s (%.1f MB)" % (src, want, os.path.getsize(dp) / 1e6))
    return want


def exit_to_main_menu(bus, log=print, timeout=QUIT_TIMEOUT_S, retry_every=25.0):
    HW.click(*MENU_EXIT_MAIN_XY, settle=1.5)
    HW.click(*CONFIRM_EXIT_XY, settle=2.5)
    t0 = time.time()
    last = time.time()
    while time.time() - t0 < timeout:
        try:
            roots = [k.get("id") for k in (bus.send("roots", "", timeout=10) or {}).get("kids", [])
                     if k.get("visible")]
        except Exception:
            roots = None
        if roots and any(r in FRONTEND_ROOTS for r in roots):
            log("  at the main menu after %.0fs (%s)"
                % (time.time() - t0, [r for r in roots if r in FRONTEND_ROOTS]))
            return True
        if time.time() - last >= retry_every:
            last = time.time()
            log("  still not at the main menu after %.0fs, re-issuing exit + confirm"
                % (time.time() - t0))
            HW.click(*MENU_EXIT_MAIN_XY, settle=1.5)
            HW.click(*CONFIRM_EXIT_XY, settle=2.5)
        time.sleep(2.0)
    raise RuntimeError(
        "bake_starts: never reached the main menu within %.0fs after exit + confirm" % timeout)


def backup(save_file, log=print):
    src = os.path.join(B.save_dir(), save_file)
    dst = os.path.join(B.presave_dir(), save_file)
    shutil.copy2(src, dst)
    if os.path.getsize(dst) != os.path.getsize(src):
        raise RuntimeError("bake_starts: backup size mismatch for %s" % save_file)
    log("  backed up -> %s" % dst)
    return dst


def have_already(campaign_map, radius, turn):
    out = set()
    for where in ("archive", "saves"):
        try:
            for p in B.list_presaves(radius=radius, campaign_map=campaign_map,
                                     turn=turn, where=where):
                out.add(p["faction"])
        except Exception:
            pass
    return out


def bake_start(bl, bus, ex, campaign, campaign_map, faction, radius, turn, log=print):
    alive = game_is_alive(bus)
    if not alive:
        log("  launching %s (cold boot)" % faction)
        started = bl.launch(faction, campaign, load_timeout=PLAYABLE_TIMEOUT_S,
                            hud_timeout=HUD_TIMEOUT_S)
    else:
        bl.bus = bl.bus or bus
        if not bl._wait_bus_ready():
            raise RuntimeError("game process is alive but the bus never answered")
        roots = [k.get("id") for k in (bus.send("roots", "", timeout=15) or {}).get("kids", [])
                 if k.get("visible")]
        if any(r in FRONTEND_ROOTS for r in roots):
            log("  starting %s from the frontend" % faction)
            started = bl.start_campaign(faction, campaign, load_timeout=PLAYABLE_TIMEOUT_S,
                                        hud_timeout=HUD_TIMEOUT_S)
        else:
            log("  restarting into %s from a live campaign" % faction)
            started = bl.restart_campaign(faction, campaign, load_timeout=PLAYABLE_TIMEOUT_S,
                                          hud_timeout=HUD_TIMEOUT_S,
                                          quit_timeout=QUIT_TIMEOUT_S)
    if not started:
        raise RuntimeError("never reached a playable campaign")

    prepare_for_trim(bus, log=log)

    t0 = time.time()
    r = B.trim(bus, radius)
    log("  trim reported: killed=%d kept=%d unplaced=%d (%.1fs)"
        % (r["n_killed"], r["n_kept"], r["n_unplaced"], time.time() - t0))
    v = verify_trim(bus, radius, log=log)

    name = B.save_name(campaign_map, faction, radius, turn)
    save_file = save_via_clicks(bus, ex, name, log=log)
    backup(save_file, log=log)
    exit_to_main_menu(bus, log=log)
    return {"faction": faction, "save": save_file, "trim": r, "verify": v}


def main(argv):
    def arg(n, d=None):
        return argv[argv.index(n) + 1] if n in argv else d

    campaigns = [c.strip() for c in
                 arg("--campaign", "Immortal Empires,Realm of Chaos").split(",") if c.strip()]
    radius_arg = arg("--radius")
    if radius_arg is None:
        raise SystemExit("--radius is required; refusing to trim with an implicit radius")
    radius = float(radius_arg)
    turn = int(arg("--turn", "1"))
    limit = int(arg("--limit", "0") or 0)
    out_path = arg("--out")

    import bus_launcher
    bl = bus_launcher.BusLauncher()
    todo = []
    for campaign in campaigns:
        campaign_map = bl.CAMPAIGN_KEYS.get(campaign, campaign)
        roster = sorted(bl.startable_factions(campaign))
        done = have_already(campaign_map, radius, turn)
        mine = [f for f in roster if f not in done]
        todo += [(campaign, campaign_map, f) for f in mine]
        print("bake_starts: %s (%s) r=%g t=%d" % (campaign, campaign_map, radius, turn))
        print("  roster %d, already baked %d, to do %d" % (len(roster), len(done), len(mine)))
    if limit:
        todo = todo[:limit]
    if not todo:
        print("  nothing to do")
        return 0

    bus = B._bus()
    import executor as EX
    ex = EX.Executor(bus) if hasattr(EX, 'Executor') else None
    baked, failed = [], []
    for i, (campaign, campaign_map, faction) in enumerate(todo):
        print("\n=== %d/%d  %s (%s)" % (i + 1, len(todo), faction, campaign_map))
        try:
            baked.append(bake_start(bl, bus, ex, campaign, campaign_map, faction,
                                    radius, turn))
        except Exception as e:
            print("  !! FAILED: %s" % repr(e)[:300])
            failed.append({"faction": faction, "campaign_map": campaign_map,
                           "error": repr(e)[:300]})
        if out_path:
            json.dump({"campaigns": campaigns, "radius": radius, "turn": turn,
                       "baked": baked, "failed": failed},
                      open(out_path, "w", encoding="utf-8"), indent=1, default=str)
    print("\nbake_starts: %d baked, %d failed of %d" % (len(baked), len(failed), len(todo)))
    return 0 if not failed else 2


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
