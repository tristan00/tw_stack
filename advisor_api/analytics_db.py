from __future__ import annotations


import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

from advisor_api import db as _db
from analytics import store as _store

_local = threading.local()

def path(run: str | None = None) -> str:
    return _store.analytics_path(run or _db.run_dir())


def connect(run: str | None = None):
    con = getattr(_local, "con", None)
    if con is None or con.closed:
        con = _local.con = _store.connect(readonly=True)
    return con


def tenant_state(name: str, run: str | None = None) -> dict:
    con = connect(run)
    if con is None:
        return {}
    try:
        return _store.state(con, name)
    except psycopg.Error:
        return {}


def all_state(run: str | None = None) -> list:
    con = connect(run)
    if con is None:
        return []
    try:
        return _store.all_state(con)
    except psycopg.Error:
        return []


def rows(sql: str, args=(), run: str | None = None) -> list:
    con = connect(run)
    if con is None:
        return []
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except psycopg.Error:
        return []


def one(sql: str, args=(), run: str | None = None) -> dict | None:
    got = rows(sql, args, run)
    return got[0] if got else None
