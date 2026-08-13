from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common
from decisions import dbopen


def live_run():
    return common.RUN_DIR


def _con(run_dir):
    return dbopen.connect(os.path.join(run_dir, "decisions.sqlite"))


def refusal_report(con):
    rows = []
    for at, in con.execute("SELECT DISTINCT action_type FROM action_taken"):
        n, ok, pre = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(counted),0),"
            " COALESCE(SUM(COALESCE(json_extract(diagnostics,'$.prechecks'),'') != ''),0)"
            " FROM action_taken WHERE action_type=?"
            " AND refusal IS NOT 'awaiting_execution'", (at,)).fetchone()
        n, ok, pre = n or 0, ok or 0, pre or 0
        if n:
            rows.append((at, n, ok, 100.0 * ok / n, pre))
    return sorted(rows, key=lambda x: x[3])


def offered_vs_taken(con):
    out = {}
    for at, n in con.execute(
            "SELECT action_type, COUNT(*) FROM action_offers GROUP BY action_type"):
        out[at] = {"offered": n}
    for at, n in con.execute(
            "SELECT action_type, COUNT(*) FROM action_taken"
            " WHERE refusal IS NOT 'awaiting_execution' GROUP BY action_type"):
        out.setdefault(at, {"offered": 0})["picked"] = n
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
        (ok_v if counted else fail_v).__class__
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

    print("\nWHAT THE EXECUTOR DID WITH WHAT THE ADVISOR CHOSE")
    print("%-20s %6s %6s %8s %9s" % ("action", "picked", "ok", "rate", "pre-check"))
    for at, tot, ok, pct, pre in refusal_report(con):
        flag = "  <-- the click keeps failing" if pct < 50 else ""
        print("%-20s %6d %6d %7.0f%% %9d%s" % (at, tot, ok, pct, pre, flag))

    print("\nGENERATED vs PICKED (dead weight check)")
    print("%-20s %9s %8s" % ("action", "generated", "picked"))
    for at, d in sorted(offered_vs_taken(con).items(), key=lambda kv: -kv[1]["offered"]):
        print("%-20s %9d %8s" % (at, d["offered"], d.get("picked", 0)))

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
