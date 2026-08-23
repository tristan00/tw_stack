
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
_MISS = object()
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


def cached_on(key_fn):
    def deco(fn):
        state: dict = {"key": None, "vals": {}, "inflight": {}}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args):
            while True:
                k = key_fn()
                with lock:
                    if state["key"] != k:
                        state["key"] = k
                        state["vals"] = {}
                        state["inflight"] = {}
                    hit = state["vals"].get(args, _MISS)
                    if hit is not _MISS:
                        _note(fn.__name__, 0.0, "hit")
                        return hit
                    ev = state["inflight"].get(args)
                    mine = ev is None
                    if mine:
                        ev = state["inflight"][args] = threading.Event()
                if not mine:
                    t0 = time.perf_counter()
                    ev.wait()
                    _note(fn.__name__, (time.perf_counter() - t0) * 1000, "wait")
                    continue
                t0 = time.perf_counter()
                try:
                    val = fn(*args)
                    _note(fn.__name__, (time.perf_counter() - t0) * 1000, "miss")
                except BaseException:
                    with lock:
                        if state["inflight"].get(args) is ev:
                            del state["inflight"][args]
                    ev.set()
                    raise
                with lock:
                    if state["key"] == k:
                        state["vals"][args] = val
                    if state["inflight"].get(args) is ev:
                        del state["inflight"][args]
                ev.set()
                return val

        wrapper.cache_clear = lambda: state.update(key=None, vals={}, inflight={})
        return wrapper
    return deco


def cached(fn):
    return cached_on(lambda: stamp())(fn)


_CAMPAIGN_STAMP_SQL = (
    "SELECT COUNT(*), MAX(campaign_id), SUM(outcome IS NOT NULL) FROM campaigns",
    "SELECT MAX(pick_id) FROM ucb_picks",
)


def campaign_stamp(run: str | None = None) -> tuple:
    con = connect(run)
    out = [db_path(run)]
    for sql in _CAMPAIGN_STAMP_SQL:
        try:
            row = con.execute(sql).fetchone()
            out.extend((v or 0) for v in (row or ()))
        except sqlite3.Error as e:
            out.append("err:%s" % e)
    return tuple(out)


def cached_per_campaign(fn):
    return cached_on(lambda: campaign_stamp())(fn)


def file_stamp(*paths) -> tuple:
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((int(st.st_size), int(st.st_mtime_ns)))
        except OSError:
            out.append(None)
    return tuple(out)


def cached_files(*paths):
    return cached_on(lambda: file_stamp(*paths))


def columns(con, name: str) -> set:
    try:
        return {c[0] for c in con.execute("SELECT * FROM %s LIMIT 0" % name).description}
    except sqlite3.Error:
        return set()
