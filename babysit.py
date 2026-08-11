from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

TW_STACK = common.ROOT
sys.path.insert(0, TW_STACK)

import runctl

RUNS_ROOT = common.native(common.RUNS_ROOT)
OFF_FLAG = common.BABYSIT_OFF
STAMP = common.BABYSIT_STAMP
LOG = common.BABYSIT_LOG
STALL_S = 1200
RELAUNCH_COOLDOWN_S = 1800

# What a relaunch must reproduce. This is the CORPUS-COLLECTION run, not a training run:
# mostly random play, no learner in the loop, no retraining. The learner is deliberately
# absent -- integrating it on too small a sample has historically produced an unstable model
# that then poisoned further training, so nothing trains until the corpus is ~10k decisions
# (roughly 350-400 campaigns at the observed 29 decisions/campaign).
#
# This block previously read exploit_tree=0.3, gnn=0.3, random=0.3, ruleset=0.1 with
# retrain_every=20 and campaigns=100. Had the babysitter ever fired against the collection
# run, a single crash would have silently switched play to 60% model-driven and started
# retraining every 20 campaigns -- and the only way to notice would have been auditing
# taken.policy long afterwards, by which point the corpus is mixed.
#
# retrain is False AND retrain_every is 0: runctl emits --retrain from `retrain` and
# --retrain-every from `retrain_every` independently, so both must be off.
RUN = {"campaigns": 500, "turns": 20, "model": "catboost",
       "retrain": False, "retrain_every": 0,
       "strategies": "random=0.96,ruleset=0.04", "ruleset": "probe_gaps",
       "factions": "all"}


def note(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def newest_session_report():
    paths = [os.path.join(RUNS_ROOT, f) for f in os.listdir(RUNS_ROOT)
             if f.startswith("session_") and f.endswith(".json")]
    return max(paths, key=os.path.getmtime) if paths else None


def session_complete():
    p = newest_session_report()
    if not p:
        return False
    rep = json.load(open(p, encoding="utf-8"))
    want = (rep.get("requested") or {}).get("campaigns") or 0
    return bool(want) and len(rep.get("campaigns") or []) >= want


def session_alive():
    return any("session.py" in row for row in runctl.status())


def log_age():
    with open(runctl.CURRENT_LOG, encoding="utf-8-sig") as fh:
        p = fh.read().strip()
    return time.time() - os.path.getmtime(p)


def cooled_down():
    try:
        last = float(open(STAMP, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return True
    return time.time() - last >= RELAUNCH_COOLDOWN_S


def main():
    if os.path.exists(OFF_FLAG):
        note("BABYSIT_OFF present -- exiting")
        return 0
    if session_complete():
        note("session complete -- not relaunching")
        return 0
    alive = session_alive()
    try:
        age = log_age()
    except OSError as e:
        age = None
        note("pointer/log unreadable: %r" % e)
    if alive and age is not None and age < STALL_S:
        note("ok: session alive, log %.0fs old" % age)
        return 0
    if not cooled_down():
        note("DEAD/STALLED (alive=%s age=%s) -- cooldown active, skipping" % (alive, age))
        return 0
    note("DEAD/STALLED (alive=%s age=%s) -- relaunching" % (alive, age))
    with open(STAMP, "w", encoding="utf-8") as fh:
        fh.write(str(time.time()))
    # with_ui=True and dev=False so a relaunch matches how the run was started by hand --
    # a babysitter that quietly brings the run back in a different shape is worse than one
    # that does nothing, because the corpus keeps growing either way.
    for step in runctl.up(RUN["campaigns"], RUN["turns"], model=RUN["model"],
                          retrain=RUN["retrain"], retrain_every=RUN["retrain_every"],
                          dev=False, with_ui=True,
                          strategies=RUN["strategies"], ruleset=RUN["ruleset"],
                          factions=RUN["factions"]):
        note(step)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
