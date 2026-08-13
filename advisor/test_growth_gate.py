from __future__ import annotations


import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from advisor import loop as L


def _h(**turns):
    return {int(t.lstrip("t")): v for t, v in turns.items()}


CASES = [
    ("before the first check, nothing is judged",
     _h(t1={"settlements": 1, "lord_level": 1}), 3, False, "before_first_check"),

    ("flat on both metrics abandons",
     _h(t1={"settlements": 1, "lord_level": 1}, t4={"settlements": 1, "lord_level": 1}),
     4, True, "stagnant"),

    ("a settlement gained keeps it running",
     _h(t1={"settlements": 1, "lord_level": 1}, t4={"settlements": 2, "lord_level": 1}),
     4, False, "growing"),

    ("a lord level gained keeps it running -- either metric satisfies the bar",
     _h(t1={"settlements": 1, "lord_level": 1}, t4={"settlements": 1, "lord_level": 2}),
     4, False, "growing"),

    ("losing a settlement abandons, like flat",
     _h(t1={"settlements": 2, "lord_level": 1}, t4={"settlements": 1, "lord_level": 1}),
     4, True, "stagnant"),

    ("a wounded lord abandons immediately, BEFORE the first-check guard",
     _h(t1={"settlements": 1, "lord_level": 1, "ll_wounded": True}), 1,
     True, "legendary_lord_wounded"),

    ("a wounded lord abandons at a normal turn too",
     _h(t1={"settlements": 1, "lord_level": 1},
        t5={"settlements": 3, "lord_level": 4, "ll_wounded": True}), 5,
     True, "legendary_lord_wounded"),

    ("no baseline turn recorded means nothing is evaluable, and it runs on",
     _h(t4={"settlements": 1, "lord_level": 1}), 4, False, "no_metric_evaluable"),

    ("a hole at the window turn drops that metric silently",
     _h(t2={"settlements": 1, "lord_level": 1}, t6={"settlements": 1, "lord_level": 1}),
     6, True, "stagnant"),
]


def check_constants(fail):
    for name, want in (("GROWTH_WINDOW", 4), ("GROWTH_LORD_WINDOW", 3),
                       ("GROWTH_MIN_GAIN", 1), ("GROWTH_FIRST_CHECK_TURN", 4)):
        got = getattr(L, name)
        if got != want:
            fail("%s is %r, pinned at %r -- changing it changes which campaigns are "
                 "abandoned and at which turn" % (name, got, want))
    if [m[0] for m in L.GROWTH_METRICS] != ["settlements", "lord_level"]:
        fail("GROWTH_METRICS is %r, pinned at settlements then lord_level"
             % ([m[0] for m in L.GROWTH_METRICS],))


def check_cases(fail):
    for name, hist, turn, want_done, want_reason in CASES:
        done, detail = L._growth_verdict(hist, turn)
        if done != want_done:
            fail("%s: done=%r, pinned at %r" % (name, done, want_done))
        if detail.get("reason") != want_reason:
            fail("%s: reason=%r, pinned at %r" % (name, detail.get("reason"), want_reason))


def check_wounded_carries_no_measurement(fail):
    done, detail = L._growth_verdict(
        _h(t1={"settlements": 1, "lord_level": 1, "ll_wounded": True}), 1)
    if detail.get("metrics") != {}:
        fail("the wounded branch now carries metrics %r -- it has always returned {}, and "
             "the dashboard's 'lord wounded, nothing measured' copy depends on it"
             % (detail["metrics"],))
    if detail.get("evaluable") != []:
        fail("the wounded branch now reports evaluable %r" % (detail["evaluable"],))


def check_window_is_variable(fail):
    _, at4 = L._growth_verdict(
        _h(t1={"settlements": 1, "lord_level": 1}, t4={"settlements": 1, "lord_level": 1}), 4)
    _, at5 = L._growth_verdict(
        _h(t1={"settlements": 1, "lord_level": 1}, t2={"settlements": 1, "lord_level": 1},
           t5={"settlements": 1, "lord_level": 1}), 5)
    if at4["metrics"]["settlements"]["window"] != 3:
        fail("settlements window at turn 4 is %r, pinned at 3 (max(4-4, first_turn=1))"
             % at4["metrics"]["settlements"]["window"])
    if at5["metrics"]["settlements"]["window"] != 4:
        fail("settlements window at turn 5 is %r, pinned at 4"
             % at5["metrics"]["settlements"]["window"])


def check_done_is_not_grew(fail):
    for hist, turn in ((_h(t1={"settlements": 1, "lord_level": 1},
                           t4={"settlements": 2, "lord_level": 1}), 4),
                       (_h(t1={"settlements": 1, "lord_level": 1},
                           t4={"settlements": 1, "lord_level": 1}), 4)):
        done, detail = L._growth_verdict(hist, turn)
        if detail.get("evaluable") and done != (not detail["grew"]):
            fail("done=%r but grew=%r -- done has always been `not grew`"
                 % (done, detail["grew"]))


def main():
    problems = []
    for fn in (check_constants, check_cases, check_wounded_carries_no_measurement,
               check_window_is_variable, check_done_is_not_grew):
        fn(problems.append)
    for p in problems:
        print("  FAIL %s" % p)
    if problems:
        print("\n%d pinned behaviour(s) of the abandonment gate changed. If that was "
              "deliberate, update this file in the same commit." % len(problems))
        return 1
    print("growth gate unchanged: %d cases, 4 constants, and the wounded branch still "
          "measures nothing" % len(CASES))
    return 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
