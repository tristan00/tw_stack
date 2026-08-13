from __future__ import annotations

"""Read access to the precomputed analytics tables.

The mirror of `db.py`, for the sidecar the analytics runner writes. Two differences, both
deliberate:

1. The file may not exist. A fresh run dir has no analytics until the runner has run once,
   and that is a state the dashboard must be able to describe -- "nobody has built this
   yet" is not the same as "there is nothing to show". `connect()` returns None rather than
   creating an empty database, so the two cannot be confused.

2. The stamp is over `analytics_state`, not over the corpus. These tables change when the
   RUNNER folds, which is a different moment from when the corpus gains a row -- and the
   gap between the two is exactly the staleness the freshness block reports.

Note this connection is read-only from the API's side, the same as the corpus. Nothing in
`advisor_api` writes anything, anywhere; the rebuild control asks the runner to do it.
"""

import os
import sqlite3
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisor_api import db as _db
from analytics import store as _store

_local = threading.local()

_STAMP_SQL = (
    "SELECT COALESCE(SUM(watermark), 0) FROM analytics_state",
    "SELECT COALESCE(SUM(rows), 0) FROM analytics_state",
    # Formula versions are in the stamp so that bumping one invalidates every memoized
    # answer immediately. Without it a rebuilt table would be served through caches keyed
    # on a watermark that happens to land on the same number.
    "SELECT COALESCE(SUM(formula_version), 0) FROM analytics_state",
)


def path(run: str | None = None) -> str:
    return _store.analytics_path(run or _db.run_dir())


def connect(run: str | None = None):
    """One read-only connection per (thread, path), or None when nothing is built yet."""
    p = path(run)
    cache = getattr(_local, "cons", None)
    if cache is None:
        cache = _local.cons = {}
    con = cache.get(p)
    if con is None or not os.path.isfile(p):
        if not os.path.isfile(p):
            cache.pop(p, None)
            return None
        con = cache[p] = _store.connect(p, readonly=True)
    return con


def stamp(run: str | None = None) -> tuple:
    """Changes exactly when the runner folds. Same per-probe guard as `db.stamp`: a probe"""
    con = connect(run)
    out = []
    if con is None:
        out.append("absent")
    else:
        for sql in _STAMP_SQL:
            try:
                row = con.execute(sql).fetchone()
                out.append((row[0] if row else 0) or 0)
            except sqlite3.Error as e:
                out.append("err:%s" % e)
    base = path(run)
    for p in (base, base + "-wal"):
        try:
            out.append(os.path.getsize(p))
        except OSError:
            out.append(0)
    return tuple(out)


def cached(fn):
    """Memoize on the analytics stamp. Late-bound, for the reason `db.cached` documents."""
    return _db.cached_on(lambda: stamp())(fn)


def tenant_state(name: str, run: str | None = None) -> dict:
    con = connect(run)
    if con is None:
        return {}
    try:
        return _store.state(con, name)
    except sqlite3.Error:
        return {}


def all_state(run: str | None = None) -> list:
    con = connect(run)
    if con is None:
        return []
    try:
        return _store.all_state(con)
    except sqlite3.Error:
        return []


def rows(sql: str, args=(), run: str | None = None) -> list:
    """Every read goes through here so a missing analytics db is one branch, not many."""
    con = connect(run)
    if con is None:
        return []
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.Error:
        return []


def one(sql: str, args=(), run: str | None = None) -> dict | None:
    got = rows(sql, args, run)
    return got[0] if got else None
