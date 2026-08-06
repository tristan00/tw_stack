from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from store import DecisionStore, IncompatibleStore

RUNS_ROOT = r"D:/twdata/runs/human"


def audit_run(run_dir):
    s = DecisionStore(run_dir)
    try:
        rows = s.interrupt_rows()
    finally:
        s.close()

    seen = multi = 0
    bad = []
    for r in rows:
        if "dilemma" not in str(r.get("screen") or ""):
            continue
        seen += 1
        opts = r.get("options") or {}
        if len(opts) < 2:
            continue
        multi += 1
        vals = [v for v in opts.values() if isinstance(v, dict)]
        problems = []
        ids = {v.get("dilemma_id") for v in vals if v.get("dilemma_id")}
        if len(ids) != 1:
            problems.append("dilemma_id not single: %s" % sorted(ids))
        oids = [v.get("option_id") for v in vals]
        if any(not o for o in oids):
            problems.append("missing option_id (%d of %d)" % (sum(1 for o in oids if not o),
                                                              len(oids)))
        elif len(set(oids)) != len(oids):
            problems.append("duplicate option_id: %s" % oids)
        if any(not v.get("text") for v in vals):
            problems.append("missing label (%d of %d)"
                            % (sum(1 for v in vals if not v.get("text")), len(vals)))
        if not r.get("chosen"):
            problems.append("no chosen option recorded")
        if problems:
            bad.append((r.get("campaign_id"), r.get("turn"), r.get("screen"), problems))
    return seen, multi, bad


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else RUNS_ROOT
    runs = [root] if os.path.exists(os.path.join(root, "decisions.sqlite")) else \
        sorted(os.path.join(root, d) for d in os.listdir(root)
               if os.path.isdir(os.path.join(root, d)))
    tot_seen = tot_multi = 0
    tot_bad = []
    for run in runs:
        if not os.path.exists(os.path.join(run, "decisions.sqlite")):
            continue
        try:
            seen, multi, bad = audit_run(run)
        except IncompatibleStore as e:
            print("%-28s SKIPPED incompatible store: %s" % (os.path.basename(run), e))
            continue
        if not seen:
            continue
        tot_seen += seen
        tot_multi += multi
        tot_bad.extend(bad)
        print("%-28s dilemma_rows=%-4d multi_option=%-4d incomplete=%d"
              % (os.path.basename(run), seen, multi, len(bad)))
        for camp, turn, screen, problems in bad:
            print("   !! %s turn=%s %s" % (camp, turn, screen))
            for p in problems:
                print("        - %s" % p)
    print()
    print("TOTAL dilemma rows=%d  multi-option=%d  incomplete=%d"
          % (tot_seen, tot_multi, len(tot_bad)))
    return 1 if tot_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
