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
     _h(t1={"settlements": 1, "lord_level": 1}), 4, False, "before_first_check"),

    ("flat on both metrics abandons",
     _h(t1={"settlements": 1, "lord_level": 1}, t5={"settlements": 1, "lord_level": 1}),
     5, True, "stagnant"),

    ("a settlement gained keeps it running",
     _h(t1={"settlements": 1, "lord_level": 1}, t5={"settlements": 2, "lord_level": 1}),
     5, False, "growing"),

    ("a lord level gained keeps it running -- either metric satisfies the bar",
     _h(t1={"settlements": 1, "lord_level": 1}, t5={"settlements": 1, "lord_level": 2}),
     5, False, "growing"),

    ("losing a settlement abandons, like flat",
     _h(t1={"settlements": 2, "lord_level": 1}, t5={"settlements": 1, "lord_level": 1}),
     5, True, "stagnant"),

    ("a wounded lord abandons immediately, BEFORE the first-check guard",
     _h(t1={"settlements": 1, "lord_level": 1, "ll_wounded": True}), 1,
     True, "legendary_lord_wounded"),

    ("a wounded lord abandons at a normal turn too",
     _h(t1={"settlements": 1, "lord_level": 1},
        t6={"settlements": 3, "lord_level": 4, "ll_wounded": True}), 6,
     True, "legendary_lord_wounded"),

    ("no baseline turn recorded means nothing is evaluable, and it runs on",
     _h(t5={"settlements": 1, "lord_level": 1}), 5, False, "no_metric_evaluable"),

    ("a hole at the window turn drops that metric silently",
     _h(t2={"settlements": 1, "lord_level": 1}, t7={"settlements": 1, "lord_level": 1}),
     7, True, "stagnant"),
]


def check_constants(fail):
    for name, want in (("GROWTH_WINDOW", 5), ("GROWTH_LORD_WINDOW", 4),
                       ("GROWTH_MIN_GAIN", 1), ("GROWTH_FIRST_CHECK_TURN", 5)):
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
    _, at5 = L._growth_verdict(
        _h(t1={"settlements": 1, "lord_level": 1}, t5={"settlements": 1, "lord_level": 1}), 5)
    _, at6 = L._growth_verdict(
        _h(t1={"settlements": 1, "lord_level": 1}, t2={"settlements": 1, "lord_level": 1},
           t6={"settlements": 1, "lord_level": 1}), 6)
    if at5["metrics"]["settlements"]["window"] != 4:
        fail("settlements window at turn 5 is %r, pinned at 4 (max(5-5, first_turn=1))"
             % at5["metrics"]["settlements"]["window"])
    if at6["metrics"]["settlements"]["window"] != 5:
        fail("settlements window at turn 6 is %r, pinned at 5"
             % at6["metrics"]["settlements"]["window"])


def check_gate_reads_the_close_row(fail):
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop.py"),
               encoding="utf-8").read()
    if "st = _turn_close_row()" not in src:
        fail("the pre-end-turn gate check no longer reads the turn_close row -- a battle "
             "won during the final turn (levelling the lord, taking a settlement) must "
             "count before the campaign is abandoned")
    if 'row.get("turn_close")' not in src:
        fail("growth_hist is no longer fed from the turn_close row -- the gate would go "
             "back to judging turn-open state and miss gains earned during the turn")


def check_done_is_not_grew(fail):
    for hist, turn in ((_h(t1={"settlements": 1, "lord_level": 1},
                           t5={"settlements": 2, "lord_level": 1}), 5),
                       (_h(t1={"settlements": 1, "lord_level": 1},
                           t5={"settlements": 1, "lord_level": 1}), 5)):
        done, detail = L._growth_verdict(hist, turn)
        if detail.get("evaluable") and done != (not detail["grew"]):
            fail("done=%r but grew=%r -- done has always been `not grew`"
                 % (done, detail["grew"]))


def main():
    problems = []
    for fn in (check_constants, check_cases, check_wounded_carries_no_measurement,
               check_window_is_variable, check_gate_reads_the_close_row,
               check_done_is_not_grew):
        fn(problems.append)
    for p in problems:
        print("  FAIL %s" % p)
    if problems:
        print("\n%d pinned behaviour(s) of the abandonment gate changed. If that was "
              "deliberate, update this file in the same commit." % len(problems))
        return 1
    print("growth gate unchanged: %d cases, 4 constants, the close-row feed, and the "
          "wounded branch still measures nothing" % len(CASES))
    return 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
