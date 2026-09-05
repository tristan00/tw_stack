from __future__ import annotations

import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

FILES = ('sql/03_tables.sql', 'sql/03_seed.sql', 'sql/03_views.sql',
         'sql/03_constraints.sql')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    sys.stderr.write('%.3f  db-init %s\n' % (time.time(), msg))


def apply(con):
    t0 = time.time()
    log('apply enter')
    for name in FILES:
        t1 = time.time()
        con.execute(io.open(os.path.join(ROOT, name), encoding='utf-8').read())
        log('%s applied %.0f ms' % (name, (time.time() - t1) * 1000))
    log('apply exit %.0f ms' % ((time.time() - t0) * 1000))


def main():
    con = pg.connect(app_name='tw-db-init', autocommit=True)
    have = con.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'corpus'").fetchone()
    if have:
        log('corpus schema already present, nothing to do')
        con.close()
        return
    apply(con)
    con.close()


if __name__ == '__main__':
    main()
