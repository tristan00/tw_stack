import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg
from migrate.run import checkpoint

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    sys.stderr.write('%.3f  s9 %s\n' % (time.time(), msg))


def wanted():
    tables = re.findall(r'CREATE TABLE (?:IF NOT EXISTS )?([a-z0-9_]+)\.([a-z0-9_]+)',
                        io.open(os.path.join(ROOT, 'sql', '03_tables.sql'),
                                encoding='utf-8').read())
    csql = io.open(os.path.join(ROOT, 'sql', '03_constraints.sql'),
                   encoding='utf-8').read()
    cons = re.findall(r'ADD CONSTRAINT ([a-z0-9_]+)', csql)
    idx = re.findall(r'CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?([a-z0-9_]+)', csql)
    return tables, cons, idx


def main():
    t0 = time.time()
    log('enter port=%s' % os.environ.get('TW_PG_PORT'))
    tables, cons, idx = wanted()
    pg.PORT = 55433
    missing = {'tables': [], 'constraints': [], 'unvalidated': [], 'indexes': []}
    with pg.connect(app_name='tw-s9check', autocommit=True) as con:
        have_tables = {(s, t) for s, t in con.execute(
            "SELECT schemaname, tablename FROM pg_tables")}
        for s, t in tables:
            if (s, t) not in have_tables:
                missing['tables'].append('%s.%s' % (s, t))
        have_cons = dict(con.execute(
            "SELECT conname, convalidated FROM pg_constraint c"
            " JOIN pg_namespace n ON n.oid = c.connamespace"
            " WHERE n.nspname IN ('corpus','dict','ref','ops','analytics2','public')"))
        for c in cons:
            if c not in have_cons:
                missing['constraints'].append(c)
            elif not have_cons[c]:
                missing['unvalidated'].append(c)
        have_idx = {r[0] for r in con.execute("SELECT indexname FROM pg_indexes")}
        for i in idx:
            if i not in have_idx:
                missing['indexes'].append(i)
        bad = sum(len(v) for v in missing.values())
        ok = bad == 0
        t1 = time.time()
        for schema in ('corpus', 'dict', 'ops', 'analytics2'):
            for (t,) in con.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname=%s", (schema,)):
                con.execute('ANALYZE %s."%s"' % (schema, t))
        log('analyze exit %.0f ms' % ((time.time() - t1) * 1000))
    with pg.connect(app_name='tw-s9check') as con:
        checkpoint(con, 'S3', 'done' if not missing['tables'] else 'failed',
                   len(tables), len(tables) - len(missing['tables']), t0)
        checkpoint(con, 'S9', 'done' if ok else 'failed',
                   len(cons) + len(idx), len(cons) + len(idx) - bad, t0)
        con.commit()
    out = {'ts': time.time(), 'ok': ok, 'tables': len(tables),
           'constraints': len(cons), 'indexes': len(idx), 'missing': missing}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 's9_check.json')
    io.open(path, 'w', encoding='utf-8', newline='\n').write(json.dumps(out, indent=2) + '\n')
    log('exit %.0f ms %s tables=%d cons=%d idx=%d missing=%d'
        % ((time.time() - t0) * 1000, 'OK' if ok else 'MISSING',
           len(tables), len(cons), len(idx), bad))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
