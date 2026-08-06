from __future__ import annotations

import collections
import glob
import io
import json
import os
import re
import sys

LOGS = r"D:\twdata\logs\advisor"
RUNS = r"D:\twdata\runs\human"


def _read(path):
    return io.open(path, encoding="utf-8", errors="replace").read().splitlines()


def session_log():
    if len(sys.argv) > 1:
        return sys.argv[1]
    p = os.path.join(LOGS, "CURRENT_SESSION_LOG.txt")
    return io.open(p, encoding="utf-8-sig").read().strip()


ROW = re.compile(r"^\s{2,}(\w+)\s+(\S.*?)\s+(OK|FAIL)(?:\s+\((\w+)\))?\s*$")
TURN_DONE = re.compile(r"turn ([\d.]+) done: (\d+) actions \((\d+) confirmed\), ended by (\w+)")
CAMP_END = re.compile(r"campaign (\d+) -> (\w+) in (\d+)s")


def main():
    log = session_log()
    lines = _read(log)
    print("=" * 84)
    print("EFFICIENCY REPORT -- %s" % os.path.basename(log))
    print("=" * 84)

    turns = [TURN_DONE.search(l) for l in lines]
    turns = [t for t in turns if t]
    camps = [CAMP_END.search(l) for l in lines]
    camps = [c for c in camps if c]
    print("\nPROGRESS")
    print("  turns completed : %d" % len(turns))
    if camps:
        print("  campaigns ended : %d" % len(camps))
        oc = collections.Counter(c.group(2) for c in camps)
        for k, v in oc.most_common():
            print("      %-12s %d" % (k, v))
        secs = [int(c.group(3)) for c in camps]
        print("  seconds/campaign: median %ds  max %ds" % (sorted(secs)[len(secs) // 2], max(secs)))
    ends = collections.Counter(t.group(4) for t in turns)
    if ends:
        print("  turn ended by   : %s" % dict(ends))

    out, fails = collections.Counter(), collections.defaultdict(collections.Counter)
    for l in lines:
        m = ROW.match(l)
        if m:
            act, _t, res, why = m.groups()
            out[(act, res)] += 1
            if res == "FAIL":
                fails[act][why or "?"] += 1
    acts = sorted({a for a, _ in out}, key=lambda a: -(out[(a, "OK")] + out[(a, "FAIL")]))
    tot_ok = sum(out[(a, "OK")] for a in acts)
    tot_bad = sum(out[(a, "FAIL")] for a in acts)
    print("\nRELIABLE   overall confirm rate: %d/%d = %.0f%%"
          % (tot_ok, tot_ok + tot_bad, 100.0 * tot_ok / max(1, tot_ok + tot_bad)))
    print("  %-20s %5s %5s %6s   %s" % ("ACTION", "OK", "FAIL", "RATE", "WHY IT FAILED"))
    for a in acts:
        ok, bad = out[(a, "OK")], out[(a, "FAIL")]
        top = ", ".join("%s x%d" % (k, v) for k, v in fails[a].most_common(3))
        print("  %-20s %5d %5d %5.0f%%   %s" % (a, ok, bad, 100.0 * ok / max(1, ok + bad), top))

    run = os.path.join(RUNS, "run")
    tp = os.path.join(run, "trace.jsonl")
    if not tp:
        print("\n(no trace.jsonl yet -- FAST/SMOOTH pending)")
        return
    evs = []
    for ln in _read(tp):
        try:
            evs.append(json.loads(ln))
        except Exception:
            pass
    spans = collections.defaultdict(list)
    for i, e in enumerate(evs):
        if e.get("kind") == "launcher" and e.get("stage") == "execute_start":
            for nxt in evs[i + 1:]:
                if nxt.get("kind") == "launcher" and nxt.get("stage") != "execute_start":
                    spans[e.get("action_type")].append(nxt["ts"] - e["ts"])
                    break
    print("\nFAST   (%s)" % os.path.basename(os.path.dirname(tp)))
    if spans:
        print("  %-20s %5s %8s %8s %9s" % ("ACTION", "N", "MEDIAN", "MAX", "TOTAL_S"))
        grand = 0.0
        for a, ds in sorted(spans.items(), key=lambda kv: -sum(kv[1])):
            ds.sort()
            grand += sum(ds)
            print("  %-20s %5d %7.1fs %7.1fs %8.1fs"
                  % (a, len(ds), ds[len(ds) // 2], ds[-1], sum(ds)))
        alld = sorted(d for v in spans.values() for d in v)
        print("  %-20s %5d %7.1fs %7.1fs %8.1fs"
              % ("ALL", len(alld), alld[len(alld) // 2], alld[-1], grand))

        print("\nSMOOTH   wasted wall-clock (time on FAILED actions)")
        waste = 0.0
        for a, ds in sorted(spans.items(), key=lambda kv: -sum(kv[1])):
            ok, bad = out[(a, "OK")], out[(a, "FAIL")]
            if not (ok + bad):
                continue
            frac_bad = bad / float(ok + bad)
            w = sum(ds) * frac_bad
            waste += w
            if w > 1.0:
                print("  %-20s %5.0f%% fail  ~%6.1fs wasted" % (a, 100 * frac_bad, w))
        print("  %-20s %11s ~%6.1fs of %.1fs total = %.0f%%"
              % ("TOTAL WASTE", "", waste, grand, 100.0 * waste / max(1.0, grand)))


if __name__ == "__main__":
    main()
