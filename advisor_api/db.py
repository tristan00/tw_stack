from __future__ import annotations

import contextvars
import functools
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

_local = threading.local()
_trace = contextvars.ContextVar("api_trace", default=None)

SEARCH_PATH = "corpus,dict,refc,ref,analytics,ops"


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


def connect():
    con = getattr(_local, "con", None)
    if con is None or con.closed:
        con = _local.con = pg.connect(app_name="tw-api", autocommit=True,
                                      readonly=True, row_factory=pg.row_factory,
                                      search_path=SEARCH_PATH)
    return con


def stamp() -> int:
    row = connect().execute("SELECT MAX(snapshot_id) m FROM corpus.snapshot").fetchone()
    return row["m"] or 0
