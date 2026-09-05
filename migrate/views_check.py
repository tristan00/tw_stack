import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

VIEWS = ['building_chains', 'buildings', 'tech', 'units', 'skills', 'rituals']

ROUNDED = {'background_weighting'}


def log(msg):
    sys.stderr.write('%.3f  views %s\n' % (time.time(), msg))


def columns(con, schema, name):
    return [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, name))]


def fetch(con, schema, name, cols, key):
    sel = ', '.join('"%s"' % c for c in cols)
    return {r[0]: r for r in con.execute(
        'SELECT %s FROM %s."%s" ORDER BY "%s"' % (sel, schema, name, key))}


def compare(con, name):
    legacy_cols = columns(con, 'reference', name)
    view_cols = columns(con, 'refc', name)
    if not legacy_cols:
        return {'status': 'no legacy table'}
    if legacy_cols != view_cols:
        return {'status': 'column mismatch', 'legacy': legacy_cols, 'view': view_cols}
    key = legacy_cols[0]
    a = fetch(con, 'reference', name, legacy_cols, key)
    b = fetch(con, 'refc', name, view_cols, key)
    only_legacy = sorted(set(a) - set(b))[:5]
    only_view = sorted(set(b) - set(a))[:5]
    diffs = []
    documented = 0
    for k in sorted(set(a) & set(b)):
        for i, col in enumerate(legacy_cols):
            x, y = a[k][i], b[k][i]
            if x == y:
                continue
            if x == '' and y is None:
                documented += 1
                continue
            if isinstance(x, float) and isinstance(y, float) and abs(x - y) < 1e-4:
                continue
            if col in ROUNDED and x is not None and y is not None:
                if abs(float(x) - float(y)) < 1e-3:
                    continue
            diffs.append({'key': str(k), 'column': col,
                          'legacy': str(x)[:40], 'view': str(y)[:40]})
            if len(diffs) >= 6:
                break
        if len(diffs) >= 6:
            break
    return {'status': 'ok' if not (only_legacy or only_view or diffs) else 'differs',
            'legacy_rows': len(a), 'view_rows': len(b),
            'only_legacy': only_legacy, 'only_view': only_view, 'diffs': diffs,
            'documented_null_for_absent': documented}


def main():
    t0 = time.time()
    log('enter')
    out = {}
    with pg.connect(app_name='tw-viewscheck', readonly=True) as con:
        for name in VIEWS:
            out[name] = compare(con, name)
            r = out[name]
            log('%-18s %-9s legacy=%s view=%s undocumented_diffs=%d'
                ' documented NULL-for-absent=%d'
                % (name, r['status'], r.get('legacy_rows'), r.get('view_rows'),
                   len(r.get('diffs') or []), r.get('documented_null_for_absent', 0)))
            for d in (r.get('diffs') or [])[:2]:
                log('    %s.%s legacy=%r view=%r' % (d['key'][:28], d['column'],
                                                     d['legacy'], d['view']))
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'views_check.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=2)
    ok = sum(1 for r in out.values() if r['status'] == 'ok')
    log('exit %.0f ms  %d of %d views equal the legacy table'
        % ((time.time() - t0) * 1000, ok, len(VIEWS)))


if __name__ == '__main__':
    main()
