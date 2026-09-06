import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TIMINGS_SQL = """
SELECT count(*),
       percentile_disc(0.5) WITHIN GROUP (ORDER BY (timings::json->>'%(k)s')::numeric),
       percentile_disc(0.9) WITHIN GROUP (ORDER BY (timings::json->>'%(k)s')::numeric)
FROM (SELECT timings FROM decisions
      WHERE timings IS NOT NULL ORDER BY decision_id DESC LIMIT %(n)d) s
"""

NO_BASELINE = {
    'store.write_decide': 'legacy log_pick/log_options are not timed',
    'store.write_verification': 'no legacy counterpart',
    'store.write_interrupt': 'interrupts.latency_ms is panel latency, not the DB write',
}


def log(msg):
    sys.stderr.write('%.3f  %s\n' % (time.time(), msg))


def percentiles(con, n=5000):
    log('percentiles enter')
    t0 = time.time()
    out = {}
    for key in ('store_ms', 'roundtrip_ms', 'collect_ms', 'score_ms', 'pickup_lag_ms'):
        rows = con.execute(TIMINGS_SQL % {'k': key, 'n': n}).fetchall()
        cnt, p50, p90 = rows[0]
        out[key] = {'n': int(cnt), 'p50': float(p50), 'p90': float(p90)}
    log('percentiles exit %.0f ms' % ((time.time() - t0) * 1000))
    return out


def timed(name, fn, repeats=3):
    log('%s enter' % name)
    runs = []
    for _ in range(repeats):
        t0 = time.time()
        result = fn()
        runs.append((time.time() - t0) * 1000.0)
    size = len(result) if hasattr(result, '__len__') else None
    log('%s exit best %.0f ms worst %.0f ms rows %s' % (name, min(runs), max(runs), size))
    return {'ms_best': round(min(runs), 1), 'ms_worst': round(max(runs), 1),
            'runs': repeats, 'rows': size}


def main():
    t0 = time.time()
    from decisions import pg, store
    log('bench.legacy enter port=%s' % pg.PORT)
    from advisor import memory

    out = {'ts': time.time(), 'port': pg.PORT,
           'git_sha': os.popen('git rev-parse --short HEAD').read().strip(),
           'no_baseline': NO_BASELINE}

    with pg.connect(readonly=True) as con:
        out['write_path'] = percentiles(con)
        out['read_path'] = {
            'prebattle_attributions': timed(
                'prebattle_attributions', lambda: memory.prebattle_attributions(con)),
        }

    st = store.DecisionStore('bench', readonly=True)
    out['read_path']['target_series'] = timed('target_series', st.target_series)
    out['read_path']['interrupt_rows'] = timed('interrupt_rows', st.interrupt_rows)

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'legacy.json')
    io.open(path, 'w', encoding='utf-8', newline='\n').write(json.dumps(out, indent=2) + '\n')
    log('bench.legacy exit %.0f ms -> %s' % ((time.time() - t0) * 1000, path))


main()
