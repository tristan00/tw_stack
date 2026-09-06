import argparse
import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault('TW_PG_PORT', '55433')

from decisions import canon, hydrate, pg

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
     " AND n.nspname IN ('corpus','dict','ref','ops','analytics')"),
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
        panel_missing = con.execute(
            "SELECT count(*) FROM corpus.interrupt ci"
            " JOIN dict.enum k ON k.enum_id = ci.kind_id"
            " JOIN dict.enum st ON st.enum_id = ci.state_at_id"
            " LEFT JOIN corpus.interrupt_battle_panel bp"
            " ON bp.interrupt_id = ci.interrupt_id"
            " LEFT JOIN corpus.interrupt_diplo_panel dp"
            " ON dp.interrupt_id = ci.interrupt_id"
            " WHERE st.key = 'panel'"
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
        twins = con.execute(
            "SELECT count(*) FROM corpus.interrupt a"
            " JOIN corpus.snapshot sa ON sa.snapshot_id = a.interrupt_id"
            " JOIN corpus.interrupt b ON b.interrupt_id > a.interrupt_id"
            " JOIN corpus.snapshot sb ON sb.snapshot_id = b.interrupt_id"
            " AND sb.campaign_id = sa.campaign_id"
            " WHERE a.root = b.root"
            " AND a.root_context IS NOT DISTINCT FROM b.root_context"
            " AND abs(sa.ts - sb.ts) <= 60").fetchone()[0]
    out = {'ok': chosen_bad == 0 and prev_bad == 0 and panel_missing == 0,
           'chosen_not_in_options': chosen_bad, 'prev_ts_after': prev_bad,
           'panel_missing': panel_missing, 'panel_by_kind': panel_by_kind,
           'twins_60s': twins,
           'min': round((time.time() - t0) / 60, 1)}
    log('V7 exit %.1f min chosen=%d prev=%d panel=%d %s'
        % (out['min'], chosen_bad, prev_bad, panel_missing,
           'PASS' if out['ok'] else 'FAIL'))
    return out


STAGES = {'v4': v4, 'v5': v5, 'v6': v6, 'v7': v7}



TABLE_TARGET = 95


def counts():
    t0 = time.time()
    log('counts enter')
    out = {}
    with pg.connect(app_name='tw-verify', readonly=True, autocommit=True) as con:
        out['tables'] = con.execute(
            "SELECT count(*) FROM pg_class k JOIN pg_namespace n"
            " ON n.oid = k.relnamespace WHERE k.relkind = 'r'"
            " AND n.nspname IN ('corpus','dict','ops','analytics')").fetchone()[0]
        out['schemas'] = [r[0] for r in con.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%'"
            " AND nspname <> 'information_schema' ORDER BY 1")]
        out['db_gb'] = round(con.execute(
            "SELECT pg_database_size(current_database())").fetchone()[0] / 2 ** 30, 2)
    out['table_target'] = TABLE_TARGET
    out['tables_ok'] = out['tables'] <= TABLE_TARGET
    out['json_decode_sites'] = _scan(r'json\.loads\(')
    out['runtime_metrics'] = 'connections, statements per decision and DDL per hour need a live run'
    out['s'] = round(time.time() - t0, 2)
    log('counts exit %.2f s tables=%d' % (out['s'], out['tables']))
    return out


READ_PATHS = ('advisor_api/queries.py', 'advisor_api/app.py', 'advisor_api/db.py',
              'advisor/memory.py', 'decisions/hydrate.py', 'decisions/store.py')


def _scan(pattern):
    rx = re.compile(pattern)
    hits = {}
    for rel in READ_PATHS:
        fp = os.path.join(ROOT, rel)
        if not os.path.exists(fp):
            continue
        n = len(rx.findall(io.open(fp, encoding='utf-8', errors='replace').read()))
        if n:
            hits[rel] = n
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stages', default='v4,v5,v6,v7')
    ap.add_argument('--workers', type=int, default=WORKERS)
    ap.add_argument('--counts', action='store_true')
    args = ap.parse_args()
    t0 = time.time()
    log('enter stages=%s counts=%s' % (args.stages, args.counts))
    if args.counts:
        out = counts()
        print(json.dumps(out, indent=2, default=str))
        log('exit %.1f s' % (time.time() - t0))
        return 0 if out['tables_ok'] else 1
    res = {}
    for s in [s for s in args.stages.split(',') if s]:
        res[s] = STAGES[s](res, args)
    all_ok = all(r['ok'] for r in res.values())
    for k in sorted(res):
        print('%-4s %s' % (k, 'PASS' if res[k]['ok'] else 'FAIL'))
    log('exit %.1f min %s' % ((time.time() - t0) / 60,
                              'ALL PASS' if all_ok else 'FAILURES'))
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
