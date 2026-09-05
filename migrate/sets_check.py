import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, dicts, pg, schema_map, sets
from migrate.canon_check import ROLES, text_of

LIMIT = int(os.environ.get('SETS_CHECK_LIMIT', '0'))


def log(msg):
    sys.stderr.write('%.3f  sets_check %s\n' % (time.time(), msg))


def main():
    t0 = time.time()
    log('enter port=%s' % os.environ.get('TW_PG_PORT'))
    con = pg.connect(app_name='tw-setscheck', autocommit=True)
    tables = sorted({v[1] for v in schema_map.COLLECTIONS.values()})
    con.execute("TRUNCATE corpus.lord_pool_candidate, %s, corpus.state_set CASCADE"
                % ', '.join('corpus.' + t for t in tables))
    d = dicts.Dicts(con)
    writer = sets.SetWriter(con, d)

    records = []
    with pg.connect(app_name='tw-setsread', readonly=True) as rc:
        for role, sql in ROLES.items():
            q = sql + (' LIMIT %d' % LIMIT if LIMIT else '')
            for (z,) in rc.execute(q):
                records.append(canon.normalise(json.loads(text_of(z))))
    log('read %d records' % len(records))

    write_ms = 0.0
    all_ids = []
    for rec in records:
        prepared = sets.prepare(rec)
        t1 = time.time()
        ids = writer.ensure(prepared)
        write_ms += (time.time() - t1) * 1000.0
        all_ids.append((rec, ids))
    log('write exit %.0f ms (%.3f ms/record) distinct sets=%d'
        % (write_ms, write_ms / max(len(records), 1), len(writer.known)))

    bad = collections.Counter()
    checked = 0
    t2 = time.time()
    seen_set = set()
    for rec, ids in all_ids:
        for key, set_id in ids.items():
            if set_id in seen_set:
                continue
            seen_set.add(set_id)
            checked += 1
            want = rec[key]
            got = sets.read_collection(con, d, key, set_id)
            if canon.canon(got) != canon.canon(want):
                bad[key] += 1
    log('verify exit %.0f ms checked=%d mismatched=%d'
        % ((time.time() - t2) * 1000, checked, sum(bad.values())))
    con.close()

    if bad:
        for key, n in bad.most_common():
            log('FAIL %s: %d sets differ' % (key, n))
        raise SystemExit('set round trip failed on %d collections' % len(bad))
    log('exit %.0f ms  SET ROUND TRIP CLEAN on %d sets'
        % ((time.time() - t0) * 1000, checked))


if __name__ == '__main__':
    main()
