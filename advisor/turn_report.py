from __future__ import annotations

import collections
import json
import os
import sqlite3
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common
from decisions import dbopen



def current_run():
    return common.RUN_DIR


def _interrupts(run_dir):
    counts = {k: collections.Counter() for k in ("all", "conf", "btl", "occ")}
    db = os.path.join(run_dir, "decisions.sqlite")
    if not os.path.isfile(db):
        return counts
    con = dbopen.connect(db)
    try:
        rows = con.execute("SELECT campaign_id, turn, kind, confirmed FROM interrupt_decisions")
        for camp, turn, kind, conf in rows:
            key = (camp, turn)
            counts["all"][key] += 1
            counts["conf"][key] += conf or 0
            if kind == "pre_battle":
                counts["btl"][key] += 1
            elif kind == "occupation":
                counts["occ"][key] += 1
    finally:
        con.close()
    return counts


def turns(run_dir):
    path = os.path.join(run_dir, "loop_report.jsonl")
    recs = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip():
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    continue
    ic = _interrupts(run_dir)
    out, camp, last_fac, prev_end = [], 0, None, None
    for i, r in enumerate(recs):
        if r.get("kind") != "turn":
            continue
        nxt = recs[i + 1] if i + 1 < len(recs) else {}
        gv = nxt if nxt.get("kind") == "growth_check" else {}
        tg, st = r.get("target") or {}, r.get("state") or {}
        fac = tg.get("campaign_id") or st.get("faction")
        key = (tg.get("campaign_uuid"), r.get("turn"))
        if fac != last_fac:
            camp, last_fac, prev_end = camp + 1, fac, None
        dur = (r["ts"] - prev_end) if prev_end else None
        prev_end = r["ts"]
        m = gv.get("metrics") or {}
        out.append({
            "camp": camp, "faction": fac, "turn": r.get("turn"),
            "actions": r.get("actions"), "confirmed": r.get("confirmed"),
            "ended_by": r.get("ended_by"),
            "settlements": st.get("settlements"), "lord_level": st.get("lord_level"),
            "armies": st.get("armies"), "treasury": st.get("treasury"),
            "power_rank": tg.get("power_rank"),
            "interrupts": ic["all"][key], "interrupts_confirmed": ic["conf"][key],
            "battles": ic["btl"][key], "occupations": ic["occ"][key],
            "seconds": dur, "inter_turn_wait": (r.get("inter_turn") or {}).get("waited_s"),
            "growth": gv.get("reason"),
            "settlements_gain": (m.get("settlements") or {}).get("gain"),
            "lord_gain": (m.get("lord_level") or {}).get("gain"),
            "picks": collections.Counter(p.get("action") for p in (r.get("picks") or [])),
        })
    return out


def _short(faction):
    f = str(faction or "?")
    for p in ("wh2_", "wh3_", "wh_"):
        f = f.replace(p, "", 1)
    for p in ("main_", "dlc0", "dlc1", "dlc2", "cp1_", "twa03_"):
        f = f.replace(p, "", 1)
    return f.lstrip("0123456789_")[:22]


def per_turn_table(rows):
    head = ("| C | faction | T | act | cf | ended_by | set | lvl | arm | treasury | pwr | int "
            "| btl | occ | secs | wait | growth |")
    out = [head, "|" + "---|" * 17]
    for r in rows:
        g = r["growth"] or ""
        if g == "before_first_check":
            g = "--"
        elif g in ("stagnant", "growing"):
            g = "%s (s%+d l%+d)" % (g[:4], r["settlements_gain"] or 0, r["lord_gain"] or 0)
        out.append("| %d | %s | %g | %d | %d | %s | %g | %g | %g | %d | %g | %d/%d | %d | %d "
                   "| %s | %s | %s |"
                   % (r["camp"], _short(r["faction"]), r["turn"] or 0, r["actions"] or 0,
                      r["confirmed"] or 0, r["ended_by"], r["settlements"] or 0,
                      r["lord_level"] or 0, r["armies"] or 0, int(r["treasury"] or 0),
                      r["power_rank"] or 0, r["interrupts_confirmed"], r["interrupts"],
                      r["battles"], r["occupations"],
                      ("%.0f" % r["seconds"]) if r["seconds"] else "-",
                      ("%.0f" % r["inter_turn_wait"]) if r["inter_turn_wait"] is not None else "-",
                      g))
    return "\n".join(out)


def by_turn_table(rows):
    by = collections.defaultdict(list)
    for r in rows:
        by[int(r["turn"] or 0)].append(r)
    out = ["| turn | campaigns alive | actions | confirmed | conf% | ended action_cap | battles "
           "| occupations | interrupts (conf%) | median secs | inter-turn wait |",
           "|" + "---|" * 11]
    for t in sorted(by):
        rs = by[t]
        acts = sum(r["actions"] or 0 for r in rs)
        conf = sum(r["confirmed"] or 0 for r in rs)
        it = sum(r["interrupts"] for r in rs)
        ic = sum(r["interrupts_confirmed"] for r in rs)
        durs = [r["seconds"] for r in rs if r["seconds"]]
        waits = [r["inter_turn_wait"] for r in rs if r["inter_turn_wait"] is not None]
        out.append("| %d | %d | %d | %d | %.0f%% | %d/%d | %d | %d | %d (%.0f%%) | %s | %s |"
                   % (t, len(rs), acts, conf, 100.0 * conf / acts if acts else 0,
                      sum(1 for r in rs if r["ended_by"] == "action_cap"), len(rs),
                      sum(r["battles"] for r in rs), sum(r["occupations"] for r in rs),
                      it, (100.0 * ic / it) if it else 0,
                      ("%.0fs" % statistics.median(durs)) if durs else "-",
                      ("%.0fs" % statistics.mean(waits)) if waits else "-"))
    return "\n".join(out)


def main():
    run_dir = sys.argv[1] if len(sys.argv) > 1 else current_run()
    rows = turns(run_dir)
    if not rows:
        raise SystemExit("no turn rows in %s" % run_dir)
    ends = collections.Counter(r["ended_by"] for r in rows)
    print("# turn report -- %s" % run_dir)
    print()
    print("%d turns over %d campaigns | %d actions, %d confirmed (%.0f%%) | end reasons: %s"
          % (len(rows), rows[-1]["camp"], sum(r["actions"] or 0 for r in rows),
             sum(r["confirmed"] or 0 for r in rows),
             100.0 * sum(r["confirmed"] or 0 for r in rows)
             / max(sum(r["actions"] or 0 for r in rows), 1),
             ", ".join("%s %d" % kv for kv in ends.most_common())))
    print()
    print("## every turn played")
    print()
    print(per_turn_table(rows))
    print()
    print("## rolled up by turn number")
    print()
    print(by_turn_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
