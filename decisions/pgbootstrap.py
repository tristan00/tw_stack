from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg
from decisions import pg_schema as S


def _provision():
    import psycopg
    kw = dict(host=pg.HOST, port=pg.PORT, dbname="postgres", user=pg.SUPERUSER,
              autocommit=True)
    supw = os.environ.get("TW_PG_SUPERUSER_PW")
    if supw:
        kw["password"] = supw
    try:
        con = psycopg.connect(**kw)
    except psycopg.OperationalError as e:
        sys.stdout.write("superuser connect failed (%s); skipping provisioning\n"
                         % str(e).strip()[:120])
        return
    try:
        cur = con.cursor()
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (pg.USER,))
        if not cur.fetchone():
            if not pg.PASSWORD:
                raise SystemExit("role %s does not exist and TW_PG_PASSWORD is not set; "
                                 "cannot create a role without a password" % pg.USER)
            cur.execute("CREATE ROLE %s LOGIN PASSWORD %%s" % pg.USER, (pg.PASSWORD,))
            sys.stdout.write("created role %s\n" % pg.USER)
        else:
            sys.stdout.write("role %s exists\n" % pg.USER)
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (pg.DB,))
        if not cur.fetchone():
            cur.execute("CREATE DATABASE %s OWNER %s" % (pg.DB, pg.USER))
            sys.stdout.write("created database %s owner %s\n" % (pg.DB, pg.USER))
        else:
            sys.stdout.write("database %s exists\n" % pg.DB)
    finally:
        con.close()


def _apply_schema():
    con = pg.connect()
    try:
        con.execute(S.DDL)
        con.execute(S.VIEWS)
        con.execute("INSERT INTO meta(k,v) VALUES('schema_version',%s)"
                    " ON CONFLICT (k) DO NOTHING", (S.SCHEMA_VERSION,))
        con.commit()
        row = con.execute("SELECT current_user, current_database(), version()").fetchone()
        sys.stdout.write("connected as %s to %s\n%s\n" % (row[0], row[1], row[2]))
        v = con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0]
        sys.stdout.write("schema_version %s\n" % v)
    finally:
        con.close()


def main():
    _provision()
    _apply_schema()
    sys.stdout.write("db-init OK\n")


if __name__ == "__main__":
    main()
