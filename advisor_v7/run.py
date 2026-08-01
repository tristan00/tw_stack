r"""run.py -- the v7 driver: wire the three roles together and play.

    python advisor_v7/run.py <run_dir> [turns]

  RECORDER  already running inside the manager (stream `decisions`). It owns the bus for READING
            and is asked for data over <run_dir>/decisions_requests.jsonl.
  ADVISOR   policy.Policy + model.Ranker, featurizing DB records. Holds no bus.
  LAUNCHER  executor.Executor, which owns a bus for EXECUTION only.

If the watchdog declares the run stuck, the loop screenshots and raises GameStuck; this exits with
status 2 so the supervising process can kill the game and start a fresh run. It deliberately does
not try to "rescue" a frozen campaign -- an unverified rescue is worse than a clean restart.
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import loop as L                                            # noqa: E402
import policy as P                                          # noqa: E402
import model as M                                           # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 1
    import journal                                          # noqa: E402
    arg = sys.argv[1]
    turns = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    if arg not in ("auto", "-"):
        run_dir = arg
        if not os.path.isdir(run_dir):
            raise SystemExit("run dir does not exist: %s (start the manager first)" % run_dir)
    else:
        run_dir = journal.current_run_dir(timeout=30.0)

    from bus import Bus
    from executor import Executor
    ex = Executor(Bus())
    state = ex.ensure_campaign(fresh="--fresh" in sys.argv)
    print("campaign: %s" % json.dumps(state)[:200], flush=True)
    # a campaign start rotates the run dir -- re-resolve it
    if not state.get("already_in_campaign"):
        live = journal.current_run_dir(timeout=120.0)
        if live != run_dir:
            print("recorder rotated on the campaign swap: %s -> %s" % (run_dir, live), flush=True)
            run_dir = live
    ex.shots_dir = os.path.join(run_dir, "shots")
    ranker = M.Ranker()
    pol = P.Policy(ranker)
    print("advisor v7: model=%s  policy=%s  turns=%d  run=%s"
          % ("trained (%d rows)" % (ranker.meta or {}).get("rows", 0) if ranker.ready
             else "COLD (random policy)", turns, run_dir), flush=True)

    status = 0
    try:
        L.run_campaign(run_dir, ex, pol, turns=turns)
    except L.GameStuck as e:
        print("\nRUN ABANDONED (stuck): %s" % e, flush=True)
        status = 2
    finally:
        v = L.verify_streams(run_dir)
        print("\n=== FOUR-STREAM CHECK ===", flush=True)
        for k in ("turns_with_reward", "decision_points", "points_with_offers", "points_with_taken",
                  "taken_with_evidence", "counted", "offers", "scored_offers", "orphan_picks"):
            print("  %-22s %s" % (k, v[k]))
        ok = v["all_points_have_offers"] and v["all_taken_have_evidence"] and not v["orphan_picks"]
        print("  %-22s %s" % ("CONTRACT", "OK" if ok else "VIOLATED"))
        if not ok:
            status = status or 3
        print(json.dumps(v))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
