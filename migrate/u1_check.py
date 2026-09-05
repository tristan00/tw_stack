import json
import os
import statistics
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, pg, store2
from migrate.canon_check import text_of

SAMPLE_SQL = """
SELECT d.decision_id, d.ts, bc.z, bw.z
  FROM decisions d
  JOIN blobs bc ON bc.blob_id = d.campaign_blob
  JOIN blobs bw ON bw.blob_id = d.world_blob
 WHERE d.decision_id %% 100 = 7
 ORDER BY d.decision_id
 LIMIT %s
"""

ENTS_SQL = """
SELECT e.decision_id, e.entity_seq, e.context_kind, e.context_id, b.z
  FROM entities e JOIN blobs b ON b.blob_id = e.features_blob
 WHERE e.decision_id = ANY(%s) ORDER BY e.decision_id, e.entity_seq
"""

N = int(os.environ.get('U1_N', '200'))


def log(msg):
    sys.stderr.write('%.3f  u1_check %s\n' % (time.time(), msg))


def main():
    t0 = time.time()
    log('enter n=%d' % N)
    with pg.connect(app_name='tw-u1read', readonly=True) as rc:
        head = list(rc.execute(SAMPLE_SQL, (N,)))
        ids = [r[0] for r in head]
        ents = {}
        for did, seq, kind, cid, z in rc.execute(ENTS_SQL, (ids,)):
            ents.setdefault(did, []).append(
                {'context_kind': kind, 'context_id': cid,
                 'state': json.loads(text_of(z))})
    log('read %d decisions, %d entity blobs'
        % (len(head), sum(len(v) for v in ents.values())))

    st = store2.Store()
    st.conn.execute("SET synchronous_commit = off")
    times, written = [], []
    for did, ts, cz, wz in head:
        snap = {'ts': ts, 'campaign': json.loads(text_of(cz)),
                'world': json.loads(text_of(wz)), 'entities': ents.get(did, [])}
        u = str(uuid.uuid4())
        t1 = time.time()
        sid = st.write_snapshot(snap, u)
        times.append((time.time() - t1) * 1000.0)
        written.append((sid, did, snap))

    times.sort()
    p50 = times[len(times) // 2]
    p90 = times[int(len(times) * 0.9)]
    log('U1 p50 %.1f ms  p90 %.1f ms  n=%d' % (p50, p90, len(times)))

    again = st.write_snapshot(
        {'ts': head[0][1], 'campaign': json.loads(text_of(head[0][2])),
         'world': json.loads(text_of(head[0][3])), 'entities': ents.get(head[0][0], [])},
        str(uuid.uuid4()))
    log('second write of the same content -> snapshot_id=%d (sets deduped)' % again)

    counts = {}
    for table in ('snapshot', 'decision', 'snapshot_campaign', 'snapshot_world',
                  'world_army', 'world_hostile', 'snapshot_entity', 'char_state',
                  'char_state_ext', 'province_state', 'campaign_state', 'character'):
        counts[table] = st.conn.execute(
            'SELECT count(*) FROM corpus."%s"' % table).fetchone()[0]
    log('rows: %s' % json.dumps(counts))
    st.close()

    out = {'n': len(times), 'p50_ms': round(p50, 1), 'p90_ms': round(p90, 1),
           'mean_ms': round(statistics.fmean(times), 1), 'rows': counts,
           'threshold_p50_ms': 45}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'u1_check.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=2)
    log('exit %.0f ms  %s' % ((time.time() - t0) * 1000,
                              'PASS' if p50 <= 45 else 'OVER THRESHOLD'))


if __name__ == '__main__':
    main()
