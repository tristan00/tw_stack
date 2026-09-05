import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg, store2
from migrate.canon_check import text_of

N = int(os.environ.get('UNITS_N', '200'))

HEAD_SQL = """
SELECT d.decision_id, d.ts, bc.z, bw.z FROM decisions d
  JOIN blobs bc ON bc.blob_id = d.campaign_blob
  JOIN blobs bw ON bw.blob_id = d.world_blob
 WHERE d.decision_id %% 100 = 7 ORDER BY d.decision_id LIMIT %s
"""

ENTS_SQL = """
SELECT e.decision_id, e.entity_seq, e.context_kind, e.context_id, b.z
  FROM entities e JOIN blobs b ON b.blob_id = e.features_blob
 WHERE e.decision_id = ANY(%s) ORDER BY e.decision_id, e.entity_seq
"""

OFFERS_SQL = """
SELECT o.decision_id, o.entity_seq, a.action_type, a.action_key, o.offer_seq
  FROM offers o JOIN actions a ON a.action_id = o.action_id
 WHERE o.decision_id = ANY(%s) ORDER BY o.decision_id, o.offer_seq
"""


def log(msg):
    sys.stderr.write('%.3f  units %s\n' % (time.time(), msg))


def main():
    t0 = time.time()
    log('enter n=%d' % N)
    with pg.connect(app_name='tw-unitsread', readonly=True) as rc:
        head = list(rc.execute(HEAD_SQL, (N,)))
        ids = [r[0] for r in head]
        ents, offers = {}, {}
        for did, seq, kind, cid, z in rc.execute(ENTS_SQL, (ids,)):
            ents.setdefault(did, []).append(
                {'context_kind': kind, 'context_id': cid,
                 'state': json.loads(text_of(z))})
        for did, seq, atype, key, oseq in rc.execute(OFFERS_SQL, (ids,)):
            offers.setdefault(did, []).append(
                {'entity_seq': seq, 'action_type': atype, 'key': key,
                 'offer_seq': oseq, 'score': None})
    log('read %d decisions, %d entities, %d offers'
        % (len(head), sum(len(v) for v in ents.values()),
           sum(len(v) for v in offers.values())))

    st = store2.Store()
    st.conn.execute("SET synchronous_commit = off")
    st.conn.execute(
        "TRUNCATE corpus.snapshot, corpus.campaign, corpus.state_set,"
        " corpus.rpc_response, corpus.ucb_pick CASCADE")
    st.campaigns.clear()
    st.characters.clear()
    st.setw.known.clear()
    stats = {'U1': [], 'U2': [], 'U3': []}
    sids = []
    for did, ts, cz, wz in head:
        snap = {'ts': ts, 'campaign': json.loads(text_of(cz)),
                'world': json.loads(text_of(wz)), 'entities': ents.get(did, [])}
        t1 = time.time()
        sid = st.write_snapshot(snap, str(uuid.uuid4()), req_id=str(uuid.uuid4()))
        stats['U1'].append((time.time() - t1) * 1000)
        sids.append(sid)

        offs = offers.get(did) or []
        pick = dict(offs[0], policy='greedy_catboost') if offs else None
        t1 = time.time()
        st.write_decide(sid, offs, pick, timings={'collect_ms': 1, 'store_ms': 2,
                                                  'roundtrip_ms': 3, 'trace_ms': 0,
                                                  'score_ms': 0, 'pickup_lag_ms': 0,
                                                  't_request': ts, 't_received': ts},
                        req_id=str(uuid.uuid4()))
        stats['U2'].append((time.time() - t1) * 1000)

        if pick:
            t1 = time.time()
            st.write_verification(sid, {'executed': True, 'confirmed': True,
                                        'counted': True, 'latency_ms': 12},
                                  req_id=str(uuid.uuid4()))
            stats['U3'].append((time.time() - t1) * 1000)

    camp_key = st.conn.execute(
        "SELECT campaign_key FROM corpus.campaign LIMIT 1").fetchone()[0]
    faction = camp_key.split(':')[0]
    st.write_interrupt({'kind': 'dilemma', 'root': 'r', 'chosen': 'c',
                        'ts': head[0][1], 'latency_ms': 5, 'state_at': 'panel',
                        'campaign': json.loads(text_of(head[0][2])),
                        'world': json.loads(text_of(head[0][3])),
                        'options': [{'key': 'a', 'text': 'A'}, {'key': 'b'}]},
                       req_id=str(uuid.uuid4()))
    st.write_diplomacy({'campaign_key': camp_key, 'kind': 'deal',
                        'channel': 'outgoing', 'ts': head[0][1]},
                       req_id=str(uuid.uuid4()))
    st.write_postmortem({'campaign_key': camp_key, 'outcome': 'completed',
                         'turns_played': 12, 'ts': head[0][1]},
                        req_id=str(uuid.uuid4()))
    cmap = st.conn.execute(
        "SELECT key FROM dict.campaign_map LIMIT 1").fetchone()
    st.write_ucb_pick({'c': 1.0, 'total_plays': 3, 'faction': faction,
                       'campaign_map': cmap[0] if cmap else None,
                       'n': 1, 'tied': 0, 'ts': head[0][1]},
                      req_id=str(uuid.uuid4()))

    counts = {}
    for t in ('snapshot', 'decision', 'decision_timing', 'offer', 'taken', 'interrupt',
              'interrupt_option', 'diplomacy_event', 'postmortem', 'ucb_pick',
              'rpc_response'):
        counts[t] = st.conn.execute('SELECT count(*) FROM corpus."%s"' % t).fetchone()[0]
    log('rows: %s' % json.dumps(counts))

    out = {'n': len(head), 'rows': counts}
    for unit, times in stats.items():
        if times:
            times.sort()
            out[unit] = {'p50_ms': round(times[len(times) // 2], 1),
                         'p90_ms': round(times[int(len(times) * 0.9)], 1),
                         'n': len(times)}
            log('%s p50 %.1f ms p90 %.1f ms n=%d'
                % (unit, out[unit]['p50_ms'], out[unit]['p90_ms'], len(times)))
    st.close()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'units_check.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=2)
    ok = out['U1']['p50_ms'] <= 45 and counts['taken'] > 0 and counts['interrupt'] == 1
    log('exit %.0f ms  %s' % ((time.time() - t0) * 1000, 'PASS' if ok else 'FAIL'))
    if not ok:
        raise SystemExit('units self-check failed')


if __name__ == '__main__':
    main()
