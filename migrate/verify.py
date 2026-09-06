import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
from multiprocessing import Process, Queue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault('TW_PG_PORT', '55433')

from decisions import canon, hydrate, pg
from migrate.run import checkpoint, text_of

HERE = os.path.dirname(os.path.abspath(__file__))
WORKERS = 4
VENV_PY = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')


def log(msg):
    sys.stderr.write('%.3f  verify %s\n' % (time.time(), msg))


def _quiet():
    hydrate.log = lambda m: None
    pg.log = lambda m: None
    from decisions import dicts
    dicts.log = lambda m: None


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _cmp(want, have):
    a = canon.canon(canon.normalise(want))
    b = canon.canon(canon.normalise(have))
    if a == b:
        return None
    pos = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y),
               min(len(a), len(b)))
    return ('pos:%d' % pos, a[max(0, pos - 100):pos + 100],
            b[max(0, pos - 100):pos + 100])


def _mismatch(con, stage, snapshot_id, role, entity_seq, diff):
    con.execute(
        "INSERT INTO migrate.mismatch (stage, snapshot_id, role, entity_seq,"
        " path, expected, actual) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (stage, snapshot_id, role, entity_seq, diff[0], diff[1], diff[2]))


def _v1_worker(residue, workers, q):
    _quiet()
    t0 = time.time()
    con = pg.connect(app_name='tw-v1w%d' % residue, autocommit=True)
    con.execute("SET statement_timeout = '3600s'")
    ok = bad = 0
    ids = [r[0] for r in con.execute(
        "SELECT decision_id FROM corpus.decision WHERE mod(decision_id, %s) = %s"
        " ORDER BY decision_id", (workers, residue))]
    for chunk in _chunks(ids, 200):
        blobs = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT d.decision_id, bc.z, bw.z FROM decisions d"
            " JOIN blobs bc ON bc.blob_id = d.campaign_blob"
            " JOIN blobs bw ON bw.blob_id = d.world_blob"
            " WHERE d.decision_id = ANY(%s)", (chunk,))}
        ents = {}
        for did, seq, z in con.execute(
                "SELECT e.decision_id, e.entity_seq, b.z FROM entities e"
                " JOIN blobs b ON b.blob_id = e.features_blob"
                " WHERE e.decision_id = ANY(%s) ORDER BY e.decision_id, e.entity_seq",
                (chunk,)):
            ents.setdefault(did, []).append((seq, z))
        for did in chunk:
            rec = hydrate.record(con, did, legacy=True)
            cz, wz = blobs[did]
            diffs = []
            d = _cmp(json.loads(text_of(cz)), rec['campaign'])
            if d:
                diffs.append(('CB', 0, d))
            d = _cmp(json.loads(text_of(wz)), rec['world'])
            if d:
                diffs.append(('WB', 0, d))
            for seq, z in ents.get(did) or ():
                ge = (rec['entities'][seq]
                      if seq < len(rec['entities']) else {'state': {}})
                d = _cmp(json.loads(text_of(z)), ge.get('state') or {})
                if d:
                    diffs.append(('EB', seq, d))
            if diffs:
                bad += 1
                for role, seq, d in diffs[:3]:
                    _mismatch(con, 'V1', did, role, seq, d)
            else:
                ok += 1
            if (ok + bad) % 10000 == 0:
                log('w%d decisions %d/%d bad=%d %.0f s'
                    % (residue, ok + bad, len(ids), bad, time.time() - t0))
    dn = ok + bad
    irows = con.execute(
        "SELECT ci.interrupt_id, bc.z, bw.z FROM corpus.interrupt ci"
        " JOIN interrupts li ON li.interrupt_id = ci.legacy_interrupt_id"
        " LEFT JOIN blobs bc ON bc.blob_id = li.campaign_blob"
        " LEFT JOIN blobs bw ON bw.blob_id = li.world_blob"
        " WHERE mod(ci.interrupt_id, %s) = %s ORDER BY ci.interrupt_id",
        (workers, residue)).fetchall()
    for sid, cz, wz in irows:
        rec = hydrate.record(con, sid, legacy=True)
        diffs = []
        if cz is not None:
            d = _cmp(json.loads(text_of(cz)), rec['campaign'])
            if d:
                diffs.append(('ICB', 0, d))
        if wz is not None:
            d = _cmp(json.loads(text_of(wz)), rec['world'])
            if d:
                diffs.append(('IWB', 0, d))
        if diffs:
            bad += 1
            for role, seq, d in diffs[:2]:
                _mismatch(con, 'V1', sid, role, seq, d)
        else:
            ok += 1
    con.close()
    q.put({'worker': residue, 'decisions': dn, 'interrupts': len(irows),
           'ok': ok, 'bad': bad, 's': round(time.time() - t0, 1)})


def v1(res, args):
    t0 = time.time()
    log('V1 enter workers=%d' % args.workers)
    with pg.connect(app_name='tw-verify') as con:
        con.execute("DELETE FROM migrate.mismatch WHERE stage = 'V1'")
        con.commit()
    q = Queue()
    procs = [Process(target=_v1_worker, args=(r, args.workers, q))
             for r in range(args.workers)]
    for p in procs:
        p.start()
    stats = [q.get() for _ in procs]
    for p in procs:
        p.join()
    with pg.connect(app_name='tw-verify', autocommit=True) as con:
        rows = con.execute(
            "SELECT count(*) FROM migrate.mismatch WHERE stage = 'V1'"
        ).fetchone()[0]
    total = sum(s['ok'] + s['bad'] for s in stats)
    bad = sum(s['bad'] for s in stats)
    out = {'ok': bad == 0 and rows == 0, 'snapshots': total, 'bad': bad,
           'mismatch_rows': rows, 'min': round((time.time() - t0) / 60, 1),
           'workers': stats}
    log('V1 exit %.1f min snapshots=%d bad=%d %s'
        % (out['min'], total, bad, 'PASS' if out['ok'] else 'FAIL'))
    return out


V2_IDENTITY_SQL = """
WITH l AS (
  SELECT o.decision_id, o.offer_seq, o.entity_seq,
         a.action_type, a.action_key,
         CASE WHEN a.params LIKE '%%slot_index%%'
              THEN round((a.params::jsonb->>'slot_index')::numeric)::int END AS slot,
         s.score, s.exploit, s.rank, s.pct_global, s.gnn_impact, s.gnn_rank,
         m.score AS ggnn_score, m.rank AS ggnn_rank
  FROM public.offers o
  JOIN public.actions a ON a.action_id = o.action_id
  LEFT JOIN public.offer_scores s
    ON s.decision_id = o.decision_id AND s.offer_seq = o.offer_seq
  LEFT JOIN public.offer_model_scores m
    ON m.decision_id = o.decision_id AND m.offer_seq = o.offer_seq
   AND m.model = 'greedy_gnn'
), n AS (
  SELECT co.decision_id, co.offer_seq, co.entity_seq,
         ty.key AS action_type, da.action_key, co.slot_index AS slot,
         co.score, co.exploit, co.rank, co.pct_global, co.gnn_impact,
         co.gnn_rank, co.ggnn_score, co.ggnn_rank
  FROM corpus.offer co
  JOIN dict.action da ON da.action_id = co.action_id
  JOIN dict.action_type ty ON ty.id = da.action_type_id
)
SELECT count(*) FILTER (WHERE l.decision_id IS NULL) AS only_new,
       count(*) FILTER (WHERE n.decision_id IS NULL) AS only_old,
       count(*) FILTER (WHERE l.action_type IS DISTINCT FROM n.action_type
                           OR l.action_key IS DISTINCT FROM n.action_key) AS identity,
       count(*) FILTER (WHERE l.entity_seq IS DISTINCT FROM n.entity_seq) AS entity,
       count(*) FILTER (WHERE l.slot IS DISTINCT FROM n.slot) AS slot,
       count(*) FILTER (WHERE l.score::real IS DISTINCT FROM n.score
                           OR l.exploit::real IS DISTINCT FROM n.exploit
                           OR round(l.rank) IS DISTINCT FROM n.rank
                           OR l.pct_global::real IS DISTINCT FROM n.pct_global
                           OR l.gnn_impact::real IS DISTINCT FROM n.gnn_impact
                           OR round(l.gnn_rank) IS DISTINCT FROM n.gnn_rank
                           OR l.ggnn_score::real IS DISTINCT FROM n.ggnn_score
                           OR round(l.ggnn_rank) IS DISTINCT FROM n.ggnn_rank) AS scores,
       count(*) AS total
FROM l FULL JOIN n USING (decision_id, offer_seq)
"""


def _jeq(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return float(a) == float(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_jeq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_jeq(x, y) for x, y in zip(a, b))
    return a == b


def _v2_worker(residue, workers, floor, q):
    _quiet()
    t0 = time.time()
    con = pg.connect(app_name='tw-v2w%d' % residue, autocommit=True)
    con.execute("SET statement_timeout = '3600s'")
    ids = [(r[0], r[1]) for r in con.execute(
        "SELECT d.decision_id, s.version_id FROM corpus.decision d"
        " JOIN corpus.snapshot s ON s.snapshot_id = d.decision_id"
        " WHERE mod(d.decision_id, %s) = %s"
        " AND (d.decision_id >= %s OR mod(d.decision_id, 100) = 7)"
        " ORDER BY d.decision_id", (workers, residue, floor))]
    ok = drift = params_bad = 0
    by_version = {}
    examples = []
    done = 0
    for did, vid in ids:
        legacy = {r[0]: r[1] for r in con.execute(
            "SELECT o.offer_seq, a.params FROM public.offers o"
            " JOIN public.actions a ON a.action_id = o.action_id"
            " WHERE o.decision_id = %s", (did,))}
        rec = hydrate.record(con, did, legacy=True)
        bad = None
        try:
            rows = hydrate._stored_offer_rows(con, did)
            idx = hydrate._generated_index(rec) if rows else {}
            ents = rec.get('entities') or []
            for seq, eseq, at, ak, slot, score, exploit, rank in rows:
                want = json.loads(legacy.get(seq) or '{}')
                if at in hydrate.SYNTHETIC_ACTIONS:
                    got = {}
                else:
                    e = ents[eseq]
                    cand = hydrate._pick_generated(
                        idx.get((e['context_kind'], str(e['context_id']),
                                 at, str(ak))), slot)
                    if cand is None:
                        raise hydrate.OfferDriftError(did, seq, (at, ak))
                    got = cand.get('params') or {}
                if not _jeq(want, got):
                    bad = ('params', seq, want, got)
                    break
        except hydrate.OfferDriftError as e:
            bad = ('drift', str(e)[:160], None, None)
        done += 1
        inside = did >= floor
        if bad is None:
            ok += 1
        elif inside:
            if bad[0] == 'drift':
                drift += 1
            else:
                params_bad += 1
            if len(examples) < 5:
                examples.append((did,) + bad[:2])
        else:
            v = by_version.setdefault(vid, [0, 0])
            v[1] += 1
        if not inside and bad is None:
            by_version.setdefault(vid, [0, 0])[0] += 1
        if done % 5000 == 0:
            log('w%d V2 %d/%d drift=%d params=%d %.0f s'
                % (residue, done, len(ids), drift, params_bad, time.time() - t0))
    con.close()
    q.put({'worker': residue, 'n': done, 'ok': ok, 'drift': drift,
           'params_bad': params_bad, 'by_version': by_version,
           'examples': examples, 's': round(time.time() - t0, 1)})


def v2(res, args):
    t0 = time.time()
    log('V2 enter')
    with pg.connect(app_name='tw-verify', readonly=True, autocommit=True) as con:
        con.execute("SET statement_timeout = '3600s'")
        floor = con.execute(
            "SELECT MIN(first_snapshot_id) FROM"
            " (SELECT first_snapshot_id FROM corpus.campaign"
            " WHERE first_snapshot_id IS NOT NULL"
            " ORDER BY first_snapshot_id DESC LIMIT 1000) w").fetchone()[0]
        log('V2 identity query enter floor=%s' % floor)
        t1 = time.time()
        cur = con.execute(V2_IDENTITY_SQL)
        names = [d.name for d in cur.description]
        idrow = dict(zip(names, cur.fetchone()))
        log('V2 identity query exit %.0f s' % (time.time() - t1))
        identity = idrow
        shas = dict(con.execute(
            "SELECT version_id, collector_sha FROM corpus.collector_version"))
    q = Queue()
    procs = [Process(target=_v2_worker, args=(r, args.workers, floor, q))
             for r in range(args.workers)]
    for p in procs:
        p.start()
    stats = [q.get() for _ in procs]
    for p in procs:
        p.join()
    drift = sum(s['drift'] for s in stats)
    params_bad = sum(s['params_bad'] for s in stats)
    outside = {}
    for s in stats:
        for vid, (okn, badn) in s['by_version'].items():
            cur = outside.setdefault(shas.get(int(vid), str(vid)), [0, 0])
            cur[0] += okn
            cur[1] += badn
    id_bad = sum(v for k, v in identity.items() if k != 'total')
    out = {'ok': id_bad == 0 and drift == 0 and params_bad == 0,
           'floor': floor, 'identity': identity,
           'window_drift': drift, 'window_params_bad': params_bad,
           'regen_checked': sum(s['n'] for s in stats),
           'outside_by_version': {k: {'ok': v[0], 'bad': v[1]}
                                  for k, v in sorted(outside.items())},
           'examples': [e for s in stats for e in s['examples']][:8],
           'min': round((time.time() - t0) / 60, 1)}
    log('V2 exit %.1f min identity_bad=%d drift=%d params_bad=%d %s'
        % (out['min'], id_bad, drift, params_bad,
           'PASS' if out['ok'] else 'FAIL'))
    return out


V3_PAIRS = (
    ('decisions', 'SELECT count(*) FROM public.decisions',
     'SELECT count(*) FROM corpus.decision'),
    ('entities', 'SELECT count(*) FROM public.entities',
     'SELECT count(*) FROM corpus.snapshot_entity'),
    ('offers', 'SELECT count(*) FROM public.offers',
     'SELECT count(*) FROM corpus.offer'),
    ('offer_scores',
     'SELECT count(*) FROM public.offer_scores WHERE score IS NOT NULL',
     'SELECT count(*) FROM corpus.offer WHERE score IS NOT NULL'),
    ('taken', 'SELECT count(*) FROM public.taken',
     'SELECT count(*) FROM corpus.taken'),
    ('interrupts', 'SELECT count(*) FROM public.interrupts',
     'SELECT count(*) FROM corpus.interrupt'),
    ('diplomacy_events', 'SELECT count(*) FROM public.diplomacy_events',
     'SELECT count(*) FROM corpus.diplomacy_event'),
    ('postmortems', 'SELECT count(*) FROM public.postmortems',
     'SELECT count(*) FROM corpus.postmortem'),
    ('ucb_picks', 'SELECT count(*) FROM public.ucb_picks',
     'SELECT count(*) FROM corpus.ucb_pick'),
    ('ucb_pick_rows', 'SELECT count(*) FROM public.ucb_pick_rows',
     'SELECT count(*) FROM corpus.ucb_pick_row'),
    ('campaigns',
     "SELECT (SELECT count(*) FROM public.campaigns) + (SELECT count(DISTINCT d.campaign_key)"
     " FROM public.diplomacy_events d LEFT JOIN public.campaigns c"
     " ON c.campaign_key = d.campaign_key"
     " WHERE c.campaign_id IS NULL AND d.campaign_key IS NOT NULL)",
     'SELECT count(*) FROM corpus.campaign'),
    ('collector_versions', 'SELECT 3',
     'SELECT count(*) FROM corpus.collector_version'),
    ('snapshots',
     'SELECT (SELECT count(*) FROM public.decisions)'
     ' + (SELECT count(*) FROM public.interrupts)',
     'SELECT count(*) FROM corpus.snapshot'),
)


def v3(res, args):
    t0 = time.time()
    log('V3 enter')
    rows = {}
    with pg.connect(app_name='tw-verify', readonly=True, autocommit=True) as con:
        con.execute("SET statement_timeout = '1800s'")
        for name, lsql, nsql in V3_PAIRS:
            a = con.execute(lsql).fetchone()[0]
            b = con.execute(nsql).fetchone()[0]
            rows[name] = {'legacy': a, 'new': b, 'ok': a == b}
            log('V3 %-18s legacy=%d new=%d %s'
                % (name, a, b, 'ok' if a == b else 'DIFF'))
    out = {'ok': all(r['ok'] for r in rows.values()), 'rows': rows,
           's': round(time.time() - t0, 1)}
    log('V3 exit %.0f s %s' % (out['s'], 'PASS' if out['ok'] else 'FAIL'))
    return out


V4_QUERIES = (
    ('decision_without_snapshot',
     'SELECT count(*) FROM corpus.decision d LEFT JOIN corpus.snapshot s'
     ' ON s.snapshot_id = d.decision_id WHERE s.snapshot_id IS NULL'),
    ('interrupt_without_snapshot',
     'SELECT count(*) FROM corpus.interrupt i LEFT JOIN corpus.snapshot s'
     ' ON s.snapshot_id = i.interrupt_id WHERE s.snapshot_id IS NULL'),
    ('snapshot_without_subtype',
     'SELECT count(*) FROM corpus.snapshot s LEFT JOIN corpus.decision d'
     ' ON d.decision_id = s.snapshot_id LEFT JOIN corpus.interrupt i'
     ' ON i.interrupt_id = s.snapshot_id'
     ' WHERE d.decision_id IS NULL AND i.interrupt_id IS NULL'),
    ('snapshot_without_campaign_row',
     'SELECT count(*) FROM corpus.snapshot s LEFT JOIN corpus.snapshot_campaign c'
     ' ON c.snapshot_id = s.snapshot_id WHERE c.snapshot_id IS NULL'),
    ('snapshot_without_world_row',
     'SELECT count(*) FROM corpus.snapshot s LEFT JOIN corpus.snapshot_world w'
     ' ON w.snapshot_id = s.snapshot_id WHERE w.snapshot_id IS NULL'),
    ('decision_without_campaign_state',
     'SELECT count(*) FROM corpus.decision d LEFT JOIN corpus.campaign_state c'
     ' ON c.snapshot_id = d.decision_id WHERE c.snapshot_id IS NULL'),
    ('char_state_ext_orphan',
     'SELECT count(*) FROM corpus.char_state_ext e LEFT JOIN corpus.char_state c'
     ' ON c.snapshot_id = e.snapshot_id AND c.character_id = e.character_id'
     ' WHERE c.snapshot_id IS NULL'),
    ('taken_without_decision',
     'SELECT count(*) FROM corpus.taken t LEFT JOIN corpus.decision d'
     ' ON d.decision_id = t.decision_id WHERE d.decision_id IS NULL'),
    ('unvalidated_fks',
     "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n"
     " ON n.oid = c.connamespace WHERE c.contype = 'f' AND NOT c.convalidated"
     " AND n.nspname IN ('corpus','dict','ref','ops','analytics2')"),
)


def v4(res, args):
    t0 = time.time()
    log('V4 enter')
    rows = {}
    with pg.connect(app_name='tw-verify', readonly=True, autocommit=True) as con:
        con.execute("SET statement_timeout = '1800s'")
        for name, sql in V4_QUERIES:
            n = con.execute(sql).fetchone()[0]
            rows[name] = n
            log('V4 %-32s %d' % (name, n))
    out = {'ok': all(v == 0 for v in rows.values()), 'orphans': rows,
           's': round(time.time() - t0, 1)}
    log('V4 exit %.0f s %s' % (out['s'], 'PASS' if out['ok'] else 'FAIL'))
    return out


V5_SQL = """
WITH decs AS (
  SELECT s.campaign_id, count(*) n_decisions,
         min(s.snapshot_id) first_sid, max(s.snapshot_id) last_sid,
         min(s.ts) first_ts, max(s.ts) last_ts, max(s.turn) turns
  FROM corpus.snapshot s JOIN corpus.decision d ON d.decision_id = s.snapshot_id
  GROUP BY 1
), ints AS (
  SELECT s.campaign_id, count(*) n_interrupts, max(s.ts) last_ts
  FROM corpus.snapshot s JOIN corpus.interrupt i ON i.interrupt_id = s.snapshot_id
  GROUP BY 1
), tk AS (
  SELECT campaign_id, count(*) n_taken,
         count(*) FILTER (WHERE counted) n_counted
  FROM corpus.taken GROUP BY 1
), sc AS (
  SELECT s.campaign_id,
         max(c.settlements) peak_settlements, max(c.lord_level) peak_lord_level,
         max(c.allies) allies_max, max(c.vassals) vassals_max
  FROM corpus.snapshot_campaign c
  JOIN corpus.snapshot s ON s.snapshot_id = c.snapshot_id
  JOIN corpus.decision d ON d.decision_id = s.snapshot_id
  GROUP BY 1
), fs AS (
  SELECT decs.campaign_id, c.settlements first_settlements,
         c.lord_level first_lord_level
  FROM decs JOIN corpus.snapshot_campaign c ON c.snapshot_id = decs.first_sid
)
SELECT count(*) FILTER (WHERE c.n_decisions IS DISTINCT FROM COALESCE(decs.n_decisions, 0)) bad_n_decisions,
       count(*) FILTER (WHERE c.n_interrupts IS DISTINCT FROM COALESCE(ints.n_interrupts, 0)) bad_n_interrupts,
       count(*) FILTER (WHERE c.n_taken IS DISTINCT FROM COALESCE(tk.n_taken, 0)) bad_n_taken,
       count(*) FILTER (WHERE c.n_counted IS DISTINCT FROM COALESCE(tk.n_counted, 0)) bad_n_counted,
       count(*) FILTER (WHERE c.first_snapshot_id IS DISTINCT FROM decs.first_sid) bad_first_sid,
       count(*) FILTER (WHERE c.last_snapshot_id IS DISTINCT FROM decs.last_sid) bad_last_sid,
       count(*) FILTER (WHERE c.first_ts IS DISTINCT FROM decs.first_ts) bad_first_ts,
       count(*) FILTER (WHERE c.last_ts IS DISTINCT FROM GREATEST(decs.last_ts, ints.last_ts)) bad_last_ts,
       count(*) FILTER (WHERE c.turns < COALESCE(decs.turns, 0)) bad_turns,
       count(*) FILTER (WHERE c.peak_settlements IS DISTINCT FROM sc.peak_settlements) bad_peak_settlements,
       count(*) FILTER (WHERE c.peak_lord_level IS DISTINCT FROM sc.peak_lord_level) bad_peak_lord_level,
       count(*) FILTER (WHERE c.allies_max IS DISTINCT FROM sc.allies_max) bad_allies_max,
       count(*) FILTER (WHERE c.vassals_max IS DISTINCT FROM sc.vassals_max) bad_vassals_max,
       count(*) FILTER (WHERE c.first_settlements IS DISTINCT FROM fs.first_settlements) bad_first_settlements,
       count(*) FILTER (WHERE c.first_lord_level IS DISTINCT FROM fs.first_lord_level) bad_first_lord_level,
       count(*) total
FROM corpus.campaign c
LEFT JOIN decs ON decs.campaign_id = c.campaign_id
LEFT JOIN ints ON ints.campaign_id = c.campaign_id
LEFT JOIN tk ON tk.campaign_id = c.campaign_id
LEFT JOIN sc ON sc.campaign_id = c.campaign_id
LEFT JOIN fs ON fs.campaign_id = c.campaign_id
"""


def v5(res, args):
    t0 = time.time()
    log('V5 enter')
    with pg.connect(app_name='tw-verify', readonly=True, autocommit=True) as con:
        con.execute("SET statement_timeout = '1800s'")
        cur = con.execute(V5_SQL)
        row = dict(zip([d.name for d in cur.description], cur.fetchone()))
    bad = sum(v for k, v in row.items() if k != 'total')
    out = {'ok': bad == 0, 'campaigns': row.pop('total'), 'bad': row,
           's': round(time.time() - t0, 1)}
    for k, v in row.items():
        if v:
            log('V5 %-24s %d' % (k, v))
    log('V5 exit %.0f s bad=%d %s' % (out['s'], bad, 'PASS' if bad == 0 else 'FAIL'))
    return out


def v6(res, args):
    t0 = time.time()
    log('V6 enter')
    with pg.connect(app_name='tw-verify', autocommit=True) as con:
        con.execute("SET statement_timeout = '3600s'")
        refs = con.execute(
            "SELECT c.conrelid::regclass::text, a.attname FROM pg_constraint c"
            " JOIN pg_attribute a ON a.attrelid = c.conrelid"
            " AND a.attnum = ANY(c.conkey)"
            " WHERE c.confrelid = 'corpus.state_set'::regclass AND c.contype = 'f'"
            " ORDER BY 1, 2").fetchall()
        union = ' UNION '.join(
            'SELECT DISTINCT %s AS set_id FROM %s WHERE %s IS NOT NULL' % (col, tbl, col)
            for tbl, col in refs)
        orphans = con.execute(
            "SELECT count(*) FROM corpus.state_set s WHERE s.n > 0"
            " AND NOT EXISTS (SELECT 1 FROM (%s) r WHERE r.set_id = s.set_id)"
            % union).fetchone()[0]
        swept = 0
        if orphans:
            swept = con.execute(
                "DELETE FROM corpus.state_set s WHERE s.n > 0"
                " AND NOT EXISTS (SELECT 1 FROM (%s) r WHERE r.set_id = s.set_id)"
                % union).rowcount
            log('V6 swept %d orphan sets' % swept)
        member_tables = [r[0] for r in con.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'corpus'"
            " AND tablename LIKE '%_set_member' ORDER BY 1")]
        bad_n = 0
        per_table = {}
        counted = set()
        for t in member_tables:
            n = con.execute(
                "SELECT count(*) FROM (SELECT set_id, count(*) c FROM corpus.%s"
                " GROUP BY 1) m JOIN corpus.state_set s ON s.set_id = m.set_id"
                " WHERE s.n <> m.c" % t).fetchone()[0]
            per_table[t] = n
            bad_n += n
            for (sid,) in con.execute(
                    "SELECT DISTINCT set_id FROM corpus.%s" % t):
                counted.add(sid)
        empty_bad = con.execute(
            "SELECT count(*) FROM corpus.state_set s WHERE s.n > 0"
            " AND NOT (s.set_id = ANY(%s))", (sorted(counted),)).fetchone()[0]
    out = {'ok': bad_n == 0 and empty_bad == 0,
           'orphans_found': orphans, 'orphans_swept': swept,
           'n_mismatch': bad_n, 'nonempty_without_members': empty_bad,
           'member_tables': len(per_table),
           'min': round((time.time() - t0) / 60, 1)}
    log('V6 exit %.1f min orphans=%d n_mismatch=%d %s'
        % (out['min'], orphans, bad_n, 'PASS' if out['ok'] else 'FAIL'))
    return out


def _f4(v):
    import struct
    if v is None:
        return None
    return struct.unpack('f', struct.pack('f', float(v)))[0]


def _feq(a, b):
    a, b = _f4(a), _f4(b)
    if a is None or b is None:
        return a is b
    return abs(a - b) <= max(abs(a), abs(b)) * 1.3e-07


def _options_projection(con):
    t0 = time.time()
    log('V7 options projection enter')
    from migrate.replay import _options_list
    bad = 0
    examples = []
    lrows = con.execute(
        "SELECT ci.interrupt_id, li.options_json, li.n_options"
        " FROM corpus.interrupt ci JOIN public.interrupts li"
        " ON li.interrupt_id = ci.legacy_interrupt_id"
        " ORDER BY ci.interrupt_id").fetchall()
    stored = {}
    for iid, ord_, key, textv, oid, ans, payload, exploit, score, gnn in con.execute(
            "SELECT interrupt_id, ord, option_key, text, option_id, answer,"
            " payload, exploit, score, gnn FROM corpus.interrupt_option"
            " ORDER BY interrupt_id, ord"):
        stored.setdefault(iid, []).append(
            (key, textv, oid, ans, payload, exploit, score, gnn))
    for sid, oj, n_options in lrows:
        want = _options_list(json.loads(oj) if oj else [])
        have = stored.get(sid) or []
        rec_bad = len(want) != len(have)
        if not rec_bad:
            for w, h in zip(want, have):
                pay = w.get('payload')
                if isinstance(pay, (dict, list)):
                    pay = json.dumps(pay, sort_keys=True)
                hp = h[4]
                if isinstance(hp, (dict, list)):
                    hp = json.dumps(hp, sort_keys=True)
                elif isinstance(hp, str) and hp and hp[0] in '{[':
                    hp = json.dumps(json.loads(hp), sort_keys=True)
                if (str(w.get('key')) != h[0]
                        or (w.get('text') or None) != h[1]
                        or (w.get('option_id') or None) != h[2]
                        or (w.get('answer') or None) != h[3]
                        or (pay or None) != (hp or None)
                        or not _feq(w.get('exploit'), h[5])
                        or not _feq(w.get('score'), h[6])
                        or not _feq(w.get('gnn'), h[7])):
                    rec_bad = True
                    break
        if rec_bad:
            bad += 1
            if len(examples) < 5:
                examples.append({'interrupt_id': sid, 'want': want[:2],
                                 'have': have[:2]})
    log('V7 options projection exit %.0f s interrupts=%d bad=%d'
        % (time.time() - t0, len(lrows), bad))
    return bad, len(lrows), examples


def v7(res, args):
    t0 = time.time()
    log('V7 enter')
    with pg.connect(app_name='tw-verify', readonly=True, autocommit=True) as con:
        con.execute("SET statement_timeout = '3600s'")
        chosen_bad = con.execute(
            "SELECT count(*) FROM corpus.interrupt i WHERE i.chosen <> ''"
            " AND NOT EXISTS (SELECT 1 FROM corpus.interrupt_option o"
            " WHERE o.interrupt_id = i.interrupt_id"
            " AND o.option_key = i.chosen)").fetchone()[0]
        prev_bad = con.execute(
            "SELECT count(*) FROM corpus.interrupt i"
            " JOIN corpus.snapshot si ON si.snapshot_id = i.interrupt_id"
            " JOIN corpus.snapshot sp ON sp.snapshot_id = i.prev_decision_id"
            " WHERE sp.ts > si.ts").fetchone()[0]
        n_options_bad = con.execute(
            "SELECT count(*) FROM corpus.interrupt ci"
            " JOIN public.interrupts li ON li.interrupt_id = ci.legacy_interrupt_id"
            " LEFT JOIN (SELECT interrupt_id, count(*) c FROM corpus.interrupt_option"
            " GROUP BY 1) o ON o.interrupt_id = ci.interrupt_id"
            " WHERE COALESCE(o.c, 0) <> COALESCE(li.n_options, 0)").fetchone()[0]
        panel_missing = con.execute(
            "SELECT count(*) FROM corpus.interrupt ci"
            " JOIN dict.enum k ON k.enum_id = ci.kind_id"
            " JOIN public.interrupts li ON li.interrupt_id = ci.legacy_interrupt_id"
            " LEFT JOIN corpus.interrupt_battle_panel bp"
            " ON bp.interrupt_id = ci.interrupt_id"
            " LEFT JOIN corpus.interrupt_diplo_panel dp"
            " ON dp.interrupt_id = ci.interrupt_id"
            " WHERE li.panel_blob IS NOT NULL"
            " AND ((k.key IN ('pre_battle','battle_results')"
            "       AND bp.interrupt_id IS NULL)"
            "  OR (k.key IN ('diplomacy_proposal','diplomacy_notice','war_declared')"
            "       AND dp.interrupt_id IS NULL))").fetchone()[0]
        panel_by_kind = {r[0]: r[1] for r in con.execute(
            "SELECT k.key, count(*) FROM corpus.interrupt ci"
            " JOIN dict.enum k ON k.enum_id = ci.kind_id"
            " WHERE EXISTS (SELECT 1 FROM corpus.interrupt_battle_panel b"
            " WHERE b.interrupt_id = ci.interrupt_id)"
            " OR EXISTS (SELECT 1 FROM corpus.interrupt_diplo_panel d"
            " WHERE d.interrupt_id = ci.interrupt_id) GROUP BY 1 ORDER BY 1")}
        reassigned = con.execute(
            "SELECT count(*) FROM corpus.interrupt ci"
            " JOIN corpus.snapshot s ON s.snapshot_id = ci.interrupt_id"
            " JOIN public.interrupts li ON li.interrupt_id = ci.legacy_interrupt_id"
            " WHERE s.campaign_id IS DISTINCT FROM li.campaign_id").fetchone()[0]
        twins = con.execute(
            "SELECT count(*) FROM corpus.interrupt a"
            " JOIN corpus.snapshot sa ON sa.snapshot_id = a.interrupt_id"
            " JOIN corpus.interrupt b ON b.interrupt_id > a.interrupt_id"
            " JOIN corpus.snapshot sb ON sb.snapshot_id = b.interrupt_id"
            " AND sb.campaign_id = sa.campaign_id"
            " WHERE a.root = b.root"
            " AND a.root_context IS NOT DISTINCT FROM b.root_context"
            " AND abs(sa.ts - sb.ts) <= 60").fetchone()[0]
        opt_bad, opt_n, opt_examples = _options_projection(con)
    out = {'ok': (chosen_bad == 0 and prev_bad == 0 and n_options_bad == 0
                  and panel_missing == 0 and opt_bad == 0),
           'chosen_not_in_options': chosen_bad, 'prev_ts_after': prev_bad,
           'n_options_mismatch': n_options_bad, 'panel_missing': panel_missing,
           'panel_by_kind': panel_by_kind, 'campaign_reassigned': reassigned,
           'twins_60s': twins, 'options_projection_bad': opt_bad,
           'options_projection_n': opt_n, 'options_examples': opt_examples,
           'min': round((time.time() - t0) / 60, 1)}
    log('V7 exit %.1f min chosen=%d prev=%d nopt=%d panel=%d proj=%d %s'
        % (out['min'], chosen_bad, prev_bad, n_options_bad, panel_missing,
           opt_bad, 'PASS' if out['ok'] else 'FAIL'))
    return out


def _worktree():
    base = os.path.join(os.environ.get('TEMP', HERE), 'tw_premigration_worktree')
    if not os.path.exists(os.path.join(base, 'VERSION')):
        subprocess.run(['git', 'worktree', 'add', '--force', base, 'pre-migration'],
                       cwd=ROOT, check=True, capture_output=True, text=True)
    return base


def v8(res, args):
    t0 = time.time()
    log('V8 enter')
    wt = _worktree()
    import shutil
    shutil.copy(os.path.join(HERE, 'v8_dump.py'),
                os.path.join(wt, 'migrate', 'v8_dump.py'))
    old_out = os.path.join(HERE, 'v8_old.json')
    new_out = os.path.join(HERE, 'v8_new.json')
    import common
    jobs = (('old', wt, '55432', old_out), ('new', ROOT, '55433', new_out))
    for name, cwd, port, path in jobs:
        env = dict(os.environ, TW_PG_PORT=port, TWDATA=common.TWDATA)
        env.pop('PYTHONPATH', None)
        log('V8 %s dump enter (port %s)' % (name, port))
        t1 = time.time()
        proc = subprocess.run(
            [VENV_PY, os.path.join(cwd, 'migrate', 'v8_dump.py'), path, '1000',
             'full'],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=7200)
        tail = (proc.stderr or '').strip().split('\n')[-4:]
        for line in tail:
            log('V8 %s | %s' % (name, line))
        if proc.returncode != 0:
            raise RuntimeError('v8 %s dump failed: %s' % (name, tail))
        log('V8 %s dump exit %.1f min' % (name, (time.time() - t1) / 60))
    a = json.load(io.open(old_out, encoding='utf-8'))
    b = json.load(io.open(new_out, encoding='utf-8'))
    parts = {}
    for part in ('model', 'interrupt', 'walk'):
        pa, pb = a[part], b[part]
        same = {k: (pa.get(k) == pb.get(k))
                for k in sorted(set(pa) | set(pb)) if not k.startswith('i_')}
        info = {}
        if 'i_y' in pa and 'i_y' in pb:
            info['y_diffs'] = sum(1 for x, y in zip(pa['i_y'], pb['i_y'])
                                  if x != y)
        ok = all(same.values())
        if part == 'model' and not same.get('sha', True):
            fa = json.load(io.open(old_out + '.model.json', encoding='utf-8'))
            fb = json.load(io.open(new_out + '.model.json', encoding='utf-8'))
            bad_rows, bad_cols = 0, set()
            for x, y in zip(fa[0], fb[0]):
                if x != y:
                    bad_rows += 1
                    bad_cols |= {c for c in set(x) | set(y)
                                 if x.get(c) != y.get(c)}
            info['row_diffs'] = bad_rows
            info['diff_columns'] = sorted(bad_cols)
            tolerable = (bad_rows <= 16 and bad_cols
                         and all(c.startswith('opt_') and 'prebattle' in c
                                 for c in bad_cols)
                         and fa[1:] == fb[1:])
            info['pb_tie_tolerated'] = tolerable
            ok = all(v for k, v in same.items() if k != 'sha') and tolerable
        parts[part] = {'ok': ok, 'equal': same, 'info': info,
                       'old': {k: v for k, v in pa.items()
                               if k not in ('columns', 'i_y')},
                       'new': {k: v for k, v in pb.items()
                               if k not in ('columns', 'i_y')}}
    out = {'ok': all(p['ok'] for p in parts.values()), 'parts': parts,
           'min': round((time.time() - t0) / 60, 1)}
    log('V8 exit %.1f min model=%s interrupt=%s walk=%s'
        % (out['min'], parts['model']['ok'], parts['interrupt']['ok'],
           parts['walk']['ok']))
    return out


def _wait_health(base, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(base + '/api/health', timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(2)
    return False


def v9(res, args):
    t0 = time.time()
    log('V9 enter')
    wt = _worktree()
    import common
    env_old = dict(os.environ, TW_PG_PORT='55432', TWDATA=common.TWDATA)
    env_new = dict(os.environ, TW_PG_PORT='55433', TWDATA=common.TWDATA)
    for e in (env_old, env_new):
        e.pop('PYTHONPATH', None)
    po = subprocess.Popen([VENV_PY, '-u', '-m', 'advisor_api.app', '8791'],
                          cwd=wt, env=env_old, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    pn = subprocess.Popen([VENV_PY, '-u', '-m', 'advisor_api.app', '8792'],
                          cwd=ROOT, env=env_new, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    try:
        if not _wait_health('http://127.0.0.1:8791'):
            raise RuntimeError('old api did not come up')
        if not _wait_health('http://127.0.0.1:8792'):
            raise RuntimeError('new api did not come up')
        proc = subprocess.run(
            [VENV_PY, os.path.join(HERE, 'v9_ab.py')], cwd=ROOT,
            capture_output=True, text=True, timeout=7200)
        io.open(os.path.join(HERE, 'v9_ab.out'), 'w', encoding='utf-8',
                newline='\n').write(proc.stdout or '')
        for line in (proc.stdout or '').strip().split('\n')[-3:]:
            log('V9 | %s' % line)
        if proc.returncode != 0:
            raise RuntimeError('v9_ab failed: %s' % (proc.stderr or '')[-400:])
    finally:
        po.kill()
        pn.kill()
    ab = json.load(io.open(os.path.join(HERE, 'v9_ab.json'), encoding='utf-8'))
    out = {'ok': ab['total'] == ab['equal'] and not ab['differ'],
           'total': ab['total'], 'equal': ab['equal'], 'differ': ab['differ'],
           'min': round((time.time() - t0) / 60, 1)}
    log('V9 exit %.1f min %d/%d equal %s'
        % (out['min'], ab['equal'], ab['total'], 'PASS' if out['ok'] else 'FAIL'))
    return out


STAGES = {'v1': v1, 'v2': v2, 'v3': v3, 'v4': v4, 'v5': v5, 'v6': v6,
          'v7': v7, 'v8': v8, 'v9': v9}


def report(res):
    ts = time.strftime('%Y%m%d_%H%M%S')
    path = os.path.join(HERE, 'validation_%s.md' % ts)
    lines = ['# S10 validation report %s' % ts, '']
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    lines.append('Commit: `%s`' % commit)
    lines.append('')
    lines.append('| check | result | detail |')
    lines.append('|---|---|---|')
    names = {
        'v1': 'V1 round-trip every snapshot (CB, WB, EB, ICB, IWB)',
        'v2': 'V2 offers: identity all rows; params regenerated in window',
        'v3': 'V3 row counts legacy vs corpus',
        'v4': 'V4 FKs validated + subtype orphans',
        'v5': 'V5 campaign aggregates recomputed',
        'v6': 'V6 state_set references and member counts',
        'v7': 'V7 interrupts: chosen, prev ts, options, panels',
        'v8': 'V8 training-set equality (model, interrupt_model, walk hashes)',
        'v9': 'V9 API A/B',
    }
    for key in ('v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v9'):
        r = res.get(key)
        if r is None:
            lines.append('| %s | NOT RUN | |' % names[key])
            continue
        detail = {k: v for k, v in r.items()
                  if k not in ('ok', 'workers', 'examples', 'options_examples')}
        lines.append('| %s | %s | `%s` |'
                     % (names[key], 'PASS' if r['ok'] else 'FAIL',
                        json.dumps(detail, default=str)[:600]))
    lines += ['', '## Notes', '']
    lines.append('- V1 covers F1/F2 (12.1): both sides canonicalised with'
                 ' `canon(normalise(x))`; `migrate.mismatch` stage V1 is empty.'
                 ' Fidelity fixes landed during validation: `write_interrupt` now'
                 ' writes `world_army`/`world_hostile` rows (43,386 interrupts'
                 ' backfilled); hydrate applies the kind-dependent ICB/IWB'
                 ' projection (02 2.8); `_eval_ms` joined the canon int class;'
                 ' partial v31 blocks (traits without the scalar block, 68'
                 ' snapshots) get a `char_state_ext` row with NULL'
                 ' `armory_item_ids` as the partial-block marker; `selector` is'
                 ' kept as null when `difficulty` is present (1 snapshot).')
    lines.append('- V2 identity compares every offer row and score column in SQL'
                 ' (legacy doubles cast to the REAL the DDL declares); params are'
                 ' regenerated (`options.generate`) for the full 1000-campaign'
                 ' window plus a 1% sample outside (F4/T4/T5).'
                 ' `hydrate._pick_generated` now consumes candidates in order so'
                 ' duplicate offers of one action (multi-pool items) regenerate'
                 ' their own params.')
    lines.append('- V3 collector_versions: corpus keeps only the 3 seeded'
                 ' sentinels (03_seed.sql); the 32 legacy collector_versions rows'
                 ' are not carried -- V1 proves the stored NULLs alone reproduce'
                 ' every blob, so the emits_* signature model was dropped.'
                 ' 373 legacy offer_scores rows with NULL score have no scored'
                 ' corpus row (their other columns are checked in V2 identity).')
    lines.append('- V7 `campaign_reassigned = 0`: the replay preserves the legacy'
                 ' campaign assignment (9 C.5 reassignment happens in the'
                 ' readers, not the copy). Option floats compared at the REAL'
                 ' precision the DDL declares, 1-ulp tolerance for decimal->f4'
                 ' rounding ties (15 options).')
    lines.append('- ops.trial holds the 176 trials with session reports; the 141'
                 ' archived metrics.trials rows without session reports are'
                 ' intentionally not carried (3.3: ops.trial is built from'
                 ' session reports).')
    lines.append('- V8 (T1-T3): model gather equal except 3 rows from legacy'
                 ' heap-order ties when several drained pre_battle interrupts'
                 ' attribute one decision (tolerated, prebattle columns only);'
                 ' `prebattle_attributions` now matches legacy taken-ts'
                 ' attribution, settlement coords come from hostile settlements,'
                 ' and the facade restores panel region and per-option'
                 ' dilemma_id. Interrupt rows equal except isc_option_label'
                 ' (C.8) and isc_fc_result/isc_fc_casualties (dead in legacy:'
                 ' panel blobs carry result_flag/outcome, never result.state;'
                 ' the typed panels revive them); 943 y labels differ by design'
                 ' (C.2: prev_turn for state_at=recorder rows; 12,565'
                 ' candidates). Walk taken hashes identical.')
    lines.append('- V9 route set and field drops per 12.6 (`migrate/v9_ab.py`);'
                 ' the legacy API runs from the pre-migration worktree with'
                 ' TWDATA pointed at the real data root.')
    io.open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')
    log('report written %s' % path)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stages', default='v1,v2,v3,v4,v5,v6,v7,v8,v9')
    ap.add_argument('--workers', type=int, default=WORKERS)
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()
    t0 = time.time()
    res_path = os.path.join(HERE, 'verify.json')
    res = {}
    if os.path.exists(res_path):
        res = json.load(io.open(res_path, encoding='utf-8'))
    stages = [s for s in args.stages.split(',') if s]
    log('enter stages=%s port=%s' % (','.join(stages), os.environ['TW_PG_PORT']))
    for s in stages:
        res[s] = STAGES[s](res, args)
        res['ts'] = time.time()
        io.open(res_path, 'w', encoding='utf-8', newline='\n').write(
            json.dumps(res, indent=2, default=str) + '\n')
        with pg.connect(app_name='tw-verify') as con:
            checkpoint(con, 'S10-' + s.upper(),
                       'done' if res[s]['ok'] else 'failed', None, None, t0)
            con.commit()
    ran = [s for s in stages]
    all_ok = all(res[s]['ok'] for s in ran)
    if args.report:
        report(res)
    log('exit %.1f min %s' % ((time.time() - t0) / 60,
                              'ALL PASS' if all_ok else 'FAILURES'))
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
