from __future__ import annotations

import json
import os
import shutil
import sys
import time

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

SAVE_WAIT_S = 30.0
SAVE_POLL_S = 0.25
MENU_SETTLE_S = 0.6
PLAYABLE_TIMEOUT_S = 90
HUD_TIMEOUT_S = 60
QUIT_TIMEOUT_S = 45
QUIT_POLL_S = 0.4
QUIT_RETRY_S = 8.0
CLEAR_POLL_S = 0.35
WORLD_TIMEOUT_S = 45.0

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


def world_state(bus, timeout=WORLD_TIMEOUT_S):
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
    t0 = time.time()
    blocking = []
    log("  clear: up to %d rounds, %.2fs apart" % (rounds, CLEAR_POLL_S))
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
        log("  clear %d/%d %.1fs: steps=%s blocking=%s"
            % (i + 1, rounds, time.time() - t0, steps or "none", blocking or "none"))
        if not blocking:
            log("  clear: screen clear after %.1fs" % (time.time() - t0))
            return roots
        try:
            shut = nav.close_popups(bus)
        except Exception as e:
            log("  clear: close_popups raised %s" % repr(e)[:120])
            shut = []
        log("  clear %d/%d %.1fs: close_popups dismissed %d -- %s"
            % (i + 1, rounds, time.time() - t0, len(shut), shut[:4] or "nothing"))
        common.wait("clear_screen_retry", CLEAR_POLL_S, "%d/%d" % (i + 1, rounds))
    raise RuntimeError(
        "bake_starts: screen still blocked after %.1fs and %d rounds of resolve + "
        "close_popups: %s -- refusing to click blind"
        % (time.time() - t0, rounds, blocking))


def end_campaign_tour(bus, log=print):
    r = bus.send("eval", _LUA_END_CAMPAIGN_TOUR, timeout=5.0) or {}
    if r.get("error"):
        raise RuntimeError("bake_starts: could not end campaign tour -- %s" % r["error"])
    log("  campaign tour ended: %s" % (r.get("result") or "ok"))
    return r


def prepare_for_trim(bus, log=print):
    end_campaign_tour(bus, log=log)
    clear_screen(bus, log=log)
    end_campaign_tour(bus, log=log)
    return clear_screen(bus, log=log)


def game_is_alive(bus):
    try:
        bus.send("roots", "", timeout=3)
        return True
    except Exception:
        import bus as _bus_mod
        return _bus_mod._game_alive()


def save_via_clicks(bus, ex, target_name, log=print):
    clear_screen(bus, log=log)
    roots = [k.get("id") for k in (bus.send("roots", "", timeout=15) or {}).get("kids", [])
             if k.get("visible")]
    log("  save: visible roots before the menu -- %s" % (roots or "none"))
    for p in [r for r in roots if r not in nav.BASE_ROOTS]:
        log("  save: %s is open over the menu coordinates -> %s"
            % (p, "closed" if nav.close_panel(bus, p) else "WOULD NOT CLOSE"))
    left = [k.get("id") for k in (bus.send("roots", "", timeout=15) or {}).get("kids", [])
            if k.get("visible") and k.get("id") not in nav.BASE_ROOTS]
    if left:
        raise RuntimeError(
            "bake_starts: %s still open over the save menu. %s and %s are screen "
            "coordinates, so a panel on top of them swallows the click and the save "
            "never happens -- refusing to click blind through it"
            % (left, MENU_SAVE_XY, CONFIRM_SAVE_XY))
    before = set(os.listdir(B.save_dir()))
    r = bus.send("click", MENU_BUTTON, timeout=25) or {}
    if not r.get("clicked"):
        raise RuntimeError("bake_starts: menu button did not click -> %s" % json.dumps(r)[:200])
    log("  save: menu open, settling %.2fs then clicking save + confirm" % MENU_SETTLE_S)
    time.sleep(MENU_SETTLE_S)
    HW.click(*MENU_SAVE_XY, settle=0.5)
    HW.click(*CONFIRM_SAVE_XY, settle=0.5)

    t0 = time.time()
    new, src = [], None
    while time.time() - t0 < SAVE_WAIT_S:
        new = [f for f in os.listdir(B.save_dir()) if f not in before]
        real = sorted(f for f in new
                      if f.lower().endswith(".save") and not B._is_decoy(f))
        if real and not any(B._is_decoy(f) for f in new):
            src = real[0]
            log("  save: %r finished after %.1fs" % (src, time.time() - t0))
            break
        log("  save: %.1fs -- %s" % (time.time() - t0, new or "nothing yet"))
        time.sleep(SAVE_POLL_S)
    if src is None:
        raise RuntimeError(
            "bake_starts: no finished save within %.0fs (saw %s) -- a .save still twinned "
            "by its .save.save staging copy has not been written yet" % (SAVE_WAIT_S, new))
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


def exit_to_main_menu(bus, log=print, timeout=QUIT_TIMEOUT_S, retry_every=QUIT_RETRY_S):
    HW.click(*MENU_EXIT_MAIN_XY, settle=0.5)
    HW.click(*CONFIRM_EXIT_XY, settle=0.8)
    t0 = time.time()
    last = time.time()
    polls = 0
    log("  exit: waiting up to %.0fs for %s, polling every %.2fs"
        % (timeout, "/".join(FRONTEND_ROOTS), QUIT_POLL_S))
    while time.time() - t0 < timeout:
        try:
            roots = [k.get("id") for k in (bus.send("roots", "", timeout=10) or {}).get("kids", [])
                     if k.get("visible")]
        except Exception:
            roots = None
        polls += 1
        if roots and any(r in FRONTEND_ROOTS for r in roots):
            log("  exit: at the main menu after %.1fs on poll %d (%s)"
                % (time.time() - t0, polls, [r for r in roots if r in FRONTEND_ROOTS]))
            return True
        log("  exit: %.1fs poll %d -- %s" % (time.time() - t0, polls, roots or "no reply"))
        if time.time() - last >= retry_every:
            last = time.time()
            log("  exit: %.1fs -- re-issuing exit + confirm" % (time.time() - t0))
            HW.click(*MENU_EXIT_MAIN_XY, settle=0.5)
            HW.click(*CONFIRM_EXIT_XY, settle=0.8)
        time.sleep(QUIT_POLL_S)
    raise RuntimeError(
        "bake_starts: never reached the main menu within %.0fs (%d polls) after exit + confirm"
        % (timeout, polls))


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
    skip = {s.strip() for s in (arg("--skip", "") or "").split(",") if s.strip()}
    out_path = arg("--out")

    import bus_launcher
    bl = bus_launcher.BusLauncher()
    todo = []
    for campaign in campaigns:
        campaign_map = bl.CAMPAIGN_KEYS.get(campaign, campaign)
        roster = sorted(bl.startable_factions(campaign))
        done = have_already(campaign_map, radius, turn)
        mine = [f for f in roster if f not in done and f not in skip]
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
