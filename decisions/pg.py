from __future__ import annotations

import os


def _cfg(key, default):
    v = os.environ.get("TW_PG_" + key)
    if v is not None and v != "":
        return v
    return default


class Row(dict):
    __slots__ = ()

    def __getitem__(self, k):
        if isinstance(k, (int, slice)):
            return list(self.values())[k]
        return dict.__getitem__(self, k)

    def __iter__(self):
        return iter(list(self.values()))


def row_factory(cursor):
    names = [d.name for d in cursor.description or ()]

    def make(values):
        return Row(zip(names, values))
    return make


HOST = _cfg("HOST", "127.0.0.1")
PORT = int(_cfg("PORT", "55432"))
DB = _cfg("DB", "tw_stack")
USER = _cfg("USER", "tw")
PASSWORD = _cfg("PASSWORD", "")
SUPERUSER = _cfg("SUPERUSER", "postgres")


def kwargs(dbname=None, user=None, autocommit=False):
    d = dict(host=HOST, port=PORT, dbname=dbname or DB, user=user or USER,
             autocommit=autocommit)
    if PASSWORD:
        d["password"] = PASSWORD
    return d


def connect(dbname=None, user=None, autocommit=False, readonly=False, row_factory=None,
            search_path=None):
    import psycopg
    kw = kwargs(dbname=dbname, user=user, autocommit=autocommit)
    if row_factory is not None:
        kw["row_factory"] = row_factory
    con = psycopg.connect(**kw)
    if search_path:
        con.execute("SET search_path = %s" % search_path)
    if readonly:
        con.execute("SET default_transaction_read_only = on")
    if search_path or readonly:
        con.commit()
    return con


def dsn(dbname=None, user=None):
    parts = ["host=%s" % HOST, "port=%d" % PORT, "dbname=%s" % (dbname or DB),
             "user=%s" % (user or USER)]
    if PASSWORD:
        parts.append("password=%s" % PASSWORD)
    return " ".join(parts)
