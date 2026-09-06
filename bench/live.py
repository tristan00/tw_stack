import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'migrate'))

import common
import verify
from decisions import hydrate, pg, store

TIMING_KEYS = ('store_ms', 'roundtrip_ms', 'collect_ms', 'score_ms', 'pickup_lag_ms')

TIMING_SQL = """
SELECT count(*),
       percentile_disc(0.5) WITHIN GROUP (ORDER BY %(k)s),
       percentile_disc(0.9) WITHIN GROUP (ORDER BY %(k)s),
       max(%(k)s)
FROM (SELECT %(k)s FROM corpus.decision_timing
      ORDER BY decision_id DESC LIMIT %(n)d) s
"""

UNIT_THRESHOLDS = {
    'write_snapshot': (45.0, 80.0),
    'write_decide': (15.0, None),
    'write_verification': (5.0, None),
    'write_interrupt': (20.0, None),
    'hydrate.record': (12.0, 25.0),
}

UNIT_LINE = re.compile(
    r'store\.(write_snapshot|write_decide|write_verification|write_interrupt)'
    r' exit ([0-9.]+) ms')

HYDRATE_LINE = re.compile(r'hydrate record exit ([0-9.]+) ms')

UNIT_SOURCES = (('manager_', '.log', UNIT_LINE), ('session_', '.err', HYDRATE_LINE))

ROUTES = (
    ('A1', '/api/run', 30.0),
    ('A2', '/api/campaigns?page_size=200', 80.0),
    ('A6', '/api/campaigns/starts', 150.0),
    ('A7', '/api/items', 200.0),
    ('A8', '/api/positions', 800.0),
    ('A9', '/api/decisions?limit=200', 40.0),
    ('A10', '/api/decisions/actions', 80.0),
    ('A11', '/api/models/forcing', 120.0),
    ('A12', '/api/campaigns/picks?limit=5', 80.0),
)

CAMPAIGN_ROUTES = (
    ('A3', '/api/campaigns/%s', 30.0),
    ('A4', '/api/campaigns/%s/state', 15.0),
    ('A5', '/api/campaigns/%s/buildings', 50.0),
)

I3_SQL = """
SELECT count(*) FILTER (WHERE c.first_snapshot_id IS DISTINCT FROM s.first_sid)
     + count(*) FILTER (WHERE c.last_snapshot_id IS DISTINCT FROM s.last_sid)
FROM corpus.character c
JOIN (SELECT character_id, min(snapshot_id) first_sid, max(snapshot_id) last_sid
      FROM corpus.char_state GROUP BY 1) s ON s.character_id = c.character_id
"""


def log(msg):
    sys.stderr.write('%.3f  live %s\n' % (time.time(), msg))


def line(name, p50, p90, mx, n, limit=None):
    verdict = '' if limit is None else ('  ok' if p50 <= limit else '  OVER %.1f' % limit)
    print('%-38s p50 %8.1f  p90 %8.1f  max %8.1f  n %6d%s'
          % (name, p50, p90, mx, n, verdict))


def stats(values):
    vs = sorted(values)
    n = len(vs)
    return {'n': n,
            'p50': vs[int(n * 0.5)] if n else 0.0,
            'p90': vs[min(n - 1, int(n * 0.9))] if n else 0.0,
            'max': vs[-1] if n else 0.0}


def decision_timings(con, n):
    log('decision_timings enter n=%d' % n)
    t0 = time.time()
    out = {}
    for key in TIMING_KEYS:
        cnt, p50, p90, mx = con.execute(TIMING_SQL % {'k': key, 'n': n}).fetchone()
        out[key] = {'n': int(cnt), 'p50': float(p50), 'p90': float(p90),
                    'max': float(mx)}
        line(key, out[key]['p50'], out[key]['p90'], out[key]['max'], out[key]['n'])
    log('decision_timings exit %.0f ms' % ((time.time() - t0) * 1000))
    return out


def unit_timings(hours):
    log('unit_timings enter hours=%d' % hours)
    t0 = time.time()
    floor = time.time() - hours * 3600.0
    seen = {}
    for directory, (prefix, suffix, pattern) in zip(
            (common.LOGS_SERVICES, common.LOGS_ADVISOR), UNIT_SOURCES):
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name)
            if not (name.startswith(prefix) and name.endswith(suffix)):
                continue
            if os.path.getmtime(path) < floor:
                continue
            for row in io.open(path, encoding='utf-8', errors='replace'):
                m = pattern.search(row)
                if m:
                    unit = m.group(1) if m.lastindex > 1 else 'hydrate.record'
                    seen.setdefault(unit, []).append(float(m.group(m.lastindex)))
    out = {}
    for unit, (p50_max, p90_max) in UNIT_THRESHOLDS.items():
        s = stats(seen.get(unit, []))
        s['p50_threshold'] = p50_max
        s['p90_threshold'] = p90_max
        s['ok'] = bool(s['n']) and s['p50'] <= p50_max and (
            p90_max is None or s['p90'] <= p90_max)
        out[unit] = s
        line(unit, s['p50'], s['p90'], s['max'], s['n'], p50_max)
    log('unit_timings exit %.0f ms' % ((time.time() - t0) * 1000))
    return out


def timed(name, fn, repeats, limit):
    t0 = time.time()
    runs = []
    for _ in range(repeats):
        t1 = time.time()
        result = fn()
        runs.append((time.time() - t1) * 1000.0)
    s = stats(runs)
    s['rows'] = len(result) if hasattr(result, '__len__') else None
    s['threshold'] = limit
    s['ok'] = s['p50'] <= limit
    line(name, s['p50'], s['p90'], s['max'], s['n'], limit)
    log('%s exit %.0f ms' % (name, (time.time() - t0) * 1000))
    return s


def read_paths(repeats, window):
    log('read_paths enter window=%d' % window)
    t0 = time.time()
    from advisor import memory
    out = {}
    with pg.connect(app_name='tw-live-bench', readonly=True, autocommit=True) as con:
        camp_ids = [r[0] for r in con.execute(
            'SELECT campaign_id FROM corpus.campaign ORDER BY campaign_id DESC'
            ' LIMIT %s', (window,))]
        out['prebattle_attributions'] = timed(
            'prebattle_attributions', lambda: memory.prebattle_attributions(con),
            repeats, 400.0)
        out['prebattle_attributions_window'] = timed(
            'prebattle_attributions window',
            lambda: memory.prebattle_attributions(con, camp_ids), repeats, 100.0)
    st = store.DecisionStore('bench-live', readonly=True)
    keys = [r[0] for r in st.con.execute(
        'SELECT campaign_key FROM corpus.campaign WHERE campaign_id = ANY(%s)',
        (camp_ids,))]
    out['target_series'] = timed('target_series', st.target_series, repeats, 80.0)
    out['interrupt_rows_window'] = timed(
        'interrupt_rows window', lambda: st.interrupt_rows(keys), repeats, 300.0)
    ids = [r[0] for r in st.con.execute(
        'SELECT decision_id FROM corpus.decision ORDER BY decision_id DESC LIMIT 200')]
    runs = []
    for i in ids:
        t1 = time.time()
        hydrate.record(st.con, i, legacy=False)
        runs.append((time.time() - t1) * 1000.0)
    s = stats(runs)
    s['threshold'] = 12.0
    s['ok'] = s['p50'] <= 12.0 and s['p90'] <= 25.0
    line('hydrate.record', s['p50'], s['p90'], s['max'], s['n'], 12.0)
    out['hydrate.record'] = s
    st.close()
    log('read_paths exit %.0f ms' % ((time.time() - t0) * 1000))
    return out


def fetch(base, path):
    with urllib.request.urlopen(base + path, timeout=600) as r:
        body = json.loads(r.read().decode('utf-8'))
        total = r.headers['Server-Timing'].split(';dur=')[1].split(',')[0]
    return float(total), body


def api(base, repeats):
    log('api enter base=%s' % base)
    t0 = time.time()
    out = {}
    key = fetch(base, '/api/campaigns?page_size=1')[1]['rows'][0]['campaign']['raw']
    cases = list(ROUTES) + [(n, p % key, lim) for n, p, lim in CAMPAIGN_ROUTES]
    for name, path, limit in cases:
        runs = [fetch(base, path)[0] for _ in range(repeats)]
        s = stats(runs)
        s['path'] = path
        s['threshold'] = limit
        s['ok'] = s['p50'] <= limit
        out[name] = s
        line('%s %s' % (name, path), s['p50'], s['p90'], s['max'], s['n'], limit)
    with pg.connect(app_name='tw-live-bench', readonly=True, autocommit=True) as con:
        runs = []
        for _ in range(200):
            t1 = time.time()
            con.execute('SELECT MAX(snapshot_id) FROM corpus.snapshot').fetchone()
            runs.append((time.time() - t1) * 1000.0)
        s = stats(runs)
        s['path'] = 'db.stamp()'
        s['threshold'] = 2.0
        s['ok'] = s['p50'] <= 2.0
        out['A13'] = s
        line('A13 db.stamp()', s['p50'], s['p90'], s['max'], s['n'], 2.0)
    log('api exit %.0f ms' % ((time.time() - t0) * 1000))
    return out


def dictionaries(con):
    log('dictionaries enter')
    t0 = time.time()
    from advisor.reference.build_reference import table_name
    dangling = 0
    unresolved = 0
    no_note = 0
    families = 0
    for family, ref_tbl, ref_col in con.execute(
            'SELECT family, ref_tbl, ref_col FROM dict.family ORDER BY 1'):
        tbl = table_name(ref_tbl)
        if con.execute("SELECT to_regclass('ref.%s')" % tbl).fetchone()[0] is None:
            continue
        families += 1
        dangling += con.execute(
            'SELECT count(*) FROM dict.%s d WHERE d.is_reference AND NOT EXISTS'
            ' (SELECT 1 FROM ref.%s r WHERE r."%s" = d.key)'
            % (family, tbl, ref_col)).fetchone()[0]
        unresolved += con.execute(
            'SELECT count(*) FROM dict.%s d WHERE NOT d.is_reference AND EXISTS'
            ' (SELECT 1 FROM ref.%s r WHERE r."%s" = d.key)'
            % (family, tbl, ref_col)).fetchone()[0]
        no_note += con.execute(
            'SELECT count(*) FROM dict.%s WHERE NOT is_reference AND note IS NULL'
            % family).fetchone()[0]
    out = {'ok': dangling == 0, 'families': families, 'dangling': dangling,
           'unresolved_since_build': unresolved, 'non_reference_without_note': no_note,
           's': round(time.time() - t0, 1)}
    log('dictionaries exit %.0f ms dangling=%d unresolved=%d no_note=%d'
        % ((time.time() - t0) * 1000, dangling, unresolved, no_note))
    return out


def integrity():
    log('integrity enter')
    t0 = time.time()
    out = {'I1': verify.v4(None, None), 'I2': verify.v5(None, None)}
    with pg.connect(app_name='tw-live-bench', readonly=True, autocommit=True) as con:
        bad = con.execute(I3_SQL).fetchone()[0]
        out['I3'] = {'ok': bad == 0, 'bad_first_last_snapshot': bad}
        log('I3 bad=%d' % bad)
    out['I4'] = verify.v6(None, None)
    out['I5'] = verify.v7(None, None)
    with pg.connect(app_name='tw-live-bench', readonly=True, autocommit=True) as con:
        out['I6'] = dictionaries(con)
    for k in sorted(out):
        print('%-38s %s' % (k, 'PASS' if out[k]['ok'] else 'FAIL'))
    log('integrity exit %.1f min' % ((time.time() - t0) / 60))
    return out


def analytics_pass():
    log('analytics_pass enter')
    t0 = time.time()
    import analytics.store as astore
    with astore.connect(readonly=True) as con:
        rows = [dict(r) for r in con.execute(
            'SELECT tenant, watermark, last_run_seconds, last_error FROM state'
            ' ORDER BY tenant')]
    worst = max([r['last_run_seconds'] or 0.0 for r in rows] or [0.0])
    out = {'ok': worst <= 20.0 and not any(r['last_error'] for r in rows),
           'tenants': rows, 'worst_run_seconds': worst, 'threshold': 20.0}
    print('%-38s worst %.1f s over %d tenants  %s'
          % ('analytics pass', worst, len(rows), 'ok' if out['ok'] else 'OVER'))
    log('analytics_pass exit %.0f ms' % ((time.time() - t0) * 1000))
    return out


def reference_check():
    log('reference_check enter')
    t0 = time.time()
    import subprocess
    p = subprocess.run([sys.executable, 'advisor/reference/build_reference.py', '--check'],
                       cwd=ROOT, capture_output=True, text=True)
    s = time.time() - t0
    out = {'ok': p.returncode == 0 and s <= 1.0 and 'unchanged' in p.stderr,
           'seconds': round(s, 2), 'threshold': 1.0,
           'tail': p.stderr.strip().splitlines()[-1:]}
    print('%-38s %.2f s  %s' % ('reference check', s, 'ok' if out['ok'] else 'OVER'))
    log('reference_check exit %.0f ms' % (s * 1000))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--api', default='http://127.0.0.1:8777')
    ap.add_argument('--decisions', type=int, default=5000)
    ap.add_argument('--hours', type=int, default=24)
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--window', type=int, default=1000)
    ap.add_argument('--day', type=int, required=True)
    args = ap.parse_args()

    t0 = time.time()
    info = common.code_version()
    log('enter day=%d port=%s version=%s' % (args.day, pg.PORT, info['version']))
    out = {'ts': time.time(), 'day': args.day, 'port': pg.PORT,
           'version': info['version'], 'git_sha': info['git_sha']}
    with pg.connect(app_name='tw-live-bench', readonly=True, autocommit=True) as con:
        out['decision_timings'] = decision_timings(con, args.decisions)
    out['unit_timings'] = unit_timings(args.hours)
    out['read_paths'] = read_paths(args.repeats, args.window)
    out['api'] = api(args.api, args.repeats)
    out['analytics'] = analytics_pass()
    out['reference'] = reference_check()
    out['integrity'] = integrity()

    over = ([k for k, v in out['unit_timings'].items() if not v['ok']]
            + [k for k, v in out['read_paths'].items() if not v['ok']]
            + [k for k, v in out['api'].items() if not v['ok']]
            + [k for k, v in out['integrity'].items() if not v['ok']]
            + ([] if out['analytics']['ok'] else ['analytics'])
            + ([] if out['reference']['ok'] else ['reference']))
    out['green'] = not over
    out['over'] = over
    path = os.path.join(HERE, 'live_day%d.json' % args.day)
    io.open(path, 'w', encoding='utf-8', newline='\n').write(
        json.dumps(out, indent=2, default=str) + '\n')
    print('\nday %d: %s%s' % (args.day, 'GREEN' if out['green'] else 'RED',
                              '' if out['green'] else '  ' + ', '.join(over)))
    log('exit %.1f min -> %s' % ((time.time() - t0) / 60, path))
    return 0 if out['green'] else 1


if __name__ == '__main__':
    sys.exit(main())
