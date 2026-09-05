import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

VIEWS = ['building_chains', 'buildings', 'tech', 'units', 'skills', 'rituals',
         'tech_links', 'skill_links', 'ancillary_effects', 'effects_meta',
         'agent_abilities', 'agent_permitted_subtypes', 'skill_actions',
         'ancillaries', 'agent_actions', 'action_results',
         'action_result_outcomes', 'captive_options', 'captive_binding',
         'trait_meta', 'trait_levels', 'trait_effects', 'trait_antitraits',
         'tech_groups', 'skill_categories', 'skill_indents', 'skill_node_sets',
         'skill_set_members', 'merc_units']

ROUNDED = {'background_weighting'}


def log(msg):
    sys.stderr.write('%.3f  views %s\n' % (time.time(), msg))


def columns(con, schema, name):
    return [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, name))]


def rows_of(con, schema, name, cols):
    sel = ', '.join('"%s"' % c for c in cols)
    return [tuple(r) for r in con.execute('SELECT %s FROM %s."%s"' % (sel, schema, name))]


def norm(row):
    return tuple('' if v is None else
                 (round(v, 4) if isinstance(v, float) else
                  (int(v) if isinstance(v, bool) else v))
                 for v in row)


def compare(con, name):
    legacy_cols = columns(con, 'reference', name)
    view_cols = columns(con, 'refc', name)
    if not legacy_cols:
        return {'status': 'no legacy table'}
    if legacy_cols != view_cols:
        return {'status': 'column mismatch', 'legacy': legacy_cols, 'view': view_cols}
    a = collections.Counter(norm(r) for r in rows_of(con, 'reference', name, legacy_cols))
    b = collections.Counter(norm(r) for r in rows_of(con, 'refc', name, view_cols))
    missing = a - b
    extra = b - a
    status = 'ok' if not missing and not extra else 'differs'
    return {'status': status, 'legacy_rows': sum(a.values()),
            'view_rows': sum(b.values()),
            'missing_from_view': sum(missing.values()),
            'extra_in_view': sum(extra.values()),
            'sample_missing': [str(r)[:90] for r in list(missing)[:2]],
            'sample_extra': [str(r)[:90] for r in list(extra)[:2]]}


def main():
    t0 = time.time()
    log('enter')
    out = {}
    with pg.connect(app_name='tw-viewscheck', readonly=True) as con:
        for name in VIEWS:
            out[name] = compare(con, name)
            r = out[name]
            log('%-26s %-8s legacy=%s view=%s missing=%s extra=%s'
                % (name, r['status'], r.get('legacy_rows'), r.get('view_rows'),
                   r.get('missing_from_view'), r.get('extra_in_view')))
            for s in (r.get('sample_missing') or [])[:1]:
                log('    only in legacy: %s' % s)
            for s in (r.get('sample_extra') or [])[:1]:
                log('    only in view  : %s' % s)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'views_check.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=2)
    ok = sum(1 for r in out.values() if r['status'] == 'ok')
    log('exit %.0f ms  %d of %d views equal the legacy table'
        % ((time.time() - t0) * 1000, ok, len(VIEWS)))


if __name__ == '__main__':
    main()
