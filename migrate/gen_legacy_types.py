import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, pg
from migrate.canon_check import text_of

ROLES = {
    'CB': "SELECT b.z FROM decisions d JOIN blobs b ON b.blob_id=d.campaign_blob"
          " WHERE mod(d.decision_id, %s) = 7",
    'WB': "SELECT b.z FROM decisions d JOIN blobs b ON b.blob_id=d.world_blob"
          " WHERE mod(d.decision_id, %s) = 7",
    'EB': "SELECT b.z FROM entities e JOIN blobs b ON b.blob_id=e.features_blob"
          " WHERE mod(e.decision_id, %s) = 7",
}

MOD = int(os.environ.get('LT_MOD', '50'))


def log(msg):
    sys.stderr.write('%.3f  gen_legacy_types %s\n' % (time.time(), msg))


def main():
    t0 = time.time()
    log('enter mod=%d' % MOD)
    out = {}
    with pg.connect(app_name='tw-legacytypes', readonly=True) as con:
        for role, sql in ROLES.items():
            t1 = time.time()
            seen = collections.defaultdict(collections.Counter)
            n = 0
            for (z,) in con.execute(sql, (MOD,)):
                n += 1
                learned = {}
                canon.normalise(json.loads(text_of(z)), '', learned)
                for path, kind in learned.items():
                    seen[path][kind] += 1
            table = {}
            conflicts = 0
            for path, kinds in seen.items():
                if len(kinds) == 1:
                    table[path] = next(iter(kinds))
                    continue
                conflicts += 1
                leaf = path.rsplit('.', 1)[-1].replace('[]', '').replace('{}', '')
                if leaf in canon.MEASURE_PATHS:
                    table[path] = 'float'
                elif leaf in canon.INT_CLASS:
                    table[path] = 'int'
                else:
                    table[path] = kinds.most_common(1)[0][0]
            out[role] = dict(sorted(table.items()))
            log('%s exit %.0f ms n=%d paths=%d conflicts=%d'
                % (role, (time.time() - t1) * 1000, n, len(table), conflicts))
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'decisions', 'legacy_types.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=1)
    log('exit %.0f ms -> %s' % ((time.time() - t0) * 1000, path))


if __name__ == '__main__':
    main()
