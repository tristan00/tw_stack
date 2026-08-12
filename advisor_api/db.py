"""Corpus access for the API.

Two things matter here and nothing else does.

1. The v1 table names are views over the interned store, written in terms of unz()/f32(),
   which only exist on a connection dbopen hands out. A plain sqlite3.connect() opens
   fine and then fails on the first SELECT, so every connection comes from dbopen.

2. Every aggregate in this app is a pure function of the corpus, and the corpus only ever
   grows. So a result stays valid until a row is appended. `stamp()` is that append
   marker and `cached()` memoizes on it -- which is why the API can afford to answer the
   same aggregates on every poll without recomputing them. The old UI recomputed six
   full-table aggregates on every refresh of every tab; this is the fix for that.
"""

from __future__ import annotations

import functools
import os
import sqlite3
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from decisions import dbopen

_local = threading.local()
_MISS = object()


def run_dir() -> str:
    """Re-resolved per call. The run dir pointer moves when a new run starts, and an API
    that cached it at import time would serve the previous run forever."""
    return common.RUN_DIR


def db_path(run: str | None = None) -> str:
    return os.path.join(run or run_dir(), "decisions.sqlite")


def connect(run: str | None = None) -> sqlite3.Connection:
    """One connection per (thread, path). uvicorn runs sync handlers on a thread pool and
    sqlite connections are not shareable across threads."""
    path = db_path(run)
    cache = getattr(_local, "cons", None)
    if cache is None:
        cache = _local.cons = {}
    con = cache.get(path)
    if con is None:
        con = cache[path] = dbopen.connect(path)
        con.row_factory = sqlite3.Row
    return con


_STAMP_SQL = (
    "SELECT MAX(decision_id) FROM decisions",
    # taken is keyed (decision_id, offer_seq, entity_seq) -- there is no taken_id. Getting
    # this wrong is what motivated the per-probe guard below: the first version wrapped all
    # three probes in one try, so this single bad column name zeroed the whole tuple, the
    # stamp became a constant, and every cache entry would have been pinned forever while
    # looking perfectly healthy. A stale cache that never expires is worse than no cache.
    "SELECT MAX(decision_id) FROM taken",
    "SELECT MAX(interrupt_id) FROM interrupts",
)


def stamp(run: str | None = None) -> tuple:
    """A cheap value that changes exactly when the corpus gains rows.

    Each probe is a MAX() over the leading column of an integer primary key -- one index
    lookup, microseconds -- where the aggregates it gates are hundreds of milliseconds.

    Every probe is guarded independently and a failing one contributes its exception text
    rather than 0, so a schema drift degrades that probe alone and still produces a value
    that moves when the others do.

    The db runs in WAL mode (the repo gitignores *.sqlite-wal), so a commit may leave the
    main file's size and mtime untouched while the -wal file grows. Both are included:
    the row probes are the real signal, the file sizes are the backstop.
    """
    con = connect(run)
    out = []
    for sql in _STAMP_SQL:
        try:
            row = con.execute(sql).fetchone()
            out.append((row[0] if row else 0) or 0)
        except sqlite3.Error as e:
            out.append("err:%s" % e)
    base = db_path(run)
    for path in (base, base + "-wal"):
        try:
            out.append(os.path.getsize(path))
        except OSError:
            out.append(0)
    return tuple(out)


def cached_on(key_fn):
    """Memoize fn(*args) on a cheap key recomputed per call.

    The key is what makes this safe: it must move whenever the underlying data could
    have changed. `cached` below passes the corpus stamp; `cached_files` passes the size
    and mtime of the files a function reads. A memoized value is only ever as correct as
    its key, so a key that cannot move is a bug, not an optimisation -- see stamp().
    """
    def deco(fn):
        state: dict = {"key": None, "vals": {}}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args):
            k = key_fn()
            with lock:
                if state["key"] != k:
                    state["key"] = k
                    state["vals"] = {}
                hit = state["vals"].get(args, _MISS)
            if hit is not _MISS:
                return hit
            val = fn(*args)
            with lock:
                if state["key"] == k:
                    state["vals"][args] = val
            return val

        wrapper.cache_clear = lambda: state.update(key=None, vals={})
        return wrapper
    return deco


def cached(fn):
    """Memoize a query fn(con, *args) on the corpus stamp.

    Cache key is args; the whole cache is dropped when the stamp moves, so it cannot
    grow without bound and can never serve a value from an older corpus.

    Note the connection is part of the argument tuple but is per-thread and stable, so it
    does not fragment the cache in practice -- and including it is safer than dropping it,
    since two connections may point at different run dirs.

    The key is bound late (`lambda: stamp()`, not `stamp`) so that replacing module-level
    stamp() replaces it for already-decorated functions too. Capturing the function object
    instead silently pinned every existing cache against a swapped stamp, which the
    invalidation test caught.
    """
    return cached_on(lambda: stamp())(fn)


def file_stamp(*paths) -> tuple:
    """(size, mtime) per path -- the change marker for file-backed sources.

    Postmortems, the training ledger and the model metadata are files the session writes,
    not corpus tables, so the corpus stamp does not move when they change. A missing file
    contributes a distinct marker rather than being skipped, so a file appearing is itself
    a change.
    """
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((int(st.st_size), int(st.st_mtime_ns)))
        except OSError:
            out.append(None)
    return tuple(out)


def cached_files(*paths):
    """Memoize a zero-or-more-arg function on the stamp of the files it reads."""
    return cached_on(lambda: file_stamp(*paths))


def columns(con, name: str) -> set:
    """Long-lived run dbs drift -- gnn_impact, gnn_rank and policy were all added after
    rows existed. A query naming a missing column raises, so callers guard on this."""
    try:
        return {c[0] for c in con.execute("SELECT * FROM %s LIMIT 0" % name).description}
    except sqlite3.Error:
        return set()
