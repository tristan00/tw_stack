from __future__ import annotations

"""The abandonment gate, pinned.

`_growth_verdict` is RUN CONTROL: it decides whether to stop playing a campaign. It is not
a measurement of how a campaign went, and the two were confused for a long time -- the
dashboard rendered this verdict AS the campaign's growth, which is why 147 of 152 filled
cells read `N -> N` and not one could ever read up. The verdict is only recorded when the
gate FIRED, and the gate fires exactly when the gain is below `GROWTH_MIN_GAIN`.

The reporting fix separates the two. This file exists so that separation cannot quietly
change what the gate does. Every campaign the run abandons, and at which turn, is decided
here; a reporting change that shifted a threshold or reordered a branch would alter the
corpus itself and would be invisible in any dashboard test.

So: nothing below asserts that the gate is CORRECT. It asserts that the gate is UNCHANGED.
Two behaviours pinned here are known to be arguable and are deliberately pinned anyway:

  the wounded check runs BEFORE the first-check guard, so a lord wounded on turn 1 ends the
  campaign before the growth bar is legal (62 campaigns, 30 of them at turns 1-3), and

  the first evaluation compares turn 4 against turn 1 for both metrics, demanding a
  settlement or a lord level in three turns.

If either is changed on purpose, this file is the thing that is supposed to fail. Edit it
in the same commit as the change, and not before.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from advisor import loop as L


def _h(**turns):
    """growth_hist as loop.py builds it: {turn: {metric: value}}."""
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
    """The thresholds are the gate. A silent edit here changes the whole corpus."""
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
    """The wounded branch returns empty metrics, which is why those campaigns render with"""
    done, detail = L._growth_verdict(
        _h(t1={"settlements": 1, "lord_level": 1, "ll_wounded": True}), 1)
    if detail.get("metrics") != {}:
        fail("the wounded branch now carries metrics %r -- it has always returned {}, and "
             "the dashboard's 'lord wounded, nothing measured' copy depends on it"
             % (detail["metrics"],))
    if detail.get("evaluable") != []:
        fail("the wounded branch now reports evaluable %r" % (detail["evaluable"],))


def check_window_is_variable(fail):
    """then_turn = max(t - window, first_turn), so the window is 3 at turn 4 and 4 at turn"""
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
    """`done` means ABANDON. It is `not grew` on every path that evaluated anything."""
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
