
from __future__ import annotations

import collections
import glob
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

OUT_PATH = os.path.join(common.REFERENCE_DIR, "cutscene_starts.json")

KEY_THRESHOLD = 20
MIN_OBS = 2

_HUD = re.compile(r"interactive HUD reached in ([\d.]+)s \((\d+) cinematic keys\)")
_PLAYABLE = re.compile(
    r"CAMPAIGN PLAYABLE: (\S+) / (\S+) -- load ([\d.]+)s \+ hud ([\d.]+)s = ([\d.]+)s")


def log_files(root=None):
    out = []
    for dirpath, _dirs, files in os.walk(root or common.TWDATA):
        for f in files:
            if f.endswith(".log"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def observations(log_glob=None):
    paths = sorted(glob.glob(log_glob)) if log_glob else log_files()
    out = collections.defaultdict(list)
    for path in paths:
        keys = None
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                m = _HUD.search(line)
                if m:
                    keys = int(m.group(2))
                    continue
                m = _PLAYABLE.search(line)
                if m:
                    out[(m.group(1), m.group(2))].append(
                        {"hud": float(m.group(4)), "keys": keys})
                    keys = None
    return out


def classify(obs=None, campaign_map=None):
    obs = observations() if obs is None else obs
    cutscene, clean, unknown = [], [], []
    for (cmap, faction), rows in sorted(obs.items()):
        if campaign_map and cmap != campaign_map:
            continue
        keys = [r["keys"] for r in rows if r["keys"] is not None]
        huds = [r["hud"] for r in rows]
        rec = {"map": cmap, "faction": faction, "n": len(rows),
               "hud_median": round(statistics.median(huds), 1),
               "keys_median": (statistics.median(keys) if keys else None)}
        if not keys or len(rows) < MIN_OBS:
            unknown.append(rec)
        elif rec["keys_median"] >= KEY_THRESHOLD:
            cutscene.append(rec)
        else:
            clean.append(rec)
    cutscene.sort(key=lambda r: -r["hud_median"])
    clean.sort(key=lambda r: r["hud_median"])
    return {"cutscene": cutscene, "clean": clean, "unknown": unknown,
            "campaign_map": campaign_map, "key_threshold": KEY_THRESHOLD,
            "min_observations": MIN_OBS,
            "launches_seen": sum(len(v) for k, v in obs.items()
                                 if not campaign_map or k[0] == campaign_map)}


DEFAULT_MAP = "wh3_main_combi"


def main(argv):
    cmap = None
    if "--map" in argv:
        cmap = argv[argv.index("--map") + 1]
    r = classify(campaign_map=cmap)
    if cmap:
        print("campaign map     : %s" % cmap)
    if not r["launches_seen"]:
        print("no launches found in %s" % common.LOGS_ADVISOR)
        return 1
    saved = sum((x["hud_median"] for x in r["cutscene"]), 0.0)
    med_clean = (statistics.median([x["hud_median"] for x in r["clean"]])
                 if r["clean"] else 0.0)
    print("launches parsed : %d" % r["launches_seen"])
    print("cutscene starts : %d" % len(r["cutscene"]))
    print("clean starts    : %d" % len(r["clean"]))
    print("unknown (<%d obs): %d" % (MIN_OBS, len(r["unknown"])))
    print("\nCUTSCENE STARTS -- each costs ~%.0fs extra to reach the HUD"
          % ((saved / max(len(r["cutscene"]), 1)) - med_clean))
    print("  %-46s %5s %8s %6s" % ("faction", "n", "hud_med", "keys"))
    for x in r["cutscene"]:
        print("  %-46s %5d %8.1f %6.0f" % (x["faction"], x["n"], x["hud_median"],
                                           x["keys_median"]))
    if r["unknown"]:
        print("\nUNKNOWN -- too few launches to call, not assumed clean:")
        for x in r["unknown"]:
            print("  %-46s %5d" % (x["faction"], x["n"]))
    if "--factions" in argv:
        print("\n" + ",".join(x["faction"] for x in r["clean"]))
    if "--write" in argv:
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            json.dump(r, fh, indent=1)
        print("\nwrote %s" % OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
