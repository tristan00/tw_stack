from __future__ import annotations


import os
import re


def save_dir():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError(
            "presaves: APPDATA is not set, so the save folder cannot be located. "
            "Warhammer 3 writes saves under %APPDATA%\\The Creative Assembly.")
    d = os.path.join(appdata, "The Creative Assembly", "Warhammer3", "save_games")
    if not os.path.isdir(d):
        raise RuntimeError(
            "presaves: no save folder at %s. Save one campaign by hand once so the "
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
    import common
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
