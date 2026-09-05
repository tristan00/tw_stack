import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, hydrate, pg
from migrate.canon_check import ROLES, text_of

COLUMNS_SQL = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
       (SELECT cl.relname FROM pg_constraint k
          JOIN pg_class cl ON cl.oid = k.confrelid
         WHERE k.conrelid = c.oid AND k.contype = 'f' AND a.attnum = ANY (k.conkey) LIMIT 1),
       (SELECT n2.nspname FROM pg_constraint k
          JOIN pg_class cl ON cl.oid = k.confrelid
          JOIN pg_namespace n2 ON n2.oid = cl.relnamespace
         WHERE k.conrelid = c.oid AND k.contype = 'f' AND a.attnum = ANY (k.conkey) LIMIT 1)
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
 WHERE n.nspname = 'corpus' AND c.relname LIKE '%_set_member'
 ORDER BY c.relname, a.attnum
"""


def log(msg):
    sys.stderr.write('%.3f  map_probe %s\n' % (time.time(), msg))


def main():
    t0 = time.time()
    log('enter')
    tables = collections.OrderedDict()
    with pg.connect(app_name='tw-mapprobe', readonly=True) as con:
        for tbl, col, typ, notnull, ftbl, fns in con.execute(COLUMNS_SQL):
            tables.setdefault(tbl, []).append(
                {'column': col, 'type': typ, 'notnull': bool(notnull),
                 'fk': ('%s.%s' % (fns, ftbl)) if ftbl else None})

        fields = collections.defaultdict(collections.Counter)
        shapes = {}
        for role, sql in ROLES.items():
            for (z,) in con.execute(sql + ' LIMIT 400'):
                rec = canon.normalise(json.loads(text_of(z)))
                for key, value in rec.items():
                    if not hydrate.is_collection(key, value):
                        continue
                    enc = hydrate.encode_collection(key, value)
                    shapes[key] = enc['shape']
                    if enc['shape'] == 'list':
                        for f in enc['fields']:
                            fields[key][f] += 1
                    elif enc['shape'] == 'dict_of_dict':
                        for f in enc['fields']:
                            fields[key]['{}.' + f] += 1
                    else:
                        fields[key]['{}=scalar'] += 1

    out = {'tables': tables,
           'collections': {k: {'shape': shapes[k], 'kind': hydrate.KINDS.get(k),
                               'fields': sorted(v)} for k, v in sorted(fields.items())}}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'map_probe.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=2)
    log('exit %.0f ms  tables=%d collections=%d'
        % ((time.time() - t0) * 1000, len(tables), len(fields)))

    unmapped = [k for k in fields if k not in hydrate.KINDS]
    if unmapped:
        log('collections with no kind code: %s' % sorted(unmapped))


if __name__ == '__main__':
    main()
