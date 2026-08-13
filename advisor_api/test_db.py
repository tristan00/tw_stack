
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisor_api import db


def test_every_stamp_probe_executes():
    con = db.connect()
    bad = []
    for sql in db._STAMP_SQL:
        try:
            row = con.execute(sql).fetchone()
        except Exception as e:
            bad.append("%s -> %s" % (sql, e))
            continue
        if row is None or not isinstance(row[0], (int, float, type(None))):
            bad.append("%s -> non-numeric %r" % (sql, row and row[0]))
    assert not bad, "stamp probes that do not execute:\n  " + "\n  ".join(bad)


def test_stamp_carries_no_error_markers():
    st = db.stamp()
    errs = [p for p in st if isinstance(p, str) and p.startswith("err:")]
    assert not errs, "stamp degraded to error markers: %s" % errs


def test_stamp_is_not_constant_zero():
    st = db.stamp()
    assert any(p for p in st), "stamp is all zeros -- cache would never invalidate: %r" % (st,)


def test_cache_returns_same_value_within_a_stamp():
    con = db.connect()
    calls = []

    @db.cached
    def counted(con):
        calls.append(1)
        return con.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]

    a, b = counted(con), counted(con)
    assert a == b
    assert len(calls) == 1, "cached() recomputed within one stamp"


def test_cache_invalidates_when_stamp_moves(monkeypatch=None):
    con = db.connect()
    calls = []

    @db.cached
    def counted(con):
        calls.append(1)
        return len(calls)

    counted(con)
    real = db.stamp
    db.stamp = lambda run=None: ("moved",)
    try:
        counted(con)
    finally:
        db.stamp = real
    assert len(calls) == 2, "cached() did not recompute after the stamp moved"


def test_columns_reports_real_schema():
    con = db.connect()
    cols = db.columns(con, "action_offers")
    assert {"decision_id", "action_type", "rank"} <= cols, cols
    assert db.columns(con, "no_such_table_xyz") == set()


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("ok   %s" % name)
        except AssertionError as e:
            fails += 1
            print("FAIL %s\n     %s" % (name, e))
        except Exception as e:
            fails += 1
            print("ERR  %s\n     %r" % (name, e))
    print("\n%d failure(s)" % fails)
    raise SystemExit(1 if fails else 0)
