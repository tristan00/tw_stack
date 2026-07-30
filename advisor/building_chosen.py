r"""building_chosen.py -- robust OFFLINE recovery of the building a construction decision chose.

Winner (verified on v6 run 20260727_144203): the in-log UI click-path.
Every player construction fires TWSTATE event `BuildingConstructionIssuedByPlayer` (carries
region=`garrison`, turn, game-time) with, at the SAME game-second, an `[ui] square_building_button`
"path from root" line whose tail
    ... CcoCampaignSettlement<REGION> ... CcoCampaignBuildingSlot<SLOT> ...
        slot_parent > <BUILDING_LEVEL_KEY> > square_building_button
embeds the exact building level key, its region, and its slot -- captured at the decision instant.

Streams the log ONCE (safe for multi-GB .tail logs). Per issue event, reads the chosen building from
the adjacent click-path; falls back to BuildingCompleted-by-region for any missing click-path. Both
anchors are the same `garrison` region string -- no screen-coordinate mapping is involved.

Verified v6 20260727_144203: 20/20 construction decisions linked (7 hef 1449 + 11 wef 1539 +
2 hef 2140), precision 100% (every prediction region-matched the issue's garrison, 14/20 independently
corroborated by a later BuildingCompleted in the SAME region, zero contradictions). Beats
BuildingCompleted-order (70% recall / 57% precision) and issue+slot state-diff (80% / 81%).
"""
import json
import re
import sys

RE_GT = re.compile(r'<([0-9.]+)s>')
RE_SETT = re.compile(r'CcoCampaignSettlement(wh[0-9a-z_]*?region[0-9a-z_]*?) > settlement_view')
RE_SLOT = re.compile(r'CcoCampaignBuildingSlot(region_slot_\d+)')
RE_KEY = re.compile(r'slot_parent > ([a-z0-9_]+) > square_building_button')
WINDOW = 0.5  # sec: click-path game-time vs issue-event game-time


def resolve_construction_choices(log_path, window=WINDOW):
    """Stream one script_log .tail; return construction decisions with the chosen building key.
    -> [{gt, turn, region, slot, chosen, source}]  (source in {"clickpath","completed",None})
    """
    issues, completed = [], []
    click_at = {}       # gt(rounded 0.1) -> [(region, slot, key)] card-select click-paths
    _bad = 0
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "square_building_button" in line and "slot_parent > " in line and "path from root" in line:
                g = RE_GT.search(line)
                k = RE_KEY.search(line)
                if g and k:
                    gt = float(g.group(1))
                    s = RE_SETT.search(line)
                    sl = RE_SLOT.search(line)
                    click_at.setdefault(round(gt, 1), []).append(
                        (s.group(1) if s else None, sl.group(1) if sl else None, k.group(1)))
                continue
            if "TWSTATE" not in line:
                continue
            if '"event":"BuildingConstructionIssuedByPlayer"' in line:
                g = RE_GT.search(line)
                js = line.find("TWSTATE {")
                try:
                    r = json.loads(line[js + 8:])
                except Exception:
                    _bad += 1           # hot per-line parse: count, don't log each
                    continue
                if r.get("event") == "BuildingConstructionIssuedByPlayer":
                    issues.append({"gt": float(g.group(1)) if g else None,
                                   "turn": r.get("turn"), "region": r.get("garrison")})
            elif '"event":"BuildingCompleted"' in line:
                g = RE_GT.search(line)
                js = line.find("TWSTATE {")
                try:
                    r = json.loads(line[js + 8:])
                except Exception:
                    _bad += 1           # hot per-line parse: count, don't log each
                    continue
                if r.get("event") == "BuildingCompleted" and r.get("building"):
                    completed.append({"gt": float(g.group(1)) if g else None,
                                      "turn": r.get("turn"), "region": r.get("garrison"),
                                      "building": r.get("building")})
    if _bad:
        sys.stderr.write("building_chosen: skipped %d malformed TWSTATE lines\n" % _bad)
    out = []
    for i in issues:
        chosen = slot = source = None
        gt = i["gt"]
        if gt is not None:
            for rk in (round(gt, 1) - 0.1, round(gt, 1), round(gt, 1) + 0.1):
                for (reg, sl, key) in click_at.get(round(rk, 1), []):
                    if reg == i["region"]:
                        slot, chosen, source = sl, key, "clickpath"   # last card-select wins
        out.append({"gt": gt, "turn": i["turn"], "region": i["region"],
                    "slot": slot, "chosen": chosen, "source": source})
    claimed = set()
    for d in out:                     # fallback: BuildingCompleted region+order
        if d["chosen"]:
            continue
        cand = sorted([(j, c) for j, c in enumerate(completed)
                       if c["region"] == d["region"] and c["turn"] >= d["turn"] and j not in claimed],
                      key=lambda jc: (jc[1]["turn"], jc[1]["gt"] or 0))
        if cand:
            j, c = cand[0]
            claimed.add(j)
            d["chosen"], d["source"] = c["building"], "completed"
    return out
