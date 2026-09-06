import argparse
import io
import json
import os
import sys
import time
import uuid
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg, store2

RANGE = 2000


def log(msg):
    sys.stderr.write('%.3f  replay %s\n' % (time.time(), msg))


def text_of(z):
    if isinstance(z, memoryview):
        z = z.tobytes()
    if isinstance(z, (bytes, bytearray)):
        try:
            return zlib.decompress(bytes(z)).decode('utf-8')
        except zlib.error:
            return bytes(z).decode('utf-8')
    return z


def jload(z):
    return json.loads(text_of(z))


SEED_SQL = (
    "INSERT INTO corpus.collector_version (collector_sha, note, emits_campaign_meta,"
    " emits_pending_queue, emits_v31_block, emits_missions) VALUES"
    " ('legacy:meta0:pq0', 'sentinel', false, false, false, false),"
    " ('legacy:meta1:pq0', 'sentinel', true, false, false, false),"
    " ('legacy:meta1:pq1', 'sentinel', true, true, false, false)",
    "INSERT INTO corpus.state_set (kind, hash, n)"
    " SELECT k, sha256(('\\x' || lpad(to_hex(k), 4, '0'))::bytea || '\\x00000000'::bytea), 0"
    " FROM generate_series(1, 29) k",
)


class ReplayStore(store2.Store):

    def __init__(self):
        store2.Store.__init__(self, app_name='tw-replay')
        self.current_campaign_id = None
        self.action_cache = {}

    def _campaign(self, camp):
        return self.current_campaign_id

    def _action(self, action_type, key):
        hit = self.action_cache.get((action_type, key))
        if hit is None:
            hit = store2.Store._action(self, action_type, key)
            self.action_cache[(action_type, key)] = hit
        return hit


def checkpoint(con, stage, state, rows_in=None, rows_out=None, started=None):
    con.execute(
        "INSERT INTO migrate.checkpoint"
        " (stage,range_lo,range_hi,state,worker,started_ts,finished_ts,rows_in,rows_out)"
        " VALUES (%s,0,0,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (stage,range_lo) DO UPDATE SET state=EXCLUDED.state,"
        " finished_ts=EXCLUDED.finished_ts, rows_in=EXCLUDED.rows_in, rows_out=EXCLUDED.rows_out",
        (stage, state, str(os.getpid()), started, time.time(), rows_in, rows_out))


def stage_reset(st):
    t0 = time.time()
    log('M-reset enter')
    tables = [r[0] for r in st.conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'corpus'")]
    st.conn.execute('TRUNCATE %s, ops.launch, ops.trial, ops.trial_campaign,'
                    ' ops.trial_policy, ops.trial_outcome CASCADE'
                    % ', '.join('corpus."%s"' % t for t in tables))
    for sql in SEED_SQL:
        st.conn.execute(sql)
    log('M-reset exit %.0f ms (%d tables)' % ((time.time() - t0) * 1000, len(tables)))


def stage_campaigns(rc, st):
    t0 = time.time()
    log('M0 campaigns enter')
    rows = list(rc.execute(
        "SELECT campaign_id, campaign_key, faction, campaign_map, presave_radius,"
        " selector, picked_ts, difficulty, leader FROM campaigns ORDER BY campaign_id"))
    factions = st.dicts.resolve('faction', [r[2] for r in rows])
    maps = st.dicts.resolve('campaign_map', [r[3] for r in rows if r[3]])
    sels = st.dicts.resolve('selector', [r[5] for r in rows if r[5]])
    for r in rows:
        cid, key, fac, cmap, radius, sel, picked, diff, leader = r
        st.conn.execute(
            "INSERT INTO corpus.campaign (campaign_id, campaign_key, faction_id,"
            " campaign_map_id, presave_radius, selector_id, picked_ts, difficulty, leader)"
            " OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (cid, key, factions[fac], maps.get(cmap), radius, sels.get(sel),
             picked, diff, leader))
        st.campaigns[key] = cid
    st.conn.execute(
        "SELECT setval(pg_get_serial_sequence('corpus.campaign','campaign_id'),"
        " (SELECT MAX(campaign_id) FROM corpus.campaign), true)")
    orphans = list(rc.execute(
        "SELECT DISTINCT d.campaign_key FROM diplomacy_events d"
        " LEFT JOIN campaigns c ON c.campaign_key = d.campaign_key"
        " WHERE c.campaign_id IS NULL AND d.campaign_key IS NOT NULL"))
    from advisor_api import ident as _ident
    for (key,) in orphans:
        fac = _ident.split_campaign_key(key)[0]
        fid = st.dicts.resolve('faction', [fac])[fac]
        cid = st.conn.execute(
            "INSERT INTO corpus.campaign (campaign_key, faction_id, note)"
            " VALUES (%s,%s,%s) RETURNING campaign_id",
            (key, fid, 'diplomacy event key with no recorded campaign')).fetchone()[0]
        st.campaigns[key] = cid
    st.conn.execute(
        "SELECT setval(pg_get_serial_sequence('corpus.campaign','campaign_id'),"
        " (SELECT MAX(campaign_id) FROM corpus.campaign), true)")
    log('M0 campaigns exit %.0f ms rows=%d orphans=%d'
        % ((time.time() - t0) * 1000, len(rows), len(orphans)))
    return len(rows) + len(orphans)


def stage_launches(rc, st):
    n = 0
    for ts, code, payload in rc.execute(
            "SELECT started_ts, code_version, params FROM app.segments ORDER BY segment_id"):
        p = json.loads(payload or '{}')
        st.conn.execute(
            "INSERT INTO ops.launch (ts, code_version, argv) VALUES (%s,%s,%s)",
            (ts, code or '', p.get('argv') or []))
        n += 1
    log('M0 launches rows=%d' % n)
    return n


def _offer_dicts(offer_rows):
    out = []
    for r in offer_rows:
        (did, seq, eseq, atype, akey, params, score, exploit, rank, pct,
         gimp, grank, ggs, ggr) = r
        slot = None
        if params and '"slot_index"' in params:
            slot = json.loads(params).get('slot_index')
            if slot is not None:
                slot = int(float(slot))
        out.append({'entity_seq': eseq, 'action_type': atype, 'key': akey,
                    'slot_index': slot, 'score': score, 'exploit': exploit,
                    'rank': None if rank is None else int(rank),
                    'pct_global': pct, 'gnn_impact': gimp,
                    'gnn_rank': None if grank is None else int(grank),
                    'ggnn_score': ggs,
                    'ggnn_rank': None if ggr is None else int(ggr)})
    return out


def _verification(t):
    (did, oseq, eseq, aid, ts, executed, confirmed, counted, refusal, signal,
     latency, policy, timing, diagnostics, atype, akey) = t
    diag = json.loads(diagnostics) if diagnostics else {}
    stderr = diag.get('stderr')
    if isinstance(stderr, list):
        stderr = '\n'.join(str(x) for x in stderr)
    pre = diag.get('prechecks') or {}
    if counted and refusal is None:
        executed = confirmed = 1
    return {'executed': bool(executed), 'confirmed': bool(confirmed),
            'counted': bool(counted), 'refusal': refusal,
            'confirm': {'signal': signal, 'latency_ms': latency},
            'timing': json.loads(timing) if timing else {},
            'prechecks_passed': pre.get('passed'),
            'doomed': diag.get('doomed'), 'stderr': stderr}


def stage_decisions(rc, st, lo, hi):
    rows = list(rc.execute(
        "SELECT d.decision_id, d.campaign_id, d.ts, d.timings, bc.z, bw.z"
        " FROM decisions d JOIN blobs bc ON bc.blob_id = d.campaign_blob"
        " JOIN blobs bw ON bw.blob_id = d.world_blob"
        " WHERE d.decision_id BETWEEN %s AND %s ORDER BY d.decision_id", (lo, hi)))
    if not rows:
        return 0
    ids = [r[0] for r in rows]
    ents = {}
    for did, seq, kind, cid, z in rc.execute(
            "SELECT e.decision_id, e.entity_seq, e.context_kind, e.context_id, b.z"
            " FROM entities e JOIN blobs b ON b.blob_id = e.features_blob"
            " WHERE e.decision_id = ANY(%s) ORDER BY e.decision_id, e.entity_seq", (ids,)):
        ents.setdefault(did, []).append(
            {'context_kind': kind, 'context_id': cid, 'state': jload(z)})
    offers = {}
    for r in rc.execute(
            "SELECT o.decision_id, o.offer_seq, o.entity_seq, a.action_type,"
            " a.action_key, a.params, s.score, s.exploit, s.rank, s.pct_global,"
            " s.gnn_impact, s.gnn_rank, m.score, m.rank"
            " FROM offers o JOIN actions a ON a.action_id = o.action_id"
            " LEFT JOIN offer_scores s ON s.decision_id = o.decision_id"
            "  AND s.offer_seq = o.offer_seq"
            " LEFT JOIN offer_model_scores m ON m.decision_id = o.decision_id"
            "  AND m.offer_seq = o.offer_seq AND m.model = 'greedy_gnn'"
            " WHERE o.decision_id = ANY(%s) ORDER BY o.decision_id, o.offer_seq", (ids,)):
        offers.setdefault(r[0], []).append(r)
    takens = {}
    for t in rc.execute(
            "SELECT t.decision_id, t.offer_seq, t.entity_seq, t.action_id, t.ts,"
            " t.executed, t.confirmed, t.counted, t.refusal, t.confirm_signal,"
            " t.latency_ms, t.policy, t.timing, t.diagnostics, a.action_type, a.action_key"
            " FROM taken t JOIN actions a ON a.action_id = t.action_id"
            " WHERE t.decision_id = ANY(%s)", (ids,)):
        takens[t[0]] = t

    for did, campaign_id, ts, timings, cz, wz in rows:
        st.current_campaign_id = campaign_id
        st.conn.execute(
            "SELECT setval(pg_get_serial_sequence('corpus.snapshot','snapshot_id'),"
            " %s, false)", (did,))
        snap = {'ts': ts, 'campaign': jload(cz), 'world': jload(wz),
                'entities': ents.get(did) or []}
        sid = st.write_snapshot(snap, str(uuid.uuid4()))
        if sid != did:
            raise SystemExit('snapshot_id %s != decision_id %s' % (sid, did))
        t = takens.get(did)
        pick = None
        if t is not None:
            pick = {'offer_seq': t[1], 'entity_seq': t[2], 'action_type': t[14],
                    'key': t[15], 'policy': t[11]}
        st.write_decide(did, _offer_dicts(offers.get(did) or []), pick,
                        timings=json.loads(timings) if timings else None)
        if t is not None:
            st.write_verification(did, _verification(t))
            st.conn.execute("UPDATE corpus.taken SET ts = %s WHERE decision_id = %s",
                            (t[4], did))
            fp = (json.loads(t[13]).get('prechecks') or {}).get('failed_precheck') \
                if t[13] else None
            if fp:
                pid = st.dicts.resolve_enum('precheck', [fp])[fp]
                st.conn.execute(
                    "UPDATE corpus.taken SET failed_precheck_id = %s"
                    " WHERE decision_id = %s", (pid, did))
    return len(rows)


def _options_list(oj):
    if isinstance(oj, dict):
        return [dict(v or {}, key=k) for k, v in oj.items()]
    out = []
    for i, o in enumerate(oj or []):
        o = dict(o or {})
        if not o.get('key'):
            o['key'] = str(o.get('label') or o.get('option') or i)
        out.append(o)
    return out


def _flag(v):
    return None if v is None else bool(v)


def stage_interrupts(rc, st, lo, hi):
    rows = list(rc.execute(
        "SELECT i.interrupt_id, i.ts, i.campaign_id, i.kind, i.root, i.root_context,"
        " i.options_json, i.chosen, i.executed, i.confirmed, i.counted, i.refusal,"
        " i.latency_ms, i.policy, bc.z, bw.z, bp.z"
        " FROM interrupts i LEFT JOIN blobs bc ON bc.blob_id = i.campaign_blob"
        " LEFT JOIN blobs bw ON bw.blob_id = i.world_blob"
        " LEFT JOIN blobs bp ON bp.blob_id = i.panel_blob"
        " WHERE i.interrupt_id BETWEEN %s AND %s ORDER BY i.interrupt_id", (lo, hi)))
    for r in rows:
        (iid, ts, campaign_id, kind, root, root_context, oj, chosen, executed,
         confirmed, counted, refusal, latency, policy, cz, wz, bz) = r
        st.current_campaign_id = campaign_id
        rec = {'kind': kind, 'root': root, 'root_context': root_context,
               'chosen': chosen, 'executed': _flag(executed),
               'confirmed': _flag(confirmed), 'counted': _flag(counted),
               'refusal': refusal, 'latency_ms': latency, 'ts': ts, 'rpc_ts': ts,
               'policy': policy, 'state_at': 'recorder',
               'campaign': jload(cz) if cz is not None else {},
               'world': jload(wz) if wz is not None else {},
               'panel': jload(bz) if bz is not None else {},
               'options': _options_list(json.loads(oj) if oj else [])}
        sid = st.write_interrupt(rec)
        st.conn.execute(
            "UPDATE corpus.interrupt SET legacy_interrupt_id = %s"
            " WHERE interrupt_id = %s", (iid, sid))
    return len(rows)


def _strs(v):
    if v is None:
        return None
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def _diplo_kind(d):
    if 'pair' in d and 'channel' not in d:
        return 'pair_checkpoint'
    if 'turns_played' in d:
        return 'campaign_end'
    return 'deal'


def stage_diplomacy(rc, st):
    t0 = time.time()
    log('M5 diplomacy enter')
    rows = list(rc.execute(
        "SELECT event_id, ts, campaign_key, turn, payload"
        " FROM diplomacy_events ORDER BY event_id"))
    camp_ids = dict(st.conn.execute(
        "SELECT campaign_key, campaign_id FROM corpus.campaign"))
    n = 0
    for event_id, ts, key, turn, payload in rows:
        d = json.loads(payload or '{}')
        kind = _diplo_kind(d)
        kid = st.dicts.resolve_enum('diplo_event_kind', [kind])[kind]
        chan = d.get('channel')
        cid = (st.dicts.resolve_enum('diplo_channel', [chan])[chan]
               if chan else None)
        fac = d.get('faction')
        fid = st.dicts.resolve('faction', [fac]).get(fac) if fac else None
        terms = d.get('terms') or []
        tids = [st.dicts.resolve_enum('diplo_term', [t])[t] for t in terms] or None
        gift = d.get('gift')
        gid = st.dicts.resolve_enum('gift', [gift])[gift] if gift else None
        panel = d.get('panel') or {}
        pair = d.get('pair') or {}
        tracked = d.get('tracked') or []
        trids = ([st.dicts.resolve('faction', [f])[f] for f in tracked]
                 or None)
        standing = pair.get('standing')
        st.conn.execute(
            "INSERT INTO corpus.diplomacy_event (event_id, campaign_id, turn, ts,"
            " ts_recorded, kind_id, channel_id, faction_id, term_ids, gift_id, ok,"
            " failed_at, success_chance, accepted, chosen, answer, executed,"
            " confirmed, policy_id, proposer, speech, attitude, pair_at_war,"
            " pair_allied, pair_trade, pair_our_master, pair_their_vassal,"
            " pair_standing, turns_played, ended_by, tracked_faction_ids)"
            " OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (event_id, camp_ids[key], turn or 0, ts, ts, kid, cid, fid, tids, gid,
             d.get('ok'), panel.get('failed_at'), panel.get('success_chance'),
             panel.get('accepted'), d.get('chosen'), d.get('answer'),
             d.get('executed'), d.get('confirmed'),
             (st.dicts.resolve_enum('policy', [d['policy']])[d['policy']]
              if d.get('policy') else None),
             d.get('proposer'), d.get('speech'), d.get('attitude'),
             pair.get('at_war'), pair.get('allied'), pair.get('trade'),
             pair.get('our_master'), pair.get('their_vassal'),
             int(round(standing)) if standing is not None else None,
             d.get('turns_played'), _strs(d.get('ended_by')), trids))
        n += 1
    st.conn.execute(
        "SELECT setval(pg_get_serial_sequence('corpus.diplomacy_event','event_id'),"
        " (SELECT MAX(event_id) FROM corpus.diplomacy_event), true)")
    log('M5 diplomacy exit %.0f ms rows=%d' % ((time.time() - t0) * 1000, n))
    return n


def stage_postmortems(rc, st):
    t0 = time.time()
    log('M6 postmortems enter')
    n = 0
    for pm_id, key, ts, run_dir, faction, payload in rc.execute(
            "SELECT postmortem_id, campaign_key, ts, run_dir, faction, payload"
            " FROM postmortems ORDER BY postmortem_id"):
        rec = dict(json.loads(payload or '{}'), campaign_key=key, ts=ts,
                   run_dir=run_dir or '', faction=faction)
        st.write_postmortem(rec)
        n += 1
    log('M6 postmortems exit %.0f ms rows=%d' % ((time.time() - t0) * 1000, n))
    return n


def stage_picks(rc, st):
    t0 = time.time()
    log('M7 picks enter')
    picks = list(rc.execute("SELECT * FROM ucb_picks ORDER BY pick_id"))
    prows = {}
    for r in rc.execute("SELECT * FROM ucb_pick_rows ORDER BY pick_id, rank"):
        prows.setdefault(r[0], []).append(r)
    maps = st.dicts.resolve('campaign_map',
                            [p[4] for p in picks if p[4]])
    factions = st.dicts.resolve('faction', [p[5] for p in picks])
    for p in picks:
        (pick_id, ts, c, total_plays, cmap, fac, n, mean, explore, score, tied,
         blend, entropy, std, adjust) = p
        st.conn.execute(
            "INSERT INTO corpus.ucb_pick (pick_id, ts, c, total_plays,"
            " campaign_map_id, faction_id, n, mean, explore, score, tied, blend,"
            " entropy, std, adjust) OVERRIDING SYSTEM VALUE"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (pick_id, ts, c or 0.0, total_plays or 0, maps[cmap], factions[fac],
             n or 0, mean, explore, score, tied or 0, blend, entropy, std, adjust))
        rows = prows.get(pick_id) or []
        rmaps = st.dicts.resolve('campaign_map', [r[2] for r in rows])
        rfacs = st.dicts.resolve('faction', [r[3] for r in rows])
        for r in rows:
            st.conn.execute(
                "INSERT INTO corpus.ucb_pick_row (pick_id, rank, campaign_map_id,"
                " faction_id, n, mean, explore, score, chosen, blend, entropy,"
                " std, adjust) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (pick_id, r[1], rmaps[r[2]], rfacs[r[3]], r[4], r[5], r[6], r[7],
                 bool(r[8]), r[9], r[10], r[11], r[12]))
    st.conn.execute(
        "SELECT setval(pg_get_serial_sequence('corpus.ucb_pick','pick_id'),"
        " (SELECT MAX(pick_id) FROM corpus.ucb_pick), true)")
    log('M7 picks exit %.0f ms picks=%d rows=%d'
        % ((time.time() - t0) * 1000, len(picks), sum(len(v) for v in prows.values())))
    return len(picks)


def stage_fixups(rc, st):
    t0 = time.time()
    log('M8 fixups enter')
    outcomes = st.dicts.resolve_enum(
        'outcome', [r[0] for r in rc.execute(
            "SELECT DISTINCT outcome FROM campaigns WHERE outcome IS NOT NULL")])
    for cid, outcome, defeated, turns, picked in rc.execute(
            "SELECT campaign_id, outcome, defeated, turns, picked_ts FROM campaigns"):
        st.conn.execute(
            "UPDATE corpus.campaign SET outcome_id = %s, defeated = %s,"
            " turns = GREATEST(turns, %s), picked_ts = COALESCE(%s, picked_ts)"
            " WHERE campaign_id = %s",
            (outcomes.get(outcome), _flag(defeated), turns or 0, picked, cid))
    by_key = {}
    for picked, cmap, fac, key in rc.execute(
            "SELECT picked_ts, campaign_map, faction, campaign_key FROM campaigns"
            " WHERE picked_ts IS NOT NULL ORDER BY picked_ts"):
        by_key.setdefault((cmap, fac), []).append((picked, key))
    used, n = set(), 0
    for pick_id, ts, cmap, fac in rc.execute(
            "SELECT pick_id, ts, campaign_map, faction FROM ucb_picks"
            " ORDER BY pick_id"):
        for cts, key in by_key.get((cmap, fac), []):
            if key in used:
                continue
            dt = cts - ts
            if dt > 120.0:
                break
            if dt >= -1.0:
                used.add(key)
                st.conn.execute(
                    "UPDATE corpus.campaign SET ucb_pick_id = %s"
                    " WHERE campaign_key = %s", (pick_id, key))
                n += 1
                break
    log('M8 fixups exit %.0f ms ucb_pick_id matched=%d'
        % ((time.time() - t0) * 1000, n))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--range', type=int, default=RANGE)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--resume', action='store_true')
    args = ap.parse_args()
    t0 = time.time()
    log('replay enter port=%s' % os.environ.get('TW_PG_PORT'))
    store2.log = lambda m: None
    from decisions import sets, dicts
    sets.log = lambda m: None
    dicts.log = lambda m: None
    pg.log = lambda m: None
    rc = pg.connect(app_name='tw-replayread', readonly=True, autocommit=True)
    st = ReplayStore()
    st.conn.execute('SET synchronous_commit = off')
    out = {'ts': time.time()}
    if not args.resume:
        stage_reset(st)
        st.dicts.cache.clear()
        st.dicts.enum_cache.clear()
        out['campaigns'] = stage_campaigns(rc, st)
        out['launches'] = stage_launches(rc, st)
        checkpoint(st.conn, 'R-M0', 'done', out['campaigns'])
    else:
        st.campaigns.update(
            {k: v for k, v in st.conn.execute(
                'SELECT campaign_key, campaign_id FROM corpus.campaign')})

    lo, hi = rc.execute('SELECT MIN(decision_id), MAX(decision_id) FROM decisions'
                        ).fetchone()
    if args.resume:
        done_hi = st.conn.execute(
            'SELECT COALESCE(MAX(decision_id), 0) FROM corpus.decision').fetchone()[0]
        lo = max(lo, int(done_hi) + 1)
        log('resume from decision %d' % lo)
    if args.limit:
        hi = min(hi, lo + args.limit - 1)
    done = 0
    t1 = time.time()
    for start in range(lo, hi + 1, args.range):
        n = stage_decisions(rc, st, start, min(start + args.range - 1, hi))
        done += n
        rate = done / max(time.time() - t1, 0.001)
        log('M1 range %d..%d done=%d (%.0f/s, eta %.0f min)'
            % (start, min(start + args.range - 1, hi), done, rate,
               (206907 - done) / max(rate, 1) / 60))
    out['decisions'] = done
    checkpoint(st.conn, 'R-M1', 'done', done)

    ilo, ihi = rc.execute(
        'SELECT MIN(interrupt_id), MAX(interrupt_id) FROM interrupts').fetchone()
    if args.limit:
        ihi = min(ihi, ilo + args.limit - 1)
    idone = 0
    for start in range(ilo, ihi + 1, args.range):
        idone += stage_interrupts(rc, st, start, min(start + args.range - 1, ihi))
        log('M4 range %d..%d done=%d' % (start, min(start + args.range - 1, ihi), idone))
    out['interrupts'] = idone
    checkpoint(st.conn, 'R-M4', 'done', idone)

    out['diplomacy'] = stage_diplomacy(rc, st)
    checkpoint(st.conn, 'R-M5', 'done', out['diplomacy'])
    out['postmortems'] = stage_postmortems(rc, st)
    checkpoint(st.conn, 'R-M6', 'done', out['postmortems'])
    out['picks'] = stage_picks(rc, st)
    checkpoint(st.conn, 'R-M7', 'done', out['picks'])
    out['ucb_matched'] = stage_fixups(rc, st)
    checkpoint(st.conn, 'R-M8', 'done', out['ucb_matched'])
    out['total_min'] = round((time.time() - t0) / 60, 1)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'replay.json')
    io.open(path, 'w', encoding='utf-8', newline='\n').write(
        json.dumps(out, indent=2) + '\n')
    log('replay exit %.1f min %s' % ((time.time() - t0) / 60, json.dumps(out)))


if __name__ == '__main__':
    main()
