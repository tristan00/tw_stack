import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg
from migrate.run import checkpoint

TABLES = ('actions', 'blobs', 'campaigns', 'collector_versions', 'decisions',
          'diplomacy_events', 'entities', 'interrupts', 'meta',
          'offer_model_scores', 'offer_scores', 'offers', 'postmortems',
          'rpc_requests', 'rpc_responses', 'taken', 'ucb_pick_rows', 'ucb_picks')


def log(msg):
    sys.stderr.write('%.3f  s2 %s\n' % (time.time(), msg))


def counts(port):
    t0 = time.time()
    log('counts enter port=%d' % port)
    pg.PORT = port
    out = {}
    with pg.connect(app_name='tw-s2check', readonly=True, autocommit=True) as con:
        for t in TABLES:
            out[t] = con.execute('SELECT count(*) FROM public."%s"' % t).fetchone()[0]
    log('counts exit port=%d %.0f ms' % (port, (time.time() - t0) * 1000))
    return out


def main():
    t0 = time.time()
    log('enter')
    c = counts(55432)
    d = counts(55433)
    diffs = {t: {'c': c[t], 'd': d[t]} for t in TABLES if c[t] != d[t]}
    ok = not diffs
    pg.PORT = 55433
    with pg.connect(app_name='tw-s2check') as con:
        checkpoint(con, 'S2', 'done' if ok else 'failed',
                   sum(c.values()), sum(d.values()), t0)
        con.commit()
    out = {'ts': time.time(), 'ok': ok, 'counts_c': c, 'counts_d': d, 'diffs': diffs}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 's2_check.json')
    io.open(path, 'w', encoding='utf-8', newline='\n').write(json.dumps(out, indent=2) + '\n')
    log('exit %.0f ms %s rows_c=%d rows_d=%d diffs=%d'
        % ((time.time() - t0) * 1000, 'EQUAL' if ok else 'DIFFER',
           sum(c.values()), sum(d.values()), len(diffs)))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
