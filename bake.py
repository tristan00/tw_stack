from __future__ import annotations

import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import presaves as P

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)

import hw_input as HW
import interrupts as I
import nav

MENU_BUTTON = "menu_bar|buttongroup|button_menu"
MENU_SAVE_XY = (181, 530)
CONFIRM_SAVE_XY = (1120, 1342)
MENU_EXIT_MAIN_XY = (183, 987)
CONFIRM_EXIT_XY = (1242, 842)
FRONTEND_ROOTS = ("hud_frontend", "campaign_select_new", "sp_frame", "main")

TRIM_TIMEOUT_S = 120.0
SAVE_WAIT_S = 30.0
SAVE_POLL_S = 0.25
MENU_SETTLE_S = 0.6
PLAYABLE_TIMEOUT_S = 90
HUD_TIMEOUT_S = 60
QUIT_TIMEOUT_S = 45
QUIT_POLL_S = 0.4
QUIT_RETRY_S = 8.0
CLEAR_POLL_S = 0.35

_LUA_END_CAMPAIGN_TOUR = "cm:skip_all_campaign_cutscenes() return 'ok'"


def end_campaign_tour(bus, log=print):
    r = bus.send("eval", _LUA_END_CAMPAIGN_TOUR, timeout=5.0) or {}
    if r.get("error"):
        raise RuntimeError("bake: could not end campaign tour -- %s" % r["error"])
    log("  campaign tour ended: %s" % (r.get("result") or "ok"))
    return r


def _bus():
    from bus import Bus
    return Bus()


def trim(bus, radius, dry=False):
    payload = ("%g dry" % float(radius)) if dry else ("%g" % float(radius))
    r = bus.send("trim", payload, timeout=TRIM_TIMEOUT_S) or {}
    if r.get("error"):
        raise RuntimeError("bake: trim refused -- %s" % r["error"])
    for k in ("n_killed", "n_kept", "n_unplaced", "n_failed"):
        if r.get(k) is None:
            raise RuntimeError(
                "bake: trim returned no %s. A trim that cannot say how many "
                "factions it removed is not a result: %s" % (k, json.dumps(r)[:300]))
    if r["n_failed"]:
        raise RuntimeError(
            "bake: disarming failed on %d of %d factions (%s). Refusing to "
            "save a world that is trimmed differently from what the name will claim."
            % (r["n_failed"], r["n_failed"] + r["n_killed"],
               ", ".join(r.get("failed") or [])[:200]))
    return r


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
        "bake: screen still blocked after %.1fs and %d rounds of resolve + "
        "close_popups: %s -- refusing to click blind"
        % (time.time() - t0, rounds, blocking))


def game_is_alive(bus):
    try:
        bus.send("roots", "", timeout=3)
        return True
    except Exception:
        import bus as _bus_mod
        return _bus_mod._game_alive()


def _is_decoy(fname):
    stem, ext = os.path.splitext(fname)
    return ext == ".save" and stem.endswith(".save")


def save_via_clicks(bus, target_name, log=print):
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
            "bake: %s still open over the save menu. %s and %s are screen "
            "coordinates, so a panel on top of them swallows the click and the save "
            "never happens -- refusing to click blind through it"
            % (left, MENU_SAVE_XY, CONFIRM_SAVE_XY))
    before = set(os.listdir(P.save_dir()))
    r = bus.send("click", MENU_BUTTON, timeout=25) or {}
    if not r.get("clicked"):
        raise RuntimeError("bake: menu button did not click -> %s" % json.dumps(r)[:200])
    log("  save: menu open, settling %.2fs then clicking save + confirm" % MENU_SETTLE_S)
    time.sleep(MENU_SETTLE_S)
    HW.click(*MENU_SAVE_XY, settle=0.5)
    HW.click(*CONFIRM_SAVE_XY, settle=0.5)

    t0 = time.time()
    new, src = [], None
    while time.time() - t0 < SAVE_WAIT_S:
        new = [f for f in os.listdir(P.save_dir()) if f not in before]
        real = sorted(f for f in new
                      if f.lower().endswith(".save") and not _is_decoy(f))
        if real and not any(_is_decoy(f) for f in new):
            src = real[0]
            log("  save: %r finished after %.1fs" % (src, time.time() - t0))
            break
        log("  save: %.1fs -- %s" % (time.time() - t0, new or "nothing yet"))
        time.sleep(SAVE_POLL_S)
    if src is None:
        raise RuntimeError(
            "bake: no finished save within %.0fs (saw %s) -- a .save still twinned "
            "by its .save.save staging copy has not been written yet" % (SAVE_WAIT_S, new))
    want = target_name + ".save"
    sp = os.path.join(P.save_dir(), src)
    dp = os.path.join(P.save_dir(), want)
    if os.path.exists(dp):
        os.remove(dp)
    os.replace(sp, dp)
    for f in new:
        if f == src:
            continue
        stray = os.path.join(P.save_dir(), f)
        if _is_decoy(f) and os.path.exists(stray):
            os.remove(stray)
    if not os.path.isfile(dp) or os.path.getsize(dp) <= 0:
        raise RuntimeError("bake: renamed save missing or empty: %s" % dp)
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
        "bake: never reached the main menu within %.0fs (%d polls) after exit + confirm"
        % (timeout, polls))


def backup(save_file, log=print):
    src = os.path.join(P.save_dir(), save_file)
    dst = os.path.join(P.presave_dir(), save_file)
    shutil.copy2(src, dst)
    if os.path.getsize(dst) != os.path.getsize(src):
        raise RuntimeError("bake: backup size mismatch for %s" % save_file)
    log("  backed up -> %s" % dst)
    return dst


def have_already(campaign_map, radius, turn):
    return {p["faction"] for p in P.list_presaves(radius=radius,
                                                  campaign_map=campaign_map, turn=turn)}


def bake_start(bl, bus, campaign, campaign_map, faction, radius, turn, log=print):
    if not game_is_alive(bus):
        log("  launching %s (cold boot)" % faction)
        started = bl.launch(faction, campaign, load_timeout=PLAYABLE_TIMEOUT_S,
                            hud_timeout=HUD_TIMEOUT_S)
    else:
        bl.bus = bl.bus or bus
        if not bl._wait_bus_ready():
            raise RuntimeError("game process is alive but the bus never answered")
        roots = [k.get("id") for k in (bus.send("roots", "", timeout=15) or {}).get("kids", [])
                 if k.get("visible")]
        if not any(r in FRONTEND_ROOTS for r in roots):
            raise RuntimeError(
                "bake: the game is alive but not at the frontend (%s) -- a failed bake "
                "must kill the game, not leave a campaign to salvage" % (roots or "no roots"))
        log("  starting %s from the frontend" % faction)
        started = bl.start_campaign(faction, campaign, load_timeout=PLAYABLE_TIMEOUT_S,
                                    hud_timeout=HUD_TIMEOUT_S)
    if not started:
        raise RuntimeError("never reached a playable campaign")

    end_campaign_tour(bus, log=log)
    clear_screen(bus, log=log)
    end_campaign_tour(bus, log=log)
    clear_screen(bus, log=log)

    t0 = time.time()
    r = trim(bus, radius)
    log("  trim reported: killed=%d kept=%d unplaced=%d (%.1fs)"
        % (r["n_killed"], r["n_kept"], r["n_unplaced"], time.time() - t0))

    clear_screen(bus, log=log)
    common.wait("bake_clear_settle", 2.0, faction)
    clear_screen(bus, log=log)

    name = P.save_name(campaign_map, faction, radius, turn)
    save_file = save_via_clicks(bus, name, log=log)
    backup(save_file, log=log)
    exit_to_main_menu(bus, log=log)
    return {"faction": faction, "save": save_file, "trim": r}


def cmd_bake(argv):
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
    only = [k.strip() for k in (arg("--factions", "") or "").split(",") if k.strip()]
    out_path = arg("--out")

    import bus_launcher
    bl = bus_launcher.BusLauncher()
    todo = []
    for campaign in campaigns:
        campaign_map = bl.CAMPAIGN_KEYS.get(campaign, campaign)
        roster = sorted(bl.startable_factions(campaign))
        done = have_already(campaign_map, radius, turn)
        mine = [f for f in roster if f not in done and f not in skip
                and (not only or f in only)]
        todo += [(campaign, campaign_map, f) for f in mine]
        print("bake: %s (%s) r=%g t=%d" % (campaign, campaign_map, radius, turn))
        print("  roster %d, already baked %d, to do %d" % (len(roster), len(done), len(mine)))
    if limit:
        todo = todo[:limit]
    if not todo:
        print("  nothing to do")
        return 0

    sys.path.insert(0, common.ADVISOR)
    import interrupt_model as IM
    ranker = IM.InterruptRanker(common.MODEL_COLD_START, strategies={"random": 1.0})
    I.reset_answers()
    I.set_chooser(lambda screen, options, campaign, panel=None, record=None, meta=None:
                  ranker.choose(screen, options, dict(campaign or {}), panel, record, meta))

    bus = _bus()
    baked, failed = [], []
    for i, (campaign, campaign_map, faction) in enumerate(todo):
        print("\n=== %d/%d  %s (%s)" % (i + 1, len(todo), faction, campaign_map))
        try:
            baked.append(bake_start(bl, bus, campaign, campaign_map, faction,
                                    radius, turn))
        except Exception as e:
            print("  !! FAILED: %s" % repr(e)[:300])
            failed.append({"faction": faction, "campaign_map": campaign_map,
                           "error": repr(e)[:300]})
            from executor import Executor
            Executor(bus).kill_game()
        if out_path:
            json.dump({"campaigns": campaigns, "radius": radius, "turn": turn,
                       "baked": baked, "failed": failed},
                      open(out_path, "w", encoding="utf-8"), indent=1, default=str)
    print("\nbake: %d baked, %d failed of %d" % (len(baked), len(failed), len(todo)))
    return 0 if not failed else 2


def cmd_status(argv):
    def arg(n, d=None):
        return argv[argv.index(n) + 1] if n in argv else d

    radius = float(arg("--radius", "150"))
    turn = int(arg("--turn", "1"))
    import bus_launcher
    bl = bus_launcher.BusLauncher()
    rc = 0
    for campaign in ("Immortal Empires", "Realm of Chaos"):
        campaign_map = bl.CAMPAIGN_KEYS[campaign]
        roster = set(bl.startable_factions(campaign))
        have = have_already(campaign_map, radius, turn)
        missing = sorted(roster - have)
        print("%s (%s) r=%g t=%d: baked %d of %d"
              % (campaign, campaign_map, radius, turn, len(have), len(roster)))
        if missing:
            rc = 2
            print("  missing (%d): %s" % (len(missing), ", ".join(missing)))
    return rc


def main(argv):
    cmds = {"bake": cmd_bake, "status": cmd_status}
    if not argv or argv[0] not in cmds:
        raise SystemExit("usage: bake.py bake|status ...\n"
                         "  bake   --radius R [--campaign a,b] [--factions k,k] [--turn N]"
                         " [--limit N] [--skip k,k] [--out path]\n"
                         "  status [--radius R] [--turn N]")
    return cmds[argv[0]](argv[1:])


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
