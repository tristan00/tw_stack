import json
import os
import statistics
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, hydrate, pg, store2
from migrate.canon_check import text_of

N = int(os.environ.get('HYDRATE_N', '200'))

HEAD_SQL = """
SELECT d.decision_id, d.ts, d.turn, c.campaign_key, bc.z, bw.z FROM decisions d
  JOIN campaigns c ON c.campaign_id = d.campaign_id
  JOIN blobs bc ON bc.blob_id = d.campaign_blob
  JOIN blobs bw ON bw.blob_id = d.world_blob
 WHERE mod(d.decision_id, 100) = 7 ORDER BY d.decision_id DESC LIMIT %s
"""

ENTS_SQL = """
SELECT e.decision_id, e.entity_seq, e.context_kind, e.context_id, b.z
  FROM entities e JOIN blobs b ON b.blob_id = e.features_blob
 WHERE e.decision_id = ANY(%s) ORDER BY e.decision_id, e.entity_seq
"""


def log(msg):
    sys.stderr.write('%.3f  hydrate_check %s\n' % (time.time(), msg))


def first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i, b[max(0, i - 100):i + 100], a[max(0, i - 100):i + 100]
    return min(len(a), len(b)), b[-180:], a[-180:]


def main():
    t0 = time.time()
    log('enter n=%d' % N)
    with pg.connect(app_name='tw-hydrateread', readonly=True) as rc:
        head = list(rc.execute(HEAD_SQL, (N,)))
        ids = [r[0] for r in head]
        ents = {}
        for did, seq, kind, cid, z in rc.execute(ENTS_SQL, (ids,)):
            ents.setdefault(did, []).append(
                {'context_kind': kind, 'context_id': cid,
                 'state': json.loads(text_of(z))})
    log('read %d decisions, %d entities' % (len(head), sum(map(len, ents.values()))))

    pg.PORT = int(os.environ.get('HYDRATE_WRITE_PORT', '55433'))
    pg.DB = os.environ.get('HYDRATE_WRITE_DB', 'tw_stack_design_scratch')
    st = store2.Store(app_name='tw-hydratecheck')
    st.conn.execute('SET synchronous_commit = off')
    st.conn.execute(
        'TRUNCATE corpus.snapshot, corpus.campaign, corpus.state_set,'
        ' corpus.rpc_response, corpus.ucb_pick CASCADE')
    st.campaigns.clear()
    st.characters.clear()
    st.setw.known.clear()
    hydrate._SET_CACHE.clear()

    written = []
    for did, ts, turn, ckey, cz, wz in head:
        snap = {'ts': ts, 'campaign': json.loads(text_of(cz)),
                'world': json.loads(text_of(wz)), 'entities': ents.get(did, [])}
        sid = st.write_snapshot(snap, str(uuid.uuid4()))
        written.append((sid, did, snap))

    times = []
    ok = bad = 0
    mismatches = []
    for sid, did, snap in written:
        t1 = time.time()
        got = hydrate.record(st.conn, sid, legacy=True)
        times.append((time.time() - t1) * 1000)
        pairs = [('CB', snap['campaign'], got['campaign']),
                 ('WB', snap['world'], got['world'])]
        for i, e in enumerate(snap['entities']):
            ge = got['entities'][i] if i < len(got['entities']) else {}
            pairs.append(('EB:%s' % e['context_kind'], e['state'],
                          ge.get('state') or {}))
            if str(ge.get('context_id')) != str(e.get('context_id')):
                pairs.append(('EB:%s:context_id' % e['context_kind'],
                              {'v': e.get('context_id')}, {'v': ge.get('context_id')}))
        rec_bad = []
        for role, want, have in pairs:
            cw = canon.canon(canon.normalise(want))
            ch = canon.canon(canon.normalise(have))
            if cw != ch:
                rec_bad.append((role, first_diff(ch, cw)))
        if rec_bad:
            bad += 1
            if len(mismatches) < 6:
                role, (pos, w, h) = rec_bad[0]
                mismatches.append({'decision_id': did, 'snapshot_id': sid,
                                   'role': role, 'pos': pos, 'want': w, 'have': h,
                                   'roles': sorted({r for r, _ in rec_bad})})
        else:
            ok += 1

    times.sort()
    p50 = times[len(times) // 2]
    p90 = times[int(len(times) * 0.9)]
    out = {'n': len(written), 'ok': ok, 'bad': bad,
           'p50_ms': round(p50, 1), 'p90_ms': round(p90, 1),
           'mean_ms': round(statistics.fmean(times), 1), 'mismatches': mismatches}
    st.close()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hydrate_check.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=1)
    log('exit %.0f ms ok=%d bad=%d p50=%.1f ms  %s'
        % ((time.time() - t0) * 1000, ok, bad, p50,
           'PASS' if bad == 0 else 'FAIL'))
    if bad:
        for m in mismatches:
            log('mismatch %s %s @%s\n  want: %s\n  have: %s'
                % (m['decision_id'], m['role'], m['pos'], m['want'], m['have']))
        raise SystemExit('hydrate round-trip failed')


if __name__ == '__main__':
    main()
