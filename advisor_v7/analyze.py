from __future__ import annotations

import json
import os
import sqlite3
import sys

RUNS_ROOT = "D:/twdata/runs/human"


def live_run():
    try:
        with open(os.path.join(RUNS_ROOT, "CURRENT_RUN"), encoding="utf-8") as f:
            d = f.read().strip()
        if d and os.path.isfile(os.path.join(d, "decisions.sqlite")):
            return d
    except OSError:
        pass
    raise SystemExit("no live run")


def _con(run_dir):
    p = os.path.join(run_dir, "decisions.sqlite").replace("\\", "/")
    return sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=10.0)


def offer_honesty(con):
    """Per action type: picked-while-available vs actually confirmed."""
    rows = []
    for at, in con.execute("SELECT DISTINCT action_type FROM action_taken"):
        r = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(t.counted),0) FROM action_taken t"
            " LEFT JOIN action_offers o ON o.offer_id=t.offer_id"
            " WHERE t.action_type=? AND t.refusal IS NOT 'awaiting_execution'"
            " AND COALESCE(o.available,1)=1", (at,)).fetchone()
        n, ok = r[0] or 0, r[1] or 0
        if n:
            rows.append((at, n, ok, 100.0 * ok / n))
    return sorted(rows, key=lambda x: x[3])


def offered_vs_taken(con):
    """Per action type: {offered, available, picked}."""
    out = {}
    for at, n, av in con.execute(
            "SELECT action_type, COUNT(*), SUM(available) FROM action_offers GROUP BY action_type"):
        out[at] = {"offered": n, "available": av or 0}
    for at, n in con.execute(
            "SELECT action_type, COUNT(*) FROM action_taken"
            " WHERE refusal IS NOT 'awaiting_execution' GROUP BY action_type"):
        out.setdefault(at, {"offered": 0, "available": 0})["picked"] = n
    return out


def time_split(con):
    rows = list(con.execute(
        "SELECT d.ts, d.timings, t.latency_ms, t.counted, t.refusal FROM decision_points d"
        " LEFT JOIN action_taken t ON t.decision_id=d.decision_id ORDER BY d.decision_id"))
    prev, wall, collect, ok_v, fail_v = None, 0.0, 0.0, 0.0, 0.0
    for ts, tm, lat, counted, refusal in rows:
        if prev:
            wall += ts - prev
        prev = ts
        t = json.loads(tm) if tm else {}
        collect += (t.get("collect_ms") or 0) / 1000.0
        if refusal == "awaiting_execution":
            continue
        (ok_v if counted else fail_v).__class__       # noqa
        if counted:
            ok_v += (lat or 0) / 1000.0
        else:
            fail_v += (lat or 0) / 1000.0
    return {"wall": wall, "collect": collect, "verify_ok": ok_v, "verify_fail": fail_v,
            "other": wall - collect - ok_v - fail_v, "n": len(rows)}


def main():
    run = sys.argv[1] if len(sys.argv) > 1 else live_run()
    con = _con(run)
    print("run: %s" % run)
    n = con.execute("SELECT COUNT(*) FROM decision_points").fetchone()[0]
    camps = [r[0] for r in con.execute("SELECT DISTINCT campaign_id FROM decision_points")]
    turns = con.execute("SELECT COALESCE(MAX(turn),0) FROM decision_points").fetchone()[0]
    print("decisions=%d  campaigns=%d  max_turn=%s" % (n, len(camps), turns))

    print("\nOFFER HONESTY -- picked while `available`, did it happen?")
    print("%-20s %6s %6s %8s" % ("action", "picked", "ok", "honesty"))
    for at, tot, ok, pct in offer_honesty(con):
        flag = "  <-- offers are lying" if pct < 50 else ""
        print("%-20s %6d %6d %7.0f%%%s" % (at, tot, ok, pct, flag))

    print("\nOFFERED vs PICKED (dead weight check)")
    print("%-20s %9s %11s %8s" % ("action", "offered", "available", "picked"))
    for at, d in sorted(offered_vs_taken(con).items(), key=lambda kv: -kv[1]["offered"]):
        print("%-20s %9d %11d %8s" % (at, d["offered"], d["available"], d.get("picked", 0)))

    t = time_split(con)
    if t["wall"] > 0:
        print("\nTIME  (wall %.0fs over %d decisions)" % (t["wall"], t["n"]))
        for k in ("collect", "verify_ok", "verify_fail", "other"):
            print("  %-12s %7.0fs  %4.0f%%" % (k, t[k], 100.0 * t[k] / t["wall"]))
        if t["verify_fail"] > t["verify_ok"]:
            print("  NOTE: more time is spent confirming FAILURES than successes -- a failed confirm"
                  "\n        burns its entire timeout, a success returns on the first poll.")
    con.close()


if __name__ == "__main__":
    main()
