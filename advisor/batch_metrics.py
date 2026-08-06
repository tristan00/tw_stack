from __future__ import annotations

import collections
import glob
import json
import os
import sqlite3
import sys
import time

RUNS = r"D:/twdata/runs/human"
OUT_DIR = r"D:\twdata\metrics"
GAP_CAP = 600.0


def _dist(xs):
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return {"n": n, "p50": round(xs[n // 2], 1), "p90": round(xs[int(n * 0.9)], 1),
            "max": round(xs[-1], 1), "sum": round(sum(xs), 1)}


def collect(since):
    acts = collections.defaultdict(lambda: {"tried": 0, "ok": 0, "secs": [], "fail_secs": [],
                                            "refusals": collections.Counter()})
    inter = collections.defaultdict(lambda: {"seen": 0, "ok": 0, "lat": []})
    camps, turns_by_camp = {}, {}
    for db in sorted(glob.glob(os.path.join(RUNS, "*", "decisions.sqlite"))):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=5.0)
        except sqlite3.Error:
            continue
        try:
            keep = {c for c, ts in con.execute(
                "SELECT campaign_id, MIN(ts) FROM decision_points GROUP BY campaign_id")
                if ts and ts >= since}
            if not keep:
                continue
            for c, mx, cj in con.execute(
                    "SELECT campaign_id, MAX(turn), MIN(campaign) FROM decision_points"
                    " GROUP BY campaign_id"):
                if c in keep:
                    turns_by_camp[c] = mx or 0
                    try:
                        camps[c] = (json.loads(cj) or {}).get("faction")
                    except Exception:
                        camps[c] = None
            rows = list(con.execute(
                "SELECT d.campaign_id, d.ts, t.action_type, t.counted, t.refusal"
                " FROM decision_points d JOIN action_taken t ON t.decision_id=d.decision_id"
                " WHERE t.refusal IS NOT 'awaiting_execution'"
                " ORDER BY d.campaign_id, d.decision_id"))
            for i, (c, ts, at, counted, ref) in enumerate(rows):
                if c not in keep:
                    continue
                a = acts[at]
                a["tried"] += 1
                a["ok"] += 1 if counted else 0
                if not counted and ref:
                    a["refusals"][ref] += 1
                gap = None
                if i + 1 < len(rows) and rows[i + 1][0] == c and rows[i + 1][1] and ts:
                    g = rows[i + 1][1] - ts
                    if 0 <= g <= GAP_CAP:
                        gap = g
                if gap is not None:
                    a["secs"].append(gap)
                    if not counted:
                        a["fail_secs"].append(gap)
            ph = ",".join("?" * len(keep))
            for kind, counted, lat in con.execute(
                    "SELECT kind, counted, latency_ms FROM interrupt_decisions"
                    " WHERE campaign_id IN (%s)" % ph, tuple(keep)):
                s = inter[kind]
                s["seen"] += 1
                s["ok"] += 1 if counted else 0
                if lat is not None:
                    s["lat"].append(lat / 1000.0)
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return acts, inter, camps, turns_by_camp


def throughput(since):
    n, secs, turns = 0, 0.0, 0
    for p in sorted(glob.glob(os.path.join(RUNS, "session_*.json"))):
        try:
            s = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for c in (s.get("campaigns") or []):
            if not isinstance(c, dict) or (c.get("started") or 0) < since:
                continue
            if not c.get("seconds"):
                continue
            n += 1
            secs += float(c["seconds"])
            turns += int(c.get("turns_played") or 0)
    h = secs / 3600.0
    return {"campaigns": n, "campaign_hours": round(h, 2),
            "campaigns_per_hour": round(n / h, 2) if h else None,
            "turns_per_hour": round(turns / h, 2) if h else None,
            "turns_per_campaign": round(float(turns) / n, 2) if n else None}


def outcomes(since):
    out = collections.Counter()
    for p in sorted(glob.glob(os.path.join(RUNS, "session_*.json"))):
        try:
            s = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for c in (s.get("campaigns") or []):
            if isinstance(c, dict) and (c.get("started") or 0) >= since:
                out[c.get("outcome") or "?"] += 1
    return dict(out)


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y%m%d_%H%M%S")
    since = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    acts, inter, camps, turns_by_camp = collect(since)
    tried = sum(a["tried"] for a in acts.values())
    ok = sum(a["ok"] for a in acts.values())
    all_secs = [s for a in acts.values() for s in a["secs"]]
    fail_secs = [s for a in acts.values() for s in a["fail_secs"]]
    row = {
        "batch": label, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "since": since,
        "campaigns": len(camps), "turns_total": sum(turns_by_camp.values()),
        "turns_per_campaign": _dist([float(v) for v in turns_by_camp.values()]),
        "actions_tried": tried, "actions_confirmed": ok,
        "confirm_pct": round(100.0 * ok / tried, 1) if tried else None,
        "action_seconds": _dist(all_secs), "failed_action_seconds": _dist(fail_secs),
        "outcomes": outcomes(since), "throughput": throughput(since),
        "by_action": {a: {"tried": v["tried"], "ok": v["ok"],
                          "pct": round(100.0 * v["ok"] / v["tried"], 1) if v["tried"] else None,
                          "sec_per_try": round(sum(v["secs"]) / len(v["secs"]), 1) if v["secs"] else None,
                          "sec_per_fail": (round(sum(v["fail_secs"]) / len(v["fail_secs"]), 1)
                                           if v["fail_secs"] else None),
                          "top_refusals": dict(v["refusals"].most_common(3))}
                      for a, v in sorted(acts.items())},
        "by_interrupt": {k: {"seen": v["seen"], "ok": v["ok"],
                             "pct": round(100.0 * v["ok"] / v["seen"], 1) if v["seen"] else None,
                             "lat_p50": _dist(v["lat"])["p50"] if v["lat"] else None}
                         for k, v in sorted(inter.items())},
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "batches.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")

    print("=" * 78)
    print("BATCH %s  campaigns=%d turns=%d  confirm=%s%%"
          % (label, row["campaigns"], row["turns_total"], row["confirm_pct"]))
    print("outcomes:", row["outcomes"])
    tp = row["throughput"]
    print("THROUGHPUT  campaigns/hr=%s  turns/hr=%s  turns/campaign=%s  (%s campaigns over %sh)"
          % (tp["campaigns_per_hour"], tp["turns_per_hour"], tp["turns_per_campaign"],
             tp["campaigns"], tp["campaign_hours"]))
    print("turns/campaign dist:", row["turns_per_campaign"])
    print("action seconds:", row["action_seconds"], " failed:", row["failed_action_seconds"])
    print("-" * 78)
    print("%-18s %6s %6s %7s %9s %9s  %s" % ("action", "tried", "ok", "pct", "s/try", "s/fail",
                                             "top refusals"))
    for a, v in row["by_action"].items():
        print("%-18s %6d %6d %6s%% %9s %9s  %s"
              % (a, v["tried"], v["ok"], v["pct"], v["sec_per_try"], v["sec_per_fail"],
                 ",".join("%s:%d" % kv for kv in list(v["top_refusals"].items())[:2])))
    print("-" * 78)
    for k, v in row["by_interrupt"].items():
        print("%-18s seen=%-4d ok=%-4d %5s%%  p50=%ss" % (k, v["seen"], v["ok"], v["pct"],
                                                          v["lat_p50"]))
    print("-> %s" % os.path.join(OUT_DIR, "batches.jsonl"))


if __name__ == "__main__":
    main()
