r"""eval.py -- offline self-test of the advisor (no game access).

Not about accuracy (per direction). It checks the PIPELINE end to end on recorded data:
  1. coverage   -- per type: decisions, how many have a linked chosen, avg options.
  2. sanity     -- within-decision ranking of the CHOSEN option (where does the model rank the option
                   the player actually took, among its own option set). A weak model still ranks
                   somewhere; this just proves scoring runs per option and produces an order.
  3. runtime    -- runtime.replay writes an advisor.log with a table per menu-open.

    python eval.py            # coverage + within-decision ranking from the cached dataset
    python eval.py <run>      # + run runtime.replay on that recorded run and report table count
"""
import json
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import models as M       # noqa: E402
import features as F     # noqa: E402


def _rank_of(chosen, tbl):
    for i, r in enumerate(tbl):
        if str(r["key"]) == str(chosen):
            return i + 1
    return None


def coverage_and_sanity():
    data = M.gather()                       # from cache
    models, counts, meta, state_models = M.load_all()
    by_type = defaultdict(list)
    for dec in data["decisions"]:
        by_type[dec["type"]].append(dec)
    print("%-11s %7s %8s %8s | %8s %6s %6s %8s" % (
        "type", "decis", "chosen", "avg_opt", "ranked", "top1", "top3", "meanrank"))
    for t in sorted(by_type):
        ds = by_type[t]
        chosen = [d for d in ds if d.get("chosen_linked")]
        avg = sum(len(d.get("options") or []) for d in ds) / max(1, len(ds))
        ranks = []
        for d in chosen:
            if not d.get("options"):
                continue
            tbl = M.score_decision(d, models, counts, meta, state_models=state_models)
            r = _rank_of(d["chosen"], tbl)
            if r:
                ranks.append(r)
        top1 = sum(1 for r in ranks if r == 1)
        top3 = sum(1 for r in ranks if r <= 3)
        mr = (sum(ranks) / len(ranks)) if ranks else None
        print("%-11s %7d %8d %8.1f | %8d %6d %6d %8s" % (
            t, len(ds), len(chosen), avg, len(ranks), top1, top3,
            ("%.1f" % mr) if mr is not None else "--"))


def main():
    coverage_and_sanity()
    if len(sys.argv) > 1:
        import runtime
        out, n = runtime.replay(sys.argv[1])
        print("\nruntime.replay -> %d tables at %s" % (n, out))


if __name__ == "__main__":
    main()
