from __future__ import annotations

"""Every check in this project, run one way.

There were twenty-three harnesses and no way to run them. Worse, they disagreed about how
they are invoked: `decisions.test_store` works as `python -m`, `bus/test_bus.py` only as a
script -- because `bus/` is a package that shadows `bus/bus.py`, so `-m bus.test_bus` makes
`from bus import Bus` resolve to the package and fail with AttributeError. That looks
exactly like a broken test, and it is why a sweep reported nine failures that were all
green when run correctly.

So the invocation is recorded here, once, next to the check it belongs to. A harness that
is not in this table is not part of the build.

    python check.py              -- everything that runs with the game down
    python check.py --with-game  -- also the ones needing a live WH3 + bus
    python check.py --list

Exit code 1 if any check that was supposed to pass did not.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

ROOT = common.ROOT
PY = common.VENV_PY

# name, argv (relative to ROOT), needs_game, what it proves
CHECKS = [
    ("store", ["-m", "decisions.test_store"], False,
     "record/read round-trip, view compatibility, layout invariant"),
    ("journal", ["-m", "decisions.test_journal"], False,
     "the jsonl append path, and that no module has an undefined global"),
    ("cco_audit", ["-m", "decisions.cco_audit"], False,
     "every CCO property route the collector reads exists on that context"),
    ("dilemma_audit", ["-m", "decisions.dilemma_audit"], False,
     "recorded dilemmas carry all their options"),
    ("lua_syntax", ["bus/test_lua_syntax.py"], False,
     "every lua fragment the collector sends compiles"),
    ("bus_seq", ["bus/test_bus.py"], False,
     "concurrent processes allocate unique strictly-increasing sequence numbers"),
    ("bus_stats", ["bus/test_bus_stats.py"], False,
     "command classifier, sqlite accumulate/flush, Bus.send instrumentation"),
    ("input", ["input/test_input.py"], False,
     "the input stream emits rows on a monotonic clock"),
    ("logs", ["logs/test_logs.py"], False,
     "the log stream captures from byte 0 and survives rotation"),
    ("campaign_swap", ["manager/test_campaign_swap.py"], False,
     "the campaign splitter cuts a game log at the right boundary"),
    ("graph_invariants", ["-m", "advisor.mapgraph.invariants"], False,
     "the action is a node, the loss compares candidates, catalogue ids are injective"),
    ("graph_wl", ["-m", "advisor.mapgraph.wl", "--selftest"], False,
     "the WL identity check fails on the bug and passes on the fix"),
    ("graph_guard", ["advisor/mapgraph/guard.py"], False,
     "cross-entity arithmetic raises, so derived features stay unwritable"),
    ("options", ["-m", "advisor.test_options"], False,
     "generate -> gate -> store end to end: nothing gated is stored, and options.py "
     "touches no bus, db or file"),
    ("coverage_selftest", ["-m", "decisions.coverage", "--selftest"], False,
     "the acceptance gate can be seen failing -- a constant field is reported, a "
     "varying one is not"),

    # need a live game, a bus, or a populated corpus
    ("coverage", ["-m", "decisions.coverage"], True,
     "no recorded field is constant/null without a stated reason"),
    ("graph_build", ["-m", "advisor.mapgraph.test_build"], True,
     "real decisions build graphs with the expected node and edge counts"),
    ("panels", ["advisor_ui/lint_panels.py"], True,
     "every UI panel renders, no empty columns, no leaked markup"),
    ("bus_live", ["bus/test_bus_live.py"], True, "a live eval round-trips through the bus"),
    ("shots", ["shots/test_shots.py"], True, "the screenshot stream writes jpgs"),
    ("manager_live", ["manager/test_manager.py"], True, "the recorder captures a fresh game log"),
    ("integrated", ["manager/test_integrated_live.py"], True, "every stream reports bus_available"),
    ("cco_commands", ["launcher/verify_cco_commands.py"], True,
     "every executor CCO command is accepted by the running game"),
]


def main(argv):
    if "--list" in argv:
        for name, cmd, ng, what in CHECKS:
            print("  %-17s %-9s %s" % (name, "game" if ng else "offline", what))
        return 0
    with_game = "--with-game" in argv
    only = [a for a in argv if not a.startswith("--")]
    rows, failed = [], []
    for name, cmd, needs_game, _what in CHECKS:
        if only and name not in only:
            continue
        if needs_game and not with_game:
            rows.append((name, "skip", "needs a live game -- rerun with --with-game"))
            continue
        try:
            r = subprocess.run([PY] + cmd, cwd=ROOT, capture_output=True, text=True,
                               timeout=600)
            out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            last = next((l for l in reversed(out) if l.strip()), "(no output)")
            ok = r.returncode == 0
        except subprocess.TimeoutExpired:
            ok, last = False, "timed out after 600s"
        rows.append((name, "ok" if ok else "FAIL", last[:96]))
        if not ok:
            failed.append(name)
    for name, status, last in rows:
        print("  %-6s %-17s %s" % (status, name, last))
    print()
    if failed:
        print("%d FAILED: %s" % (len(failed), ", ".join(failed)))
    else:
        print("all checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
