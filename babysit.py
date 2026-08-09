from __future__ import annotations

import json
import os
import sys
import time

TW_STACK = r"D:\tw_stack"
sys.path.insert(0, TW_STACK)

import runctl

RUNS_ROOT = r"D:\twdata\runs\human"
OFF_FLAG = r"D:\twdata\BABYSIT_OFF"
STAMP = r"D:\twdata\logs\services\babysitter_last_relaunch.txt"
LOG = r"D:\twdata\logs\services\babysitter.log"
STALL_S = 1200
RELAUNCH_COOLDOWN_S = 1800

RUN = {"campaigns": 100, "turns": 20, "model": "catboost", "retrain_every": 10,
       "strategies": "exploit_tree=0.5,random=0.4,ruleset=0.1", "ruleset": "v1",
       "factions": ("wh2_main_hef_nagarythe,wh3_main_cth_the_northern_provinces,"
                    "wh3_dlc27_sla_masque_of_slaanesh,wh_main_vmp_schwartzhafen,"
                    "wh2_main_def_cult_of_pleasure")}


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
    for step in runctl.up(RUN["campaigns"], RUN["turns"], model=RUN["model"],
                          retrain_every=RUN["retrain_every"], dev=True, with_ui=False,
                          strategies=RUN["strategies"], ruleset=RUN["ruleset"],
                          factions=RUN["factions"]):
        note(step)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
