r"""Data view: EVERY game-state data point, SEQUENTIALLY.

Writes a JSONL dataset of every TWSTATE record (events, player_snapshots, resource changes, ...) in
capture order -- the full game-state timeline for the run (or one turn). NOT player-filtered: all
factions. Each output row keeps its non-null fields plus seq / t (game-time) / turn / kind / event.

    python game_state_sequence.py <run_dir> [turn] [out.jsonl]

Prints a summary (counts by kind + event, turn span) and the first few records; the full dataset is
the written file.
"""
import json
import os
import sys
from collections import Counter

import structurer


def main():
    run = sys.argv[1]
    turn = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
    exports = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
    os.makedirs(exports, exist_ok=True)
    run_name = os.path.basename(run.rstrip("/\\"))
    default = os.path.join(exports, "gamestate_%s_%s.jsonl" % (run_name, ("t%d" % turn if turn else "all")))
    out = sys.argv[3] if len(sys.argv) > 3 else default

    n = 0
    kinds = Counter()
    events = Counter()
    turns = set()
    first = []
    with open(out, "w", encoding="utf-8") as f:
        for r in structurer.iter_twstate(run, turn):
            rec = {k: v for k, v in r.items() if v not in (None, "")}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            kinds[r.get("kind")] += 1
            if r.get("event"):
                events[r["event"]] += 1
            if r.get("turn") is not None:
                turns.add(r["turn"])
            if len(first) < 8:
                first.append(rec)

    print("wrote %d game-state data points -> %s" % (n, out))
    print("player  : %s" % structurer.player_faction(run))
    print("turns   : %s" % sorted(turns))
    print("by kind : %s" % dict(kinds))
    print("top events:")
    for ev, c in events.most_common(15):
        print("   %6d  %s" % (c, ev))
    print("--- first records ---")
    for r in first:
        keys = [k for k in ("seq", "t", "turn", "kind", "event") if k in r]
        print("   " + json.dumps({k: r[k] for k in keys}, ensure_ascii=False))


if __name__ == "__main__":
    main()
