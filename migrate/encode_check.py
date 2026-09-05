import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, hydrate, pg
from migrate.canon_check import ROLES, text_of


def log(msg):
    sys.stderr.write('%.3f  %s\n' % (time.time(), msg))


def check_role(con, role, sql):
    log('%s enter' % role)
    t0 = time.time()
    n = ok = 0
    hashes = collections.defaultdict(set)
    sets_seen = 0
    encode_ms = 0.0
    failures = collections.Counter()
    for (z,) in con.execute(sql):
        rec = canon.normalise(json.loads(text_of(z)))
        n += 1
        t1 = time.time()
        enc = hydrate.encode_record(rec)
        encode_ms += (time.time() - t1) * 1000.0
        back = hydrate.hydrate_record(enc)
        if canon.canon(back) == canon.canon(rec):
            ok += 1
        else:
            failures['identity'] += 1
        for key, s in enc['sets'].items():
            sets_seen += 1
            hashes[s['kind']].add(s['hash'])
    dedup = {k: len(v) for k, v in sorted(hashes.items())}
    log('%s exit %.0f ms  n=%d identity=%d sets=%d distinct=%d encode=%.3f ms/rec'
        % (role, (time.time() - t0) * 1000, n, ok, sets_seen,
           sum(dedup.values()), encode_ms / max(n, 1)))
    return {'n': n, 'identity': ok, 'sets': sets_seen, 'distinct_sets': sum(dedup.values()),
            'encode_ms_per_record': round(encode_ms / max(n, 1), 4),
            'failures': dict(failures)}


def main():
    t0 = time.time()
    log('encode_check enter port=%s' % os.environ.get('TW_PG_PORT', '55433'))
    out = {}
    with pg.connect(readonly=True) as con:
        con.execute("SET statement_timeout='900s'")
        for role, sql in ROLES.items():
            out[role] = check_role(con, role, sql)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'encode_check.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=2)
    n = sum(v['n'] for v in out.values())
    ok = sum(v['identity'] for v in out.values())
    log('encode_check exit %.0f ms  total=%d identity=%d (%.3f%%)'
        % ((time.time() - t0) * 1000, n, ok, 100.0 * ok / max(n, 1)))


main()
