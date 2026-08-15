from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)

import journal

TURN_EVERY = 3.0
TARGET_EVERY = 10.0
HASH_EVERY = 5.0
SNAPSHOT_EVERY = 20.0


def main():
    run_dir = sys.argv[1] if len(sys.argv) > 1 else journal.current_run_dir()
    sys.stderr.write("human_poller -> %s\n" % run_dir)
    counts = {"turn": 0, "target": 0, "hash": 0, "snapshot": 0, "error": 0}
    last = {"turn": 0.0, "target": 0.0, "hash": 0.0, "snapshot": 0.0}
    seen_turn = [None]
    while True:
        now = time.time()
        try:
            if now - last["turn"] >= TURN_EVERY:
                last["turn"] = now
                t, _campaign = journal.request_turn(run_dir)
                counts["turn"] += 1
                if t != seen_turn[0]:
                    seen_turn[0] = t
                    last["target"] = 0.0
                    last["snapshot"] = 0.0
                    sys.stderr.write("human_poller: turn -> %s\n" % t)
            if now - last["hash"] >= HASH_EVERY:
                last["hash"] = now
                journal.request_hash(run_dir)
                counts["hash"] += 1
            if now - last["target"] >= TARGET_EVERY:
                last["target"] = now
                journal.request_target(run_dir)
                counts["target"] += 1
            if now - last["snapshot"] >= SNAPSHOT_EVERY:
                last["snapshot"] = now
                journal.request_snapshot(run_dir)
                counts["snapshot"] += 1
                sys.stderr.write("human_poller: %s\n" % counts)
        except Exception as e:
            counts["error"] += 1
            sys.stderr.write("human_poller: %s\n" % repr(e)[:140])
            time.sleep(2.0)
        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
