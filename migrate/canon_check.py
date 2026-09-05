import collections
import json
import os
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, pg

ROLES = {
    'CB': "SELECT b.z FROM decisions d JOIN blobs b ON b.blob_id=d.campaign_blob WHERE d.decision_id % 100 = 7",
    'WB': "SELECT b.z FROM decisions d JOIN blobs b ON b.blob_id=d.world_blob WHERE d.decision_id % 100 = 7",
    'EB': "SELECT b.z FROM decisions d JOIN entities e ON e.decision_id=d.decision_id"
          " JOIN blobs b ON b.blob_id=e.features_blob WHERE d.decision_id % 100 = 7",
}


def log(msg):
    sys.stderr.write('%.3f  %s\n' % (time.time(), msg))


def text_of(z):
    if isinstance(z, memoryview):
        z = z.tobytes()
    if isinstance(z, (bytes, bytearray)):
        try:
            return zlib.decompress(bytes(z)).decode('utf-8')
        except zlib.error:
            return bytes(z).decode('utf-8')
    return z


def check_role(con, role, sql):
    log('%s enter' % role)
    t0 = time.time()
    n = exact = 0
    types_seen = collections.defaultdict(set)
    failures = collections.Counter()
    example = None
    for (z,) in con.execute(sql):
        raw = text_of(z)
        try:
            rec = json.loads(raw)
        except ValueError:
            failures['unparseable'] += 1
            continue
        n += 1
        learned = {}
        try:
            norm = canon.normalise(rec, '', learned)
        except canon.CollectError as exc:
            failures['CollectError: %s' % str(exc)[:60]] += 1
            continue
        for path, kind in learned.items():
            types_seen[path].add(kind)
        back = canon.canon(canon.legacy_view(norm, learned))
        if canon.canon(canon.normalise(json.loads(back))) == canon.canon(norm):
            exact += 1
            if back != raw:
                failures['textual_only'] += 1
        else:
            failures['semantic'] += 1
            if example is None:
                a = canon.canon(canon.normalise(json.loads(back)))
                b = canon.canon(norm)
                for i, (x, y) in enumerate(zip(a, b)):
                    if x != y:
                        example = (role, i, b[max(0, i-90):i+90], a[max(0, i-90):i+90])
                        break
                else:
                    example = (role, -1, b[:180], a[:180])
    conflicts = {p: sorted(k) for p, k in types_seen.items() if len(k) > 1}
    log('%s exit %.0f ms  n=%d exact=%d (%.2f%%) conflicts=%d'
        % (role, (time.time() - t0) * 1000, n, exact, 100.0 * exact / max(n, 1), len(conflicts)))
    return {'n': n, 'exact': exact,
            'textual_only': failures.get('textual_only', 0),
            'semantic': failures.get('semantic', 0), 'paths': len(types_seen),
            'conflicting_paths': conflicts, 'failures': dict(failures.most_common(6)),
            'first_diff': example}


def main():
    t0 = time.time()
    log('canon_check enter port=%s' % os.environ.get('TW_PG_PORT', '55432'))
    out = {}
    with pg.connect(readonly=True) as con:
        con.execute("SET statement_timeout='900s'")
        for role, sql in ROLES.items():
            out[role] = check_role(con, role, sql)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'canon_check.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=2)
    total = sum(v['n'] for v in out.values())
    exact = sum(v['exact'] for v in out.values())
    log('canon_check exit %.0f ms  total=%d exact=%d (%.3f%%)'
        % ((time.time() - t0) * 1000, total, exact, 100.0 * exact / max(total, 1)))


if __name__ == '__main__':
    main()
