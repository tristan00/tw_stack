from __future__ import annotations

from decisions import pg

SCRATCH = "tw_stack_test"


def fresh(name=SCRATCH):
    admin = pg.connect(dbname="postgres", autocommit=True)
    try:
        admin.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)
        admin.execute("CREATE DATABASE %s" % name)
    finally:
        admin.close()
    pg.DB = name
    return name


def drop(name=SCRATCH, restore="tw_stack"):
    pg.DB = restore
    admin = pg.connect(dbname="postgres", autocommit=True)
    try:
        admin.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)
    finally:
        admin.close()
