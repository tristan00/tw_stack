r"""Which starts open on a cutscene, measured from the launcher's own logs.

WHY -- a cutscene start costs about a minute of wall clock per campaign and buys nothing.
Reaching an interactive HUD takes ~19s on a start with no cutscene and 68-119s on one that
has one, every campaign, forever. Restricting --factions to the clean starts is the
cheapest throughput win available: no code changes the game, nothing is skipped that
matters, the corpus just stops paying for intro movies.

WHAT IS ACTUALLY MEASURED. The launcher logs two lines per campaign:

    [launch] interactive HUD reached in <hud>s (<n> cinematic keys)
    [launch] CAMPAIGN PLAYABLE: <map> / <faction> -- load Xs + hud <hud>s = Zs

`cinematic keys` is the count of space/escape presses advance_to_hud had to send before
the HUD appeared. That is the direct measure -- hud seconds is confounded by disk and
load variance, the key count is not. Over 610 launches across 103 distinct starts the
split is not a judgement call:

    89 starts   7-9 keys      17-30s to HUD      no cutscene
    14 starts   92-200 keys   68-119s to HUD     cutscene

Nothing lands between 9 and 92 keys, so KEY_THRESHOLD is a gap, not a percentile. The 14
are overwhelmingly the recent DLC legendary lords, which ship with an intro movie.

The list is DERIVED, never hand-maintained: rerun this and it re-measures. A start with
too few observations is reported as unknown rather than assumed clean, because assuming
clean is the expensive direction.

    python launcher/cutscene_starts.py            # report
    python launcher/cutscene_starts.py --write    # also write the json
    python launcher/cutscene_starts.py --factions # comma list for --factions
"""

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

# 9 vs 92 -- anywhere in that gap works, and nothing has ever landed in it.
KEY_THRESHOLD = 20
MIN_OBS = 2

_HUD = re.compile(r"interactive HUD reached in ([\d.]+)s \((\d+) cinematic keys\)")
_PLAYABLE = re.compile(
    r"CAMPAIGN PLAYABLE: (\S+) / (\S+) -- load ([\d.]+)s \+ hud ([\d.]+)s = ([\d.]+)s")


def log_files(root=None):
    r"""Every .log under TWDATA, not just the live advisor directory.

    Scanning only D:\twdata\logs\advisor missed 75 logs sitting in archive subtrees
    (corpus wipes, scrapped runs, misplaced_session_logs_20260807) plus more under runs
    and scratch -- and those are launches too. Every start has been played many times
    across all of them, so the wider scan is what makes the per-start medians solid rather
    than a read of the last few days.
    """
    out = []
    for dirpath, _dirs, files in os.walk(root or common.TWDATA):
        for f in files:
            if f.endswith(".log"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def observations(log_glob=None):
    """(map, faction) -> [{hud, keys}] over every log on disk."""
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


def classify(obs=None):
    """Split the observed starts into cutscene / clean / unknown."""
    obs = observations() if obs is None else obs
    cutscene, clean, unknown = [], [], []
    for (cmap, faction), rows in sorted(obs.items()):
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
            "key_threshold": KEY_THRESHOLD, "min_observations": MIN_OBS,
            "launches_seen": sum(len(v) for v in obs.values())}


def main(argv):
    r = classify()
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
