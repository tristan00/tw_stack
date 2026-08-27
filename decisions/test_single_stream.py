from __future__ import annotations


import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import common

BANNED = ("request_target", "target_row", "entity_target_rows", "write_diplo_state",
          "diplo_world", "_LUA_TARGET", "_LUA_ENTITY_TARGETS", "_LUA_DIPLO_WORLD",
          "target_rows", "diplo_state")

SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", ".venv", "venv"}

SELF = os.path.abspath(__file__)


def _sources(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            if f.endswith(".py"):
                p = os.path.join(dirpath, f)
                if os.path.abspath(p) != SELF:
                    yield p


def main():
    problems = []
    for path in _sources(common.ROOT):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for name in BANNED:
            if name in text:
                problems.append("%s contains %r -- the end-of-turn stream is deleted; "
                                "per-turn state comes from turn_open/turn_close only"
                                % (os.path.relpath(path, common.ROOT), name))
    loop_src = open(os.path.join(common.ADVISOR, "loop.py"), encoding="utf-8").read()
    if '"turn_open": row_open' not in loop_src:
        problems.append("loop.py no longer emits turn_open on the turn row")
    if '"state": state' in loop_src:
        problems.append("loop.py emits an end-of-turn state dict again")

    from decisions import pgtest
    from decisions.store import DecisionStore
    pgtest.fresh()
    try:
        st = DecisionStore("singlestream/run")
        names = {r[0]: r[1] for r in st.con.execute(
            "SELECT table_name, CASE table_type WHEN 'VIEW' THEN 'view' ELSE 'table' END"
            " FROM information_schema.tables WHERE table_schema='public'"
            " AND (table_name LIKE 'turn\\_%' OR table_name LIKE '%target%'"
            " OR table_name LIKE 'diplo\\_s%')")}
        for v in ("turn_bounds", "turn_open", "turn_close"):
            if names.get(v) != "view":
                problems.append("%s is %r in a fresh schema -- it must be a VIEW, never "
                                "a table someone can write to" % (v, names.get(v)))
        stray = [n for n, t in names.items() if t == "table"]
        if stray:
            problems.append("a fresh schema contains end-of-turn table(s) %s" % stray)
        st.close()
    finally:
        pgtest.drop()
    for p in problems:
        print("  FAIL %s" % p)
    if problems:
        print("\n%d single-stream violation(s): there is ONE state collection, the "
              "pre-decision snapshot, and ONE definition of per-turn state, the "
              "turn_open/turn_close views" % len(problems))
        return 1
    print("single stream: no banned end-of-turn identifier anywhere, loop emits turn_open")
    return 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
