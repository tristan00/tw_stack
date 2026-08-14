from __future__ import annotations


import json
import os
import re
import sys
import time

import common

sys.path.insert(0, common.LAUNCHER)
sys.path.insert(0, os.path.join(common.ROOT, "bus"))

DEFAULT_RADIUS = 240.0
SAVE_SETTLE_S = 90.0
TRIM_TIMEOUT_S = 120.0
PLAYABLE_TIMEOUT_S = 240.0


def save_dir():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError(
            "bake_saves: APPDATA is not set, so the save folder cannot be located. "
            "Warhammer 3 writes saves under %APPDATA%\\The Creative Assembly.")
    d = os.path.join(appdata, "The Creative Assembly", "Warhammer3", "save_games")
    if not os.path.isdir(d):
        raise RuntimeError(
            "bake_saves: no save folder at %s. Save one campaign by hand once so the "
            "game creates it, rather than having this script guess another location." % d)
    return d


def save_name(campaign_map, faction, radius, kept, turn):
    return "trim__%s__%s__r%g__keep%d__t%d" % (
        campaign_map, faction, float(radius), int(kept), int(turn))


def parse_save_name(name):
    m = re.match(r"^trim__(.+?)__(.+?)__r([0-9.]+)__keep(\d+)__t(\d+)$",
                 os.path.splitext(os.path.basename(name))[0])
    if not m:
        return None
    return {"campaign_map": m.group(1), "faction": m.group(2),
            "radius": float(m.group(3)), "kept": int(m.group(4)),
            "turn": int(m.group(5))}


def _bus():
    from bus import Bus
    return Bus()


def game_is_up(bus):
    try:
        r = bus.send("eval", "cm and cm:turn_number()", timeout=15.0) or {}
    except Exception:
        return False
    return r.get("error") in (None, "null") and r.get("result") is not None


def trim(bus, radius, dry=False):
    payload = ("%g dry" % float(radius)) if dry else ("%g" % float(radius))
    r = bus.send("trim", payload, timeout=TRIM_TIMEOUT_S) or {}
    if r.get("error"):
        raise RuntimeError("bake_saves: trim refused -- %s" % r["error"])
    for k in ("n_killed", "n_kept", "n_unplaced", "n_failed"):
        if r.get(k) is None:
            raise RuntimeError(
                "bake_saves: trim returned no %s. A trim that cannot say how many "
                "factions it removed is not a result: %s" % (k, json.dumps(r)[:300]))
    if r["n_failed"]:
        raise RuntimeError(
            "bake_saves: cm:kill_faction failed on %d of %d factions (%s). Refusing to "
            "save a world that is trimmed differently from what the name will claim."
            % (r["n_failed"], r["n_failed"] + r["n_killed"],
               ", ".join(r.get("failed") or [])[:200]))
    return r


def save_campaign(bus, name, timeout=SAVE_SETTLE_S):
    before = set(os.listdir(save_dir()))
    r = bus.send("savegame", name, timeout=timeout) or {}
    if r.get("error"):
        raise RuntimeError("bake_saves: savegame refused -- %s" % r["error"])
    deadline = time.time() + timeout
    while time.time() < deadline:
        now = set(os.listdir(save_dir()))
        fresh = [f for f in (now - before) if f.startswith(name)]
        if fresh:
            path = os.path.join(save_dir(), sorted(fresh)[0])
            if os.path.getsize(path) > 0:
                return path
        time.sleep(1.0)
    raise RuntimeError(
        "bake_saves: no save file named %r appeared in %s within %.0fs. The click may "
        "have been reported as sent without the game writing anything -- an unverified "
        "save is void." % (name, save_dir(), timeout))


def to_main_menu(ex):
    if ex.leave_campaign_via_click():
        return True
    raise RuntimeError(
        "bake_saves: could not get back to the frontend after saving. Stopping rather "
        "than starting the next campaign on top of a session in an unknown state.")


def bake_one(campaign, faction, radius, turn=1, dry=False, log=print):
    import bus_launcher
    from executor import Executor

    bl = bus_launcher.BusLauncher()
    campaign_map = bl.CAMPAIGN_KEYS.get(campaign, campaign)
    bus = _bus()
    ex = Executor(bus)

    if game_is_up(bus):
        log("bake: taking over the running game")
        started = bl.start_campaign(faction, campaign, load_timeout=PLAYABLE_TIMEOUT_S)
    else:
        log("bake: launching the game")
        started = bl.launch(faction, campaign, load_timeout=PLAYABLE_TIMEOUT_S)
    if not started:
        raise RuntimeError("bake_saves: %s on %s never reached a playable campaign"
                           % (faction, campaign))

    t0 = time.time()
    r = trim(bus, radius, dry=dry)
    log("bake: trim r=%g -- killed %d, kept %d, unplaced %d, already dead %d (%.1fs)"
        % (radius, r["n_killed"], r["n_kept"], r["n_unplaced"],
           r.get("n_already_dead") or 0, time.time() - t0))

    out = {"campaign": campaign, "campaign_map": campaign_map, "faction": faction,
           "radius": float(radius), "turn": int(turn), "dry": bool(dry),
           "n_killed": r["n_killed"], "n_kept": r["n_kept"],
           "n_unplaced": r["n_unplaced"], "origin": [r.get("origin_x"), r.get("origin_y")],
           "kept": r.get("kept") or [], "killed": r.get("killed") or []}

    if dry:
        out["save"] = None
        log("bake: dry run, nothing saved")
        return out

    name = save_name(campaign_map, faction, radius, r["n_kept"], turn)
    out["save"] = save_campaign(bus, name)
    log("bake: saved %s" % out["save"])
    to_main_menu(ex)
    return out


def bake_all(campaign, radius, factions=None, turn=1, dry=False, out_path=None,
             log=print):
    import bus_launcher
    bl = bus_launcher.BusLauncher()
    keys = factions or bl.startable_factions(campaign)
    log("bake_all: %d starts on %s at radius %g" % (len(keys), campaign, radius))
    done, failed = [], []
    for i, faction in enumerate(sorted(keys)):
        log("\n=== %d/%d  %s" % (i + 1, len(keys), faction))
        try:
            done.append(bake_one(campaign, faction, radius, turn=turn, dry=dry, log=log))
        except Exception as e:
            log("!! %s FAILED: %s" % (faction, repr(e)[:300]))
            failed.append({"faction": faction, "error": repr(e)[:300]})
        if out_path:
            json.dump({"campaign": campaign, "radius": radius, "turn": turn,
                       "dry": dry, "baked": done, "failed": failed},
                      open(out_path, "w"), indent=1)
    log("\nbake_all: %d baked, %d failed of %d starts" % (len(done), len(failed), len(keys)))
    return {"baked": done, "failed": failed, "requested": len(keys)}


def main(argv):
    def arg(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    campaign = arg("--campaign", "Realm of Chaos")
    radius = float(arg("--radius", str(DEFAULT_RADIUS)))
    turn = int(arg("--turn", "1"))
    dry = "--dry" in argv
    one = arg("--faction")
    out_path = arg("--out")
    factions = None
    if arg("--factions"):
        factions = [k.strip() for k in arg("--factions").split(",") if k.strip()]

    if one:
        print(json.dumps(bake_one(campaign, one, radius, turn=turn, dry=dry),
                         indent=1, default=str))
        return 0
    r = bake_all(campaign, radius, factions=factions, turn=turn, dry=dry,
                 out_path=out_path)
    return 0 if not r["failed"] else 2


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
