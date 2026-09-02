
from __future__ import annotations

import contextvars
import functools
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

import common
from decisions import pg

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
    return pg.dsn()


SEARCH_PATH = "public, analytics, app, reference"


def connect(run: str | None = None):
    con = getattr(_local, "con", None)
    if con is None or con.closed:
        con = _local.con = pg.connect(autocommit=True, readonly=True,
                                      row_factory=pg.row_factory,
                                      search_path=SEARCH_PATH)
    return con


def write():
    con = getattr(_local, "wcon", None)
    if con is None or con.closed:
        con = _local.wcon = pg.connect(autocommit=True, row_factory=pg.row_factory,
                                       search_path=SEARCH_PATH)
    return con


_STAMP_SQL = (
    "SELECT MAX(decision_id) m FROM decisions",
    "SELECT MAX(decision_id) m FROM taken",
    "SELECT MAX(interrupt_id) m FROM interrupts",
)


def stamp(run: str | None = None) -> tuple:
    con = connect(run)
    out = []
    for sql in _STAMP_SQL:
        try:
            row = con.execute(sql).fetchone()
            out.append((row["m"] if row else 0) or 0)
        except psycopg.Error as e:
            out.append("err:%s" % e)
    return tuple(out)


def columns(con, name: str) -> set:
    try:
        return {c.name for c in con.execute("SELECT * FROM %s LIMIT 0" % name).description}
    except psycopg.Error:
        return set()
