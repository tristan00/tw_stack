from __future__ import annotations

import collections
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common
from decisions import dbopen

from store import DecisionStore

RUN_DIR = common.RUN_DIR


def run_dir():
    return RUN_DIR


def _con(run):
    return dbopen.connect(os.path.join(run, "decisions.sqlite"))


def dilemmas(rows):
    complete, incomplete, keys = 0, [], collections.Counter()
    for r in rows:
        if "dilemma" not in str(r.get("screen") or ""):
            continue
        opts = r.get("options") or {}
        if len(opts) < 2:
            continue
        vals = [v for v in opts.values() if isinstance(v, dict)]
        ids = {v.get("dilemma_id") for v in vals if v.get("dilemma_id")}
        oids = [v.get("option_id") for v in vals]
        problems = []
        if len(ids) != 1:
            problems.append("dilemma_id not single: %s" % sorted(ids))
        if any(not o for o in oids):
            problems.append("missing option_id")
        elif len(set(oids)) != len(oids):
            problems.append("duplicate option_id")
        if any(not v.get("text") for v in vals):
            problems.append("missing label")
        if not r.get("chosen"):
            problems.append("no chosen recorded")
        if problems:
            incomplete.append((r.get("campaign_id"), r.get("turn"), problems))
        else:
            complete += 1
            keys[sorted(ids)[0].replace("CcoCdirEventsDilemmaChoiceDetailRecord", "")] += 1
    return complete, incomplete, keys


def misclassified(rows):
    out = []
    for r in rows:
        screen = str(r.get("screen") or "")
        if "dilemma" in screen:
            continue
        opts = r.get("options") or {}
        if len(opts) >= 2:
            out.append((r.get("campaign_id"), r.get("turn"), screen, sorted(opts)[:4]))
    return out


def actions(con):
    by = collections.defaultdict(lambda: [0, 0, collections.Counter()])
    waste = 0
    lat = collections.defaultdict(list)
    for atype, executed, confirmed, refusal, latency, timing in con.execute(
            "SELECT action_type,executed,confirmed,refusal,latency_ms,timing FROM action_taken"):
        rec = by[atype]
        rec[0] += 1
        if confirmed:
            rec[1] += 1
        elif refusal:
            rec[2][str(refusal)[:38]] += 1
        if latency:
            lat[atype].append(latency)
        if timing:
            try:
                waste += (json.loads(timing) or {}).get("confirm_wasted_ms") or 0
            except Exception:
                pass
    return by, waste, lat


def main():
    run = run_dir()
    print("run dir: %s" % run)
    s = DecisionStore(run)
    try:
        rows = s.interrupt_rows()
    finally:
        s.close()
    con = _con(run)
    try:
        camps = [r[0] for r in con.execute(
            "SELECT COUNT(DISTINCT campaign_id) FROM decision_points")]
        turns = [r[0] for r in con.execute("SELECT COUNT(DISTINCT campaign_id||':'||turn) "
                                           "FROM decision_points")]
        dp = [r[0] for r in con.execute("SELECT COUNT(*) FROM decision_points")]
        print("campaigns=%s  campaign-turns=%s  decision points=%s  interrupt rows=%d"
              % (camps[0], turns[0], dp[0], len(rows)))

        print()
        print("--- DILEMMAS ---")
        ok, bad, keys = dilemmas(rows)
        print("multi-option dilemmas: %d complete, %d incomplete" % (ok, len(bad)))
        for camp, turn, problems in bad:
            print("  !! %s turn=%s -> %s" % (camp, turn, "; ".join(problems)))
        for k, n in keys.most_common():
            print("   %-58s x%d" % (k[:58], n))

        print()
        print("--- POSSIBLE MISCLASSIFICATION (non-dilemma screens with >1 option) ---")
        mis = misclassified(rows)
        if not mis:
            print("   none")
        for camp, turn, screen, opts in mis[:12]:
            print("   %s turn=%s screen=%s opts=%s" % (camp, turn, screen, opts))

        print()
        print("--- ACTIONS: what works vs not ---")
        by, waste, lat = actions(con)
        print("   %-18s %6s %8s %7s  %s" % ("action", "tried", "confirm", "rate", "top refusal"))
        for atype, (n, okc, refs) in sorted(by.items(), key=lambda kv: -kv[1][0]):
            top = refs.most_common(1)
            print("   %-18s %6d %8d %6.0f%%  %s"
                  % (atype[:18], n, okc, 100.0 * okc / n if n else 0,
                     ("%s x%d" % top[0]) if top else ""))
        print()
        print("   confirm time wasted: %.1f s" % (waste / 1000.0))
        slow = sorted(((sum(v) / len(v), k, len(v)) for k, v in lat.items() if v), reverse=True)
        for ms, k, n in slow[:6]:
            print("   slow: %-18s mean %6.0f ms over %d" % (k[:18], ms, n))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
