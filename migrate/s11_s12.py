import io
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault('TW_PG_PORT', '55433')

from decisions import pg
from migrate.run import checkpoint

VENV_PY = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')


def log(msg):
    sys.stderr.write('%.3f  s11s12 %s\n' % (time.time(), msg))


def run_analytics(args):
    t0 = time.time()
    log('S11 runner %s enter' % ' '.join(args))
    proc = subprocess.run(
        [VENV_PY, '-m', 'analytics.runner', '--once'] + args,
        cwd=ROOT, env=dict(os.environ), capture_output=True, text=True,
        timeout=3600)
    folded = 0
    for line in (proc.stdout or '').split('\n'):
        if line.strip():
            log('S11 | %s' % line.strip())
        if 'folded ' in line:
            tok = line.split('folded ', 1)[1].split(' ', 1)[0]
            folded += int(tok.rstrip(','))
    if proc.returncode != 0:
        raise RuntimeError('analytics runner failed: %s'
                           % (proc.stderr or '')[-400:])
    log('S11 runner exit %.1f min folded=%d' % ((time.time() - t0) / 60, folded))
    return folded


def main():
    t0 = time.time()
    log('enter port=%s' % os.environ['TW_PG_PORT'])
    out = {'ts': time.time()}
    out['folded_rebuild'] = run_analytics(['--rebuild'])
    out['folded_second'] = run_analytics([])
    from analytics import store as astore
    from analytics.tenants import TENANTS
    src = pg.connect(app_name='tw-s11check', readonly=True, autocommit=True,
                     row_factory=pg.row_factory, search_path=pg.CORPUS_PATH)
    an = astore.connect()
    wm = {}
    ok = True
    for t in astore.order_tenants(list(TENANTS)):
        st = astore.state(an, t.NAME)
        hi = int(t.safe_hi(src, an) or 0)
        wm[t.NAME] = {'watermark': int(st['watermark']), 'safe_hi': hi,
                      'ok': int(st['watermark']) >= hi}
        ok = ok and wm[t.NAME]['ok']
        log('S11 %-22s watermark=%d safe_hi=%d %s'
            % (t.NAME, st['watermark'], hi, 'ok' if wm[t.NAME]['ok'] else 'BEHIND'))
    out['watermarks'] = wm
    out['second_pass_zero'] = out['folded_second'] == 0
    ok = ok and out['second_pass_zero']

    t1 = time.time()
    log('S12 sequences enter')
    con = pg.connect(app_name='tw-s12', autocommit=True)
    cols = con.execute(
        "SELECT table_schema, table_name, column_name"
        " FROM information_schema.columns"
        " WHERE table_schema IN ('corpus','dict','ops','analytics2','migrate')"
        " AND (is_identity = 'YES' OR column_default LIKE 'nextval%')"
        " ORDER BY 1, 2").fetchall()
    seqs = []
    for schema, table, col in cols:
        row = con.execute(
            "SELECT pg_get_serial_sequence(%s, %s)",
            ('%s.%s' % (schema, table), col)).fetchone()
        if not row or not row[0]:
            continue
        newval = con.execute(
            'SELECT setval(%%s, COALESCE((SELECT MAX("%s") FROM %s."%s"), 0) + 1,'
            ' false)' % (col, schema, table), (row[0],)).fetchone()[0]
        seqs.append({'seq': row[0], 'next': int(newval)})
        log('S12 %-48s next=%d' % (row[0], newval))
    out['sequences'] = seqs
    log('S12 sequences exit %.0f ms n=%d' % ((time.time() - t1) * 1000, len(seqs)))
    checkpoint(con, 'S11', 'done' if ok else 'failed',
               out['folded_rebuild'], out['folded_second'], t0)
    checkpoint(con, 'S12', 'done', len(seqs), len(seqs), t1)
    con.close()
    src.close()
    an.close()
    out['ok'] = ok
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 's11_s12.json')
    io.open(path, 'w', encoding='utf-8', newline='\n').write(
        json.dumps(out, indent=2) + '\n')
    log('exit %.1f min %s' % ((time.time() - t0) / 60, 'OK' if ok else 'FAILED'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
