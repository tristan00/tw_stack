
from __future__ import annotations

import contextvars
import functools
import os
import sqlite3
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from decisions import dbopen

_local = threading.local()
_trace = contextvars.ContextVar("api_trace", default=None)


def trace_begin():
    return _trace.set([])


def trace_end(token) -> list:
    items = _trace.get() or []
    _trace.reset(token)
    return items


def _note(name, ms, kind):
    items = _trace.get()
    if items is not None:
        items.append((name, ms, kind))


def timed(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            _note(fn.__name__, (time.perf_counter() - t0) * 1000, "run")
    return wrapper


def run_dir() -> str:
    return common.RUN_DIR


def db_path(run: str | None = None) -> str:
    return os.path.join(run or run_dir(), "decisions.sqlite")


def connect(run: str | None = None) -> sqlite3.Connection:
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
    "SELECT MAX(decision_id) FROM taken",
    "SELECT MAX(interrupt_id) FROM interrupts",
)


def stamp(run: str | None = None) -> tuple:
    con = connect(run)
    out = []
    for sql in _STAMP_SQL:
        try:
            row = con.execute(sql).fetchone()
            out.append((row[0] if row else 0) or 0)
        except sqlite3.Error as e:
            out.append("err:%s" % e)
    return tuple(out)


def columns(con, name: str) -> set:
    try:
        return {c[0] for c in con.execute("SELECT * FROM %s LIMIT 0" % name).description}
    except sqlite3.Error:
        return set()
