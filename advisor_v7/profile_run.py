r"""profile_run.py -- where the loop's time actually goes.

    python advisor_v7/profile_run.py [run_dir]

READS ONLY decisions_stream.jsonl (append-only text). It never opens decisions.sqlite, so it cannot
take a lock, block the recorder's writer, or perturb the run it is measuring -- profiling a live
session has to be free, or it is measuring itself.

Two levels:
  PER PHASE   inside the recorder's snapshot: campaign_state / world_state / legal_stances /
              lord_state / lord_offers / province_state / province_offers / campaign_offers
  PER CYCLE   the whole decision point (collect + everything else the recorder did)
"""
from __future__ import annotations

import glob
import json
import os
import sys

RUNS_ROOT = "D:/twdata/runs/human"
PHASES = ("campaign_state", "world_state", "legal_stances", "lord_state", "lord_offers",
          "province_state", "province_offers", "campaign_offers")


def newest_run():
    try:
        with open(os.path.join(RUNS_ROOT, "CURRENT_RUN"), encoding="utf-8") as f:
            d = f.read().strip()
        if d and os.path.isdir(d):
            return d
    except OSError:
        pass
    ds = sorted(glob.glob(os.path.join(RUNS_ROOT, "*", "decisions_stream.jsonl")),
                key=os.path.getmtime, reverse=True)
    if not ds:
        raise SystemExit("no decisions_stream.jsonl under %s" % RUNS_ROOT)
    return os.path.dirname(ds[0])


def load(run_dir):
    rows = []
    path = os.path.join(run_dir, "decisions_stream.jsonl")
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or '"decisions_point"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "decisions_point":
                rows.append(r)
    return rows


def _stats(xs):
    if not xs:
        return (0, 0, 0, 0)
    s = sorted(xs)
    return (len(s), sum(s) / len(s), s[len(s) // 2], s[-1])


def report(run_dir):
    rows = load(run_dir)
    if not rows:
        print("no decision points recorded yet in %s" % run_dir)
        return
    prof = [r for r in rows if r.get("profile")]
    total = [r["ms"] for r in rows if r.get("ms")]
    n, mean, med, mx = _stats(total)
    print("run: %s" % run_dir)
    print("decision points: %d  (with phase profile: %d)" % (len(rows), len(prof)))
    print("collect cycle ms:  n=%d  mean=%.0f  median=%.0f  max=%.0f" % (n, mean, med, mx))
    print("estimated total collect time: %.1f min" % (sum(total) / 60000.0))
    if not prof:
        print("\n(no per-phase profile yet -- restart the manager to pick up the instrumented "
              "collect.py; older rows predate it)")
        return
    print("\n%-18s %6s %8s %8s %8s %8s" % ("phase", "n", "mean", "median", "max", "share"))
    grand = sum(sum(v for k, v in (r["profile"] or {}).items() if k in PHASES) for r in prof) or 1
    rowsout = []
    for ph in PHASES:
        xs = [r["profile"][ph] for r in prof if (r.get("profile") or {}).get(ph) is not None]
        if not xs:
            continue
        c, mn, md, mxx = _stats(xs)
        rowsout.append((sum(xs), ph, c, mn, md, mxx, 100.0 * sum(xs) / grand))
    for tot, ph, c, mn, md, mxx, share in sorted(rowsout, reverse=True):
        print("%-18s %6d %8.0f %8.0f %8.0f %7.1f%%" % (ph, c, mn, md, mxx, share))
    ents = [(r["profile"].get("_lords"), r["profile"].get("_regions"), r["ms"]) for r in prof
            if r.get("ms")]
    if ents:
        print("\nscaling (cycle ms by entity count):")
        by = {}
        for l, g, ms in ents:
            by.setdefault((l, g), []).append(ms)
        for (l, g), xs in sorted(by.items(), key=lambda kv: -len(kv[1]))[:8]:
            c, mn, md, mxx = _stats(xs)
            print("  %s lords + %s regions: n=%-4d median=%.0fms" % (l, g, c, md))


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else newest_run())
