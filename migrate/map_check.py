import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, hydrate, pg, schema_map
from migrate.canon_check import ROLES, text_of

COLS_SQL = """
SELECT c.relname, a.attname FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
 WHERE n.nspname = 'corpus' AND c.relname LIKE '%_set_member'
"""


def log(msg):
    sys.stderr.write('%.3f  map_check %s\n' % (time.time(), msg))


def main():
    t0 = time.time()
    log('enter')
    failures = []
    table_cols = collections.defaultdict(set)
    with pg.connect(app_name='tw-mapcheck', readonly=True) as con:
        for tbl, col in con.execute(COLS_SQL):
            table_cols[tbl].add(col)

        for key, (kind, table, shape, cols) in sorted(schema_map.COLLECTIONS.items()):
            have = table_cols.get(table)
            if not have:
                failures.append('%s: table corpus.%s does not exist' % (key, table))
                continue
            missing = set(cols) - have
            if missing:
                failures.append('%s -> %s: columns not in the table: %s'
                                % (key, table, sorted(missing)))
            extra = have - set(cols) - {'set_id', 'ord'}
            if extra:
                failures.append('%s -> %s: columns with no source: %s'
                                % (key, table, sorted(extra)))
        log('declared %d collections over %d tables'
            % (len(schema_map.COLLECTIONS),
               len({v[1] for v in schema_map.COLLECTIONS.values()})))

        seen = collections.Counter()
        leftover = collections.defaultdict(set)
        rows = 0
        for role, sql in ROLES.items():
            for (z,) in con.execute(sql):
                rows += 1
                rec = canon.normalise(json.loads(text_of(z)))
                for key, value in rec.items():
                    if key in schema_map.NOT_SETS:
                        continue
                    if not hydrate.is_collection(key, value):
                        continue
                    if key not in schema_map.COLLECTIONS:
                        failures.append('collection %r has no mapping' % key)
                        continue
                    seen[key] += 1
                    for f in schema_map.unconsumed(key, value):
                        leftover[key].add(f)
                    try:
                        schema_map.members(key, value)
                    except Exception as exc:
                        failures.append('%s: members() raised %s' % (key, exc))

    for key, fields in sorted(leftover.items()):
        failures.append('%s: record fields with no column: %s' % (key, sorted(fields)))

    log('scanned %d records, %d distinct collections present' % (rows, len(seen)))
    if failures:
        for f in sorted(set(failures)):
            log('FAIL %s' % f)
        raise SystemExit('mapping incomplete: %d problems' % len(set(failures)))
    log('exit %.0f ms  MAPPING COMPLETE: every column sourced, every field consumed'
        % ((time.time() - t0) * 1000))


if __name__ == '__main__':
    main()
