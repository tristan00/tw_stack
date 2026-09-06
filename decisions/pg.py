from __future__ import annotations

import os
import sys
import time


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
PORT = int(_cfg("PORT", "55433"))
DB = _cfg("DB", "tw_stack")
USER = _cfg("USER", "tw")
PASSWORD = _cfg("PASSWORD", "")
SUPERUSER = _cfg("SUPERUSER", "postgres")

CONNECT_TIMEOUT = 5
KEEPALIVES_IDLE = 30

CORPUS_PATH = "corpus,dict,ref,ops,public"


def log(msg):
    sys.stderr.write("%.3f  pg %s\n" % (time.time(), msg))


def kwargs(dbname=None, user=None, autocommit=False, app_name=None, search_path=None):
    d = dict(host=HOST, port=PORT, dbname=dbname or DB, user=user or USER,
             autocommit=autocommit, connect_timeout=CONNECT_TIMEOUT,
             keepalives=1, keepalives_idle=KEEPALIVES_IDLE)
    if PASSWORD:
        d["password"] = PASSWORD
    if app_name:
        d["application_name"] = app_name
    if search_path:
        d["options"] = "-c search_path=%s" % search_path.replace(" ", "")
    return d


def connect(app_name=None, dbname=None, user=None, autocommit=False, readonly=False,
            row_factory=None, search_path=None):
    import psycopg
    t0 = time.time()
    if search_path is None:
        search_path = os.environ.get("TW_PG_SEARCH_PATH") or None
    kw = kwargs(dbname=dbname, user=user, autocommit=autocommit,
                app_name=app_name, search_path=search_path)
    if row_factory is not None:
        kw["row_factory"] = row_factory
    con = psycopg.connect(**kw)
    if readonly:
        con.execute("SET default_transaction_read_only = on")
        if not autocommit:
            con.commit()
    log("connect %s %s:%d/%s %.0f ms" % (app_name or "-", HOST, PORT, dbname or DB,
                                         (time.time() - t0) * 1000))
    return con


class Conn:

    def __init__(self, app_name, readonly=False, search_path=None, dbname=None, user=None):
        self.app_name = app_name
        self.readonly = readonly
        self.search_path = search_path
        self.dbname = dbname
        self.user = user
        self.reconnects = 0
        self.con = self._open()

    def _open(self):
        con = connect(app_name=self.app_name, dbname=self.dbname, user=self.user,
                      autocommit=True, readonly=self.readonly,
                      search_path=self.search_path)
        con.execute("SET synchronous_commit = off")
        return con

    def _reconnect(self):
        t0 = time.time()
        try:
            self.con.close()
        except Exception:
            pass
        self.con = self._open()
        self.reconnects += 1
        log("reconnect %s #%d %.0f ms" % (self.app_name, self.reconnects,
                                          (time.time() - t0) * 1000))

    def execute(self, sql, params=None):
        return self.con.execute(sql, params)

    def cursor(self):
        return self.con.cursor()

    def close(self):
        self.con.close()

    def unit(self, name):
        return _Unit(self, name)


class _Unit:

    def __init__(self, conn, name):
        self.conn = conn
        self.name = name
        self.t0 = None

    def __enter__(self):
        import psycopg
        self.t0 = time.time()
        log("%s enter" % self.name)
        try:
            self.conn.con.execute("BEGIN")
        except psycopg.OperationalError:
            self.conn._reconnect()
            log("%s abort after reconnect" % self.name)
            raise
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        import psycopg
        try:
            if exc_type is None:
                self.conn.con.execute("COMMIT")
            else:
                self.conn.con.execute("ROLLBACK")
        except psycopg.OperationalError:
            self.conn._reconnect()
            log("%s exit %.1f ms (connection lost)" % (self.name,
                                                       (time.time() - self.t0) * 1000))
            return False
        log("%s exit %.1f ms%s" % (self.name, (time.time() - self.t0) * 1000,
                                   "" if exc_type is None else " (rolled back)"))
        return False


def dsn(dbname=None, user=None):
    parts = ["host=%s" % HOST, "port=%d" % PORT, "dbname=%s" % (dbname or DB),
             "user=%s" % (user or USER)]
    if PASSWORD:
        parts.append("password=%s" % PASSWORD)
    return " ".join(parts)
