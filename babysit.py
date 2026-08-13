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

RUN = {"campaigns": 500, "turns": 20, "model": "catboost",
       "retrain": True, "retrain_every": 10,
       # Model-driven play raised to 60%: the two learned arms at 0.3 each, random at 0.3
       # for continued coverage, ruleset at 0.1. Sums to 1.0.
       "strategies": "marwil_gnn=0.3,greedy_catboost=0.3,random=0.3,ruleset=0.1",
       "ruleset": "probe_gaps",
       # Every start on the map, not the cutscene-filtered subset.
       "factions": "all",
       "campaign": "Realm of Chaos",
       # dev is ON: the diagnostic streams, including the panel dump, are the reason this
       # run exists in this shape. A relaunch that quietly drops them collects a campaign
       # that cannot be debugged when it wedges -- which is the failure that started this.
       "dev": True}


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
    """False when the report cannot be read, not an exception."""
    p = newest_session_report()
    if not p:
        return False
    try:
        rep = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError) as e:
        note("session report unreadable (%s) -- assuming not complete" % repr(e)[:80])
        return False
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
    # Every field comes from RUN so a relaunch matches how the run was started by hand --
    # a babysitter that quietly brings the run back in a different shape is worse than one
    # that does nothing, because the corpus keeps growing either way. dev included.
    for step in runctl.up(RUN["campaigns"], RUN["turns"], model=RUN["model"],
                          retrain=RUN["retrain"], retrain_every=RUN["retrain_every"],
                          dev=RUN.get("dev", False), with_ui=True,
                          strategies=RUN["strategies"], ruleset=RUN["ruleset"],
                          factions=RUN["factions"], campaign=RUN["campaign"]):
        note(step)
    return 0


def loop(every_s):
    """Keep checking until the session finishes or BABYSIT_OFF appears."""
    note("babysit loop starting: every %.0fs" % every_s)
    while True:
        try:
            if os.path.exists(OFF_FLAG):
                note("BABYSIT_OFF present -- loop exiting")
                return 0
            if session_complete():
                note("session complete -- loop exiting")
                return 0
            main()
        except Exception as e:                                  # noqa: BLE001
            note("check failed (continuing): %r" % (e,))
        time.sleep(every_s)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        secs = 300.0
        if i + 1 < len(sys.argv):
            try:
                secs = float(sys.argv[i + 1])
            except ValueError:
                pass
        raise SystemExit(loop(secs))
    raise SystemExit(main())
