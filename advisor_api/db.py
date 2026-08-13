
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
    base = db_path(run)
    for path in (base, base + "-wal"):
        try:
            out.append(os.path.getsize(path))
        except OSError:
            out.append(0)
    return tuple(out)


def cached_on(key_fn):
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
    return cached_on(lambda: stamp())(fn)


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
