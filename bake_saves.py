from __future__ import annotations


import json
import os
import re
import sys
import time

import common

sys.path.insert(0, common.LAUNCHER)
sys.path.insert(0, os.path.join(common.ROOT, "bus"))

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


def save_name(campaign_map, faction, radius, turn):
    return "trim__%s__%s__r%g__t%d" % (
        campaign_map, faction, float(radius), int(turn))


def parse_save_name(name):
    m = re.match(r"^trim__(.+?)__(.+?)__r([0-9.]+)__t(\d+)$",
                 os.path.splitext(os.path.basename(name))[0])
    if not m:
        return None
    return {"campaign_map": m.group(1), "faction": m.group(2),
            "radius": float(m.group(3)), "turn": int(m.group(4))}


def presave_dir():
    d = os.path.join(common.native(common.TWDATA), "presaves")
    os.makedirs(d, exist_ok=True)
    return d


def restore_presave(meta, log=print):
    import shutil
    dst = os.path.join(save_dir(), meta["file"])
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(meta["path"]):
        return dst
    shutil.copy2(meta["path"], dst)
    log("restored %s into the game save folder" % meta["file"])
    return dst


def list_presaves(radius=None, campaign_map=None, turn=None, where="archive"):
    if where == "archive":
        roots = [presave_dir()]
    elif where == "game":
        roots = [save_dir()]
    elif where == "both":
        roots = [presave_dir(), save_dir()]
    else:
        raise ValueError("where must be archive, game, or both; got %r" % where)
    out, seen = [], set()
    for root in roots:
        for f in sorted(os.listdir(root)):
            if not f.endswith(".save") or f in seen:
                continue
            meta = parse_save_name(f)
            if not meta:
                continue
            if radius is not None and float(meta["radius"]) != float(radius):
                continue
            if campaign_map is not None and meta["campaign_map"] != campaign_map:
                continue
            if turn is not None and int(meta["turn"]) != int(turn):
                continue
            seen.add(f)
            meta["file"] = f
            meta["path"] = os.path.join(root, f)
            out.append(meta)
    return out


def presave_radii():
    counts = {}
    for p in list_presaves():
        counts.setdefault(p["radius"], []).append(p)
    return {r: len(v) for r, v in sorted(counts.items())}


def _bus():
    from bus import Bus
    return Bus()


def attach(bus=None):
    import bus_launcher
    bl = bus_launcher.BusLauncher()
    bl.bus = bus or _bus()
    if not bl._wait_bus_ready():
        raise RuntimeError(
            "bake_saves: no live game answered the bus. Start the game first, or let "
            "bake_one launch it, rather than driving a session that is not there.")
    return bl


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
            "bake_saves: disarming failed on %d of %d factions (%s). Refusing to "
            "save a world that is trimmed differently from what the name will claim."
            % (r["n_failed"], r["n_failed"] + r["n_killed"],
               ", ".join(r.get("failed") or [])[:200]))
    return r


SAVE_NEEDLES = ("save",)
ACCEPT_NEEDLES = ("accept", "ok_button", "button_ok", "confirm", "save_button")
FIELD_NEEDLES = ("text_edit", "editable", "input", "filename", "text_box")


def ui_paths(bl, needles, depth=8, nodes=1500):
    hits = []
    for root in (bl._roots_safe() or []):
        rid = str(root.get("id") or "")
        if not rid or not root.get("visible"):
            continue
        t = bl.tree(rid, depth=depth, nodes=nodes) or {}
        for nd in (t.get("nodes") or []):
            p = str(nd.get("path") or "")
            low = p.lower()
            if any(n in low for n in needles):
                hits.append(p)
    return hits


def discover_save_ui(bl, log=print):
    bl._send("key", "@root escape", timeout=15)
    time.sleep(1.5)
    roots = [r.get("id") for r in (bl._roots_safe() or []) if r.get("visible")]
    out = {"roots_visible": roots,
           "save_like": ui_paths(bl, SAVE_NEEDLES),
           "field_like": ui_paths(bl, FIELD_NEEDLES),
           "accept_like": ui_paths(bl, ACCEPT_NEEDLES)}
    log(json.dumps(out, indent=1)[:4000])
    return out


def save_via_ui(bl, name, log=print):
    bl._send("key", "@root escape", timeout=15)
    time.sleep(1.5)
    entries = ui_paths(bl, SAVE_NEEDLES)
    if not entries:
        raise RuntimeError(
            "bake_saves: pressed escape but found nothing save-like in the visible UI. "
            "Run --discover-save on a live campaign and fill in the real ids rather "
            "than guessing: %s" % json.dumps(
                [r.get("id") for r in (bl._roots_safe() or [])])[:300])
    entry = sorted(entries, key=len)[0]
    log("bake: clicking save entry %s" % entry)
    bl._send("click", entry, timeout=20)
    time.sleep(1.5)
    fields = ui_paths(bl, FIELD_NEEDLES)
    if fields:
        field = sorted(fields, key=len)[0]
        bl._send("click", field, timeout=20)
        time.sleep(0.4)
        for ch in name:
            bl._send("key", "%s %s" % (field, ch), timeout=10)
    else:
        for ch in name:
            bl._send("key", "@root %s" % ch, timeout=10)
    accepts = ui_paths(bl, ACCEPT_NEEDLES)
    if not accepts:
        raise RuntimeError(
            "bake_saves: typed the name but found no accept button. Discovered "
            "save-like=%s field-like=%s" % (entries[:5], fields[:5]))
    accept = sorted(accepts, key=len)[0]
    log("bake: confirming with %s" % accept)
    bl._send("click", accept, timeout=20)
    return {"entry": entry, "accept": accept, "fields": fields[:3]}


QUICKSAVE_HOTKEY = ("ctrl", "s")
STABLE_POLL_S = 3.0
STABLE_READS = 2


def _list_save_files():
    d = save_dir()
    out = {}
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.isfile(p):
            out[f] = os.path.getsize(p)
    return out


def _is_decoy(fname):
    stem, ext = os.path.splitext(fname)
    return ext == ".save" and stem.endswith(".save")


def _wait_named_save(name, timeout, log=print):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for f in os.listdir(save_dir()):
            if f.startswith(name):
                path = os.path.join(save_dir(), f)
                if os.path.getsize(path) > 0:
                    return path
        time.sleep(1.0)
    raise RuntimeError(
        "bake_saves: no save file named %r appeared in %s within %.0fs. A save call "
        "reported as sent without the game writing anything is not a save."
        % (name, save_dir(), timeout))


def quicksave_via_hotkey(bl, timeout=SAVE_SETTLE_S, log=print):
    from executor import Executor
    before = set(_list_save_files())
    ex = Executor(bl.bus)
    if not ex.send_hotkey(*QUICKSAVE_HOTKEY):
        raise RuntimeError(
            "bake_saves: could not send the quicksave hotkey -- the game was not the "
            "foreground window. A hotkey sent anywhere else does nothing.")
    deadline = time.time() + timeout
    candidate, last_size, stable_reads = None, None, 0
    while time.time() < deadline:
        now = _list_save_files()
        fresh = {f: sz for f, sz in now.items() if f not in before}
        primaries = {f: sz for f, sz in fresh.items() if not _is_decoy(f)}
        decoy_present = any(_is_decoy(f) for f in fresh)
        if primaries:
            candidate = sorted(primaries)[0]
            sz = primaries[candidate]
            if decoy_present:
                stable_reads = 0
            elif sz == last_size:
                stable_reads += 1
            else:
                stable_reads = 1
            last_size = sz
            log("bake: quicksave candidate %r  size=%d  decoy=%s  stable_reads=%d"
                % (candidate, sz, decoy_present, stable_reads))
            if stable_reads >= STABLE_READS and not decoy_present:
                return os.path.join(save_dir(), candidate)
        time.sleep(STABLE_POLL_S)
    raise RuntimeError(
        "bake_saves: quicksave hotkey sent but no save settled to a stable size within "
        "%.0fs (last candidate %r). A file whose size is still changing, or that still "
        "has its .save.save staging twin, is not saved yet." % (timeout, candidate))


def save_campaign(bus, name, bl=None, timeout=SAVE_SETTLE_S, log=print):
    r = bus.send("savegame", name, timeout=15) or {}
    if not r.get("error"):
        log("bake: scripted save via %s" % r.get("used"))
        return _wait_named_save(name, timeout, log=log)

    log("bake: no scripted save (%s)" % str(r["error"])[:120])
    if bl is None:
        raise RuntimeError("bake_saves: savegame refused and no launcher was given "
                           "to fall back on -- %s" % r["error"])

    try:
        log("bake: trying the quicksave hotkey")
        raw = quicksave_via_hotkey(bl, timeout=timeout, log=log)
    except RuntimeError as e:
        log("bake: quicksave hotkey failed (%s) -- driving the save screen by click"
            % repr(e)[:150])
        save_via_ui(bl, name, log=log)
        return _wait_named_save(name, timeout, log=log)

    target = os.path.join(save_dir(), name + ".save")
    if os.path.exists(target):
        raise RuntimeError(
            "bake_saves: target save name %r already exists -- refusing to overwrite "
            "a prior bake rather than silently clobbering it" % target)
    os.replace(raw, target)
    log("bake: renamed %s -> %s" % (os.path.basename(raw), target))
    return target


def to_main_menu(bl):
    if bl.quit_to_main_menu():
        return True
    raise RuntimeError(
        "bake_saves: could not get back to the frontend after saving. Stopping rather "
        "than starting the next campaign on top of a session in an unknown state.")


def bake_one(campaign, faction, radius, turn=1, dry=False, log=print):
    import bus_launcher

    bl = bus_launcher.BusLauncher()
    campaign_map = bl.CAMPAIGN_KEYS.get(campaign, campaign)
    bus = _bus()

    if game_is_up(bus):
        log("bake: taking over the running game")
        bl = attach(bus)
        started = bl.restart_campaign(faction, campaign, load_timeout=PLAYABLE_TIMEOUT_S)
    else:
        log("bake: launching the game")
        started = bl.launch(faction, campaign, load_timeout=PLAYABLE_TIMEOUT_S)
    if not started:
        raise RuntimeError("bake_saves: %s on %s never reached a playable campaign"
                           % (faction, campaign))

    t0 = time.time()
    r = trim(bus, radius, dry=dry)
    log("bake: trim r=%g -- disarmed %d, kept %d, unplaced %d, already dead %d (%.1fs)"
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

    name = save_name(campaign_map, faction, radius, turn)
    out["save"] = save_campaign(bus, name, bl=bl, log=log)
    log("bake: saved %s" % out["save"])
    to_main_menu(bl)
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
    radius_arg = arg("--radius")
    if radius_arg is None:
        raise SystemExit("--radius is required; refusing to trim with an implicit radius")
    radius = float(radius_arg)
    turn = int(arg("--turn", "1"))
    dry = "--dry" in argv
    one = arg("--faction")
    out_path = arg("--out")
    factions = None
    if arg("--factions"):
        factions = [k.strip() for k in arg("--factions").split(",") if k.strip()]

    if "--discover-save" in argv:
        discover_save_ui(attach())
        return 0
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
