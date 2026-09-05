from __future__ import annotations

import json
import sys
import time

from decisions import canon, dicts, pg, rowmap, schema_map, sets

MAX_ENTITIES = 64


def _num(v):
    try:
        return int(float(str(v).replace(',', '').strip()))
    except (TypeError, ValueError):
        return None


def log(msg):
    sys.stderr.write('%.3f  store.%s\n' % (time.time(), msg))


def timed(name):
    def wrap(fn):
        def call(self, *a, **kw):
            t0 = time.time()
            log('%s enter' % name)
            try:
                return fn(self, *a, **kw)
            finally:
                log('%s exit %.1f ms' % (name, (time.time() - t0) * 1000))
        return call
    return wrap


class Store:

    def __init__(self, app_name='tw-recorder', collector_sha='legacy:meta1:pq1'):
        self.conn = pg.Conn(app_name, search_path=pg.CORPUS_PATH)
        self.dicts = dicts.Dicts(self.conn)
        self.setw = sets.SetWriter(self.conn, self.dicts)
        self.campaigns = {}
        self.characters = {}
        self.collector_sha = collector_sha
        self.version_id = None
        self.kind_ids = self.dicts.resolve_enum(
            'snapshot_kind', ['decision', 'interrupt'])
        self.entity_kind_ids = self.dicts.resolve_enum(
            'entity_kind', ['campaign', 'lord', 'hero', 'province'])

    def close(self):
        self.conn.close()

    def campaign_key(self, faction, uuid):
        return str(uuid) if uuid else str(faction or '')

    def _version(self):
        if self.version_id is None:
            row = self.conn.execute(
                "SELECT version_id FROM corpus.collector_version WHERE collector_sha = %s",
                (self.collector_sha,)).fetchone()
            if row is None:
                raise KeyError('collector_version %r not seeded' % self.collector_sha)
            self.version_id = row[0]
        return self.version_id

    def _campaign(self, camp):
        key = self.campaign_key(camp.get('faction'), camp.get('campaign_uuid'))
        if key in self.campaigns:
            return self.campaigns[key]
        ids = self.dicts.resolve('faction', [camp.get('faction')])
        maps = self.dicts.resolve('campaign_map', [camp.get('campaign_map')])
        sels = self.dicts.resolve('selector', [camp.get('selector')])
        row = self.conn.execute(
            "INSERT INTO corpus.campaign (campaign_key, faction_id, campaign_map_id,"
            " presave_radius, selector_id, difficulty, leader)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (campaign_key) DO NOTHING"
            " RETURNING campaign_id",
            (key, ids.get(camp.get('faction')), maps.get(camp.get('campaign_map')),
             camp.get('presave_radius'), sels.get(camp.get('selector')),
             camp.get('difficulty'), camp.get('leader'))).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT campaign_id FROM corpus.campaign WHERE campaign_key = %s",
                (key,)).fetchone()
        self.campaigns[key] = row[0]
        return row[0]

    def _character(self, campaign_id, cqi, snapshot_id):
        hit = self.characters.get((campaign_id, cqi))
        if hit is not None:
            self.conn.execute(
                "UPDATE corpus.character SET last_snapshot_id = GREATEST(last_snapshot_id, %s)"
                " WHERE character_id = %s", (snapshot_id, hit))
            return hit
        row = self.conn.execute(
            "INSERT INTO corpus.character (campaign_id, cqi, first_snapshot_id,"
            " last_snapshot_id) VALUES (%s,%s,%s,%s)"
            " ON CONFLICT (campaign_id, cqi) DO UPDATE SET"
            " first_snapshot_id = LEAST(corpus.character.first_snapshot_id, EXCLUDED.first_snapshot_id),"
            " last_snapshot_id = GREATEST(corpus.character.last_snapshot_id, EXCLUDED.last_snapshot_id)"
            " RETURNING character_id",
            (campaign_id, cqi, snapshot_id, snapshot_id)).fetchone()
        self.characters[(campaign_id, cqi)] = row[0]
        return row[0]

    def _row(self, table, source, set_ids, extra):
        spec = rowmap.TABLES[table]
        cols, vals = [], []
        for col, src in sorted(spec.items()):
            if col in extra:
                continue
            if src is None:
                value = None
            elif src.startswith(rowmap.SET):
                value = set_ids.get(src[len(rowmap.SET):])
            else:
                value = source.get(src)
                fam = self.dicts.family_of(table, col)
                if fam is not None and value is not None:
                    value = self.dicts.ids_for(table, col, [value]).get(value)
                arr = dicts.ARRAY_FAMILIES.get((table, col))
                if arr is not None and value is not None:
                    ids = self.dicts.resolve(arr, value)
                    value = [ids.get(k) for k in value]
            cols.append(col)
            vals.append(value)
        for col, value in sorted(extra.items()):
            cols.append(col)
            vals.append(value)
        self.conn.execute(
            "INSERT INTO corpus.%s (%s) VALUES (%s)"
            % (table, ', '.join(cols), ', '.join(['%s'] * len(vals))), tuple(vals))

    def _ord_rows(self, table, snapshot_id, items):
        spec = rowmap.TABLES[table]
        columns = sorted(spec)
        resolvers, arrays = {}, {}
        for col in columns:
            if self.dicts.family_of(table, col) is not None:
                resolvers[col] = self.dicts.ids_for(
                    table, col, [i.get(spec[col]) for i in items])
        rows = []
        for ord_, item in enumerate(items):
            values = [snapshot_id, ord_]
            for col in columns:
                src = spec[col]
                v = None if src is None else item.get(src)
                if col in resolvers and v is not None:
                    v = resolvers[col].get(v)
                values.append(v)
            rows.append(tuple(values))
        if not rows:
            return
        with self.conn.cursor().copy(
                "COPY corpus.%s (snapshot_id, ord, %s) FROM STDIN"
                % (table, ', '.join(columns))) as cp:
            for row in rows:
                cp.write_row(row)

    def write_snapshot(self, snapshot, decision_uuid, req_id=None):
        t0 = time.time()
        log('write_snapshot enter uuid=%s' % decision_uuid)
        camp = canon.normalise(snapshot.get('campaign') or {})
        world = canon.normalise(snapshot.get('world') or {})
        ents = [{'context_kind': e.get('context_kind'),
                 'context_id': e.get('context_id'),
                 'state': canon.normalise(e.get('state') or {})}
                for e in (snapshot.get('entities') or [])]
        if len(ents) > MAX_ENTITIES:
            raise ValueError('decision has %d entities, limit %d' % (len(ents), MAX_ENTITIES))

        prepared = {'campaign': sets.prepare(camp), 'world': sets.prepare(world)}
        for i, e in enumerate(ents):
            prepared['e%d' % i] = sets.prepare(e.get('state') or {})
        hashed_ms = (time.time() - t0) * 1000

        with self.conn.unit('U1'):
            hit = self.conn.execute(
                "SELECT decision_id FROM corpus.decision WHERE decision_uuid = %s",
                (decision_uuid,)).fetchone()
            if hit:
                log('write_snapshot exit %.1f ms (idempotent hit %d)'
                    % ((time.time() - t0) * 1000, hit[0]))
                return hit[0]
            campaign_id = self._campaign(camp)
            ids = {name: self.setw.ensure(p) for name, p in prepared.items()}
            snapshot_id = self.conn.execute(
                "INSERT INTO corpus.snapshot (campaign_id, kind_id, ts, turn, version_id)"
                " VALUES (%s,%s,%s,%s,%s) RETURNING snapshot_id",
                (campaign_id, self.kind_ids['decision'],
                 snapshot.get('ts') or time.time(), camp.get('turn') or 0,
                 self._version())).fetchone()[0]
            self.conn.execute(
                "INSERT INTO corpus.decision (decision_id, decision_uuid, n_entities)"
                " VALUES (%s,%s,%s)", (snapshot_id, decision_uuid, len(ents)))
            self._row('snapshot_campaign', camp, ids['campaign'],
                      {'snapshot_id': snapshot_id})
            self._read_failures(snapshot_id, camp)
            self._row('snapshot_world', world, ids['world'],
                      {'snapshot_id': snapshot_id})
            self._ord_rows('world_army', snapshot_id, world.get('armies') or [])
            self._ord_rows('world_hostile', snapshot_id, world.get('hostiles') or [])
            for i, e in enumerate(ents):
                self._entity(snapshot_id, campaign_id, i, e, ids['e%d' % i])
            if req_id is not None:
                self.conn.execute(
                    "INSERT INTO corpus.rpc_response (req_id, ts, snapshot_id, payload)"
                    " VALUES (%s,%s,%s,%s) ON CONFLICT (req_id) DO NOTHING",
                    (req_id, time.time(), snapshot_id,
                     json.dumps({'store_ms': round((time.time() - t0) * 1000, 1)})))
        log('write_snapshot exit %.1f ms (hash %.1f ms) snapshot_id=%d'
            % ((time.time() - t0) * 1000, hashed_ms, snapshot_id))
        return snapshot_id

    def _read_failures(self, snapshot_id, camp):
        rows = [(snapshot_id, str(msg), int(n))
                for msg, n in (camp.get('read_failures') or {}).items()]
        if rows:
            with self.conn.cursor().copy(
                    "COPY corpus.snapshot_read_failure (snapshot_id, message, n)"
                    " FROM STDIN") as cp:
                for row in rows:
                    cp.write_row(row)

    def _has_ext(self, state):
        for src in rowmap.CHAR_STATE_EXT.values():
            if src is None or src.startswith(rowmap.SET):
                continue
            if src in state:
                return True
        return False

    def _char_derived(self, state):
        tiles = state.get('move_tiles') or []
        rc = state.get('reach_chars') or {}
        rs = state.get('reach_setts') or {}
        sett_keys = [k for k, v in rs.items() if v]
        sett_ids = self.dicts.resolve('region', sett_keys)
        rays = tiles[0].get('reach_rays') if tiles else None
        return {
            'move_x': [t.get('x') for t in tiles],
            'move_y': [t.get('y') for t in tiles],
            'reach_rays': rays,
            'reach_max': tiles[0].get('reach_max') if tiles else None,
            'reach_chars_true': [int(k) for k, v in rc.items() if v],
            'reach_setts_true': [sett_ids.get(k) for k in sett_keys],
        }

    def _entity(self, snapshot_id, campaign_id, seq, entity, set_ids):
        kind = entity.get('context_kind')
        state = entity.get('state') or {}
        character_id = None
        region_id = None
        if kind in ('lord', 'hero'):
            character_id = self._character(campaign_id, entity.get('context_id'),
                                           snapshot_id)
        if kind == 'province':
            region = state.get('region')
            region_id = self.dicts.resolve('region', [region]).get(region)
        self.conn.execute(
            "INSERT INTO corpus.snapshot_entity (snapshot_id, entity_seq, kind_id,"
            " character_id, region_id) VALUES (%s,%s,%s,%s,%s)",
            (snapshot_id, seq, self.entity_kind_ids[kind], character_id, region_id))
        for table in rowmap.ENTITY_TABLES[kind]:
            if table == 'char_state_ext' and not self._has_ext(state):
                continue
            extra = {'snapshot_id': snapshot_id}
            if table.startswith('char_state'):
                extra['character_id'] = character_id
            if table in ('char_state', 'province_state', 'campaign_state'):
                extra['entity_seq'] = seq
            if table == 'char_state':
                extra['is_hero'] = kind == 'hero'
                extra.update(self._char_derived(state))
            self._row(table, state, set_ids, extra)

    def write_decide(self, decision_id, offers, pick, scores=None, timings=None,
                     req_id=None):
        t0 = time.time()
        log('write_decide enter decision_id=%s offers=%d' % (decision_id, len(offers or [])))
        seqs = {(k, str(i)): s for s, (k, i) in enumerate(self.conn.execute(
            "SELECT se.kind_id, COALESCE(ch.cqi::text, se.region_id::text)"
            " FROM corpus.snapshot_entity se"
            " LEFT JOIN corpus.character ch ON ch.character_id = se.character_id"
            " WHERE se.snapshot_id = %s ORDER BY se.entity_seq", (decision_id,)))}
        with self.conn.unit('U2'):
            rows = []
            for seq, o in enumerate(offers or []):
                action = self._action(o.get('action_type'), o.get('key'))
                rows.append((decision_id, seq, o.get('entity_seq') or 0, action,
                             o.get('slot_index'), o.get('score'), o.get('exploit'),
                             o.get('rank'), o.get('pct_global'), o.get('gnn_impact'),
                             o.get('gnn_rank'), o.get('ggnn_score'), o.get('ggnn_rank')))
            if rows:
                with self.conn.cursor().copy(
                        "COPY corpus.offer (decision_id, offer_seq, entity_seq, action_id,"
                        " slot_index, score, exploit, rank, pct_global, gnn_impact,"
                        " gnn_rank, ggnn_score, ggnn_rank) FROM STDIN") as cp:
                    for row in rows:
                        cp.write_row(row)
            self.conn.execute(
                "UPDATE corpus.decision SET n_offers = %s WHERE decision_id = %s",
                (len(rows), decision_id))
            if timings:
                self._timings(decision_id, timings)
            if pick:
                self._taken(decision_id, pick)
            if req_id is not None:
                self._respond(req_id, decision_id)
        log('write_decide exit %.1f ms' % ((time.time() - t0) * 1000))

    def _action(self, action_type, key):
        types = self.dicts.resolve('action_type', [action_type])
        tid = types.get(action_type)
        row = self.conn.execute(
            "INSERT INTO dict.action (action_type_id, action_key) VALUES (%s,%s)"
            " ON CONFLICT (action_type_id, action_key) DO NOTHING RETURNING action_id",
            (tid, key)).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT action_id FROM dict.action WHERE action_type_id = %s"
                " AND action_key = %s", (tid, key)).fetchone()
        return row[0]

    def _timings(self, decision_id, t):
        hk = t.get('housekeep_parts') or {}
        self.conn.execute(
            "INSERT INTO corpus.decision_timing (decision_id, t_request, t_received,"
            " collect_ms, store_ms, pickup_lag_ms, roundtrip_ms, trace_ms, score_ms,"
            " housekeep_ms, hk_hud_check_ms, hk_generate_ms, hk_pick_log_ms,"
            " hk_verify_log_ms, hk_active_from_ms, hk_post_attack_ms, hk_drain_ms,"
            " hk_resolve_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (decision_id) DO NOTHING",
            (decision_id, t.get('t_request') or 0.0, t.get('t_received') or 0.0,
             t.get('collect_ms') or 0, t.get('store_ms') or 0,
             t.get('pickup_lag_ms') or 0, t.get('roundtrip_ms') or 0,
             t.get('trace_ms') or 0, t.get('score_ms') or 0, t.get('housekeep_ms'),
             hk.get('hud_check'), hk.get('generate_ms'), hk.get('pick_log'),
             hk.get('verify_log'), hk.get('active_from'), hk.get('post_attack'),
             hk.get('drain'), hk.get('resolve')))

    def _taken(self, decision_id, pick):
        camp = self.conn.execute(
            "SELECT campaign_id FROM corpus.snapshot WHERE snapshot_id = %s",
            (decision_id,)).fetchone()[0]
        policy = self.dicts.resolve_enum('policy', [pick.get('policy')])
        self.conn.execute(
            "INSERT INTO corpus.taken (decision_id, campaign_id, offer_seq, entity_seq,"
            " action_id, policy_id, ts, executed, confirmed, counted, latency_ms)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,false,false,false,0)"
            " ON CONFLICT (decision_id) DO NOTHING",
            (decision_id, camp, pick.get('offer_seq') or 0, pick.get('entity_seq') or 0,
             self._action(pick.get('action_type'), pick.get('key')),
             policy.get(pick.get('policy')), time.time()))

    def _respond(self, req_id, snapshot_id, **payload):
        self.conn.execute(
            "INSERT INTO corpus.rpc_response (req_id, ts, snapshot_id, payload)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (req_id) DO NOTHING",
            (req_id, time.time(), snapshot_id, json.dumps(payload)))

    def write_verification(self, decision_id, result, req_id=None):
        t0 = time.time()
        log('write_verification enter decision_id=%s' % decision_id)
        with self.conn.unit('U3'):
            self.conn.execute(
                "UPDATE corpus.taken SET executed = %s, confirmed = %s, counted = %s,"
                " latency_ms = %s WHERE decision_id = %s",
                (result.get('executed'), result.get('confirmed'),
                 result.get('counted'), result.get('latency_ms'), decision_id))
            if req_id is not None:
                self._respond(req_id, decision_id)
        log('write_verification exit %.1f ms' % ((time.time() - t0) * 1000))


    def _screen_ids(self, kind, rec):
        if kind != 'dilemma':
            return None, None
        ctx = str(rec.get('dilemma_id') or rec.get('root_context') or '')
        if not ctx:
            return None, None
        head = ctx.split(':', 1)[0]
        key = ctx.split(':', 1)[1] if ctx.startswith('Cco') and ':' in ctx else ctx
        if ctx.startswith('Cco') and 'Incident' in head:
            return None, self.dicts.resolve('incident', [key]).get(key)
        return self.dicts.resolve('dilemma', [key]).get(key), None

    def _battle_panel(self, snapshot_id, panel):
        res = panel.get('result') or {}
        cas = panel.get('casualties') or {}
        self.conn.execute(
            "INSERT INTO corpus.interrupt_battle_panel (interrupt_id, ally_cqi,"
            " enemy_cqi, n_ally_armies, n_enemy_armies, result_state, result_text,"
            " casualties_state, casualties_text) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (snapshot_id, _num(panel.get('ally_cqi')), _num(panel.get('enemy_cqi')),
             panel.get('n_ally_armies'), panel.get('n_enemy_armies'),
             res.get('state') or panel.get('result_flag'),
             res.get('text') or panel.get('outcome'),
             cas.get('state'), cas.get('text')))

    def _diplo_panel(self, snapshot_id, panel):
        self.conn.execute(
            "INSERT INTO corpus.interrupt_diplo_panel (interrupt_id, attitude,"
            " attitude_label, race, reliability, strength_ranks, settlements,"
            " demands, offers, treaties, amount_demanded, amount_offered)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (snapshot_id, _num(panel.get('attitude')), panel.get('attitude_label'),
             panel.get('race'), panel.get('reliability'),
             [_num(x) for x in panel.get('strength_ranks') or []],
             _num(panel.get('settlements')), panel.get('demands'),
             panel.get('offers'), panel.get('treaties'),
             _num(panel.get('amount_demanded')), _num(panel.get('amount_offered'))))

    BATTLE_PANEL_KINDS = ('pre_battle', 'battle_results')
    DIPLO_PANEL_KINDS = ('diplomacy_proposal', 'diplomacy_notice', 'war_declared',
                         'declare_war_cancel', 'ally_attacked')

    def write_interrupt(self, rec, req_id=None):
        t0 = time.time()
        log('write_interrupt enter kind=%s' % rec.get('kind'))
        camp = canon.normalise(rec.get('campaign') or {})
        world = canon.normalise(rec.get('world') or {})
        ts = rec.get('ts') or time.time()
        with self.conn.unit('U4'):
            campaign_id = self._campaign(camp)
            ids = {'campaign': self.setw.ensure(sets.prepare(camp)),
                   'world': self.setw.ensure(sets.prepare(world))}
            snapshot_id = self.conn.execute(
                "INSERT INTO corpus.snapshot (campaign_id, kind_id, ts, turn, version_id)"
                " VALUES (%s,%s,%s,%s,%s) RETURNING snapshot_id",
                (campaign_id, self.kind_ids['interrupt'], ts,
                 camp.get('turn') or 0, self._version())).fetchone()[0]
            prev = self.conn.execute(
                "SELECT snapshot_id FROM corpus.snapshot WHERE campaign_id = %s"
                " AND kind_id = %s AND ts <= %s ORDER BY ts DESC, snapshot_id DESC"
                " LIMIT 1", (campaign_id, self.kind_ids['decision'], ts)).fetchone()
            kinds = self.dicts.resolve_enum('interrupt_kind', [rec.get('kind')])
            states = self.dicts.resolve_enum('state_at', [rec.get('state_at') or 'panel'])
            policies = self.dicts.resolve_enum('policy', [rec.get('policy')])
            refusals = self.dicts.resolve_enum('refusal', [rec.get('refusal')])
            region = (rec.get('panel') or {}).get('region')
            regions = self.dicts.resolve('region', [region]) if region else {}
            dilemma_id, incident_id = self._screen_ids(rec['kind'], rec)
            self.conn.execute(
                "INSERT INTO corpus.interrupt (interrupt_id, prev_decision_id,"
                " ts_recorded, state_at_id, kind_id, root, dilemma_id, incident_id,"
                " root_context, region_id, chosen, answer, policy_id, executed,"
                " confirmed, counted, refusal_id, latency_ms)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (snapshot_id, prev[0] if prev else None,
                 rec.get('rpc_ts') or ts,
                 states.get(rec.get('state_at') or 'panel'), kinds[rec['kind']],
                 rec.get('root') or '', dilemma_id, incident_id,
                 rec.get('root_context'),
                 regions.get(region), rec.get('chosen') or '', rec.get('answer'),
                 policies.get(rec.get('policy')), rec.get('executed'),
                 rec.get('confirmed'), rec.get('counted'),
                 refusals.get(rec.get('refusal')), rec.get('latency_ms') or 0))
            self._row('snapshot_campaign', camp, ids['campaign'],
                      {'snapshot_id': snapshot_id})
            self._read_failures(snapshot_id, camp)
            self._row('snapshot_world', world, ids['world'], {'snapshot_id': snapshot_id})
            panel = rec.get('panel') or {}
            if panel and rec['kind'] in self.BATTLE_PANEL_KINDS:
                self._battle_panel(snapshot_id, panel)
            elif panel and rec['kind'] in self.DIPLO_PANEL_KINDS:
                self._diplo_panel(snapshot_id, panel)
            rows = []
            for ord_, o in enumerate(rec.get('options') or []):
                rows.append((snapshot_id, ord_, o.get('key') or str(ord_), o.get('text'),
                             o.get('option_id'), o.get('answer'), o.get('payload'),
                             o.get('exploit'), o.get('score'), o.get('gnn')))
            if rows:
                with self.conn.cursor().copy(
                        "COPY corpus.interrupt_option (interrupt_id, ord, option_key,"
                        " text, option_id, answer, payload, exploit, score, gnn)"
                        " FROM STDIN") as cp:
                    for row in rows:
                        cp.write_row(row)
            if req_id is not None:
                self._respond(req_id, snapshot_id)
        log('write_interrupt exit %.1f ms interrupt_id=%d'
            % ((time.time() - t0) * 1000, snapshot_id))
        return snapshot_id

    def write_diplomacy(self, row, req_id=None):
        t0 = time.time()
        log('write_diplomacy enter')
        with self.conn.unit('U5'):
            key = row.get('campaign_key')
            camp = self.conn.execute(
                "SELECT campaign_id FROM corpus.campaign WHERE campaign_key = %s",
                (key,)).fetchone()
            kinds = self.dicts.resolve_enum('diplo_event_kind', [row.get('kind') or 'deal'])
            chans = self.dicts.resolve_enum('diplo_channel',
                                            [row.get('channel') or 'outgoing'])
            self.conn.execute(
                "INSERT INTO corpus.diplomacy_event (campaign_id, turn, ts, ts_recorded,"
                " kind_id, channel_id) VALUES (%s,%s,%s,%s,%s,%s)",
                (camp[0] if camp else None, row.get('turn') or 0,
                 row.get('ts') or time.time(), time.time(),
                 kinds[row.get('kind') or 'deal'], chans[row.get('channel') or 'outgoing']))
            if req_id is not None:
                self._respond(req_id, None)
        log('write_diplomacy exit %.1f ms' % ((time.time() - t0) * 1000))

    def write_postmortem(self, rec, req_id=None):
        t0 = time.time()
        log('write_postmortem enter')
        with self.conn.unit('U6'):
            camp = self.conn.execute(
                "SELECT campaign_id FROM corpus.campaign WHERE campaign_key = %s",
                (rec.get('campaign_key'),)).fetchone()
            outcomes = self.dicts.resolve_enum('outcome', [rec.get('outcome') or 'completed'])
            self.conn.execute(
                "INSERT INTO corpus.postmortem (campaign_id, ts, run_dir, outcome_id,"
                " defeated, turns_played) VALUES (%s,%s,%s,%s,%s,%s)",
                (camp[0] if camp else None, rec.get('ts') or time.time(),
                 rec.get('run_dir') or '',
                 outcomes[rec.get('outcome') or 'completed'],
                 bool(rec.get('defeated')), rec.get('turns_played')))
            if req_id is not None:
                self._respond(req_id, None)
        log('write_postmortem exit %.1f ms' % ((time.time() - t0) * 1000))

    def write_ucb_pick(self, rec, req_id=None):
        t0 = time.time()
        log('write_ucb_pick enter')
        with self.conn.unit('U7'):
            factions = self.dicts.resolve('faction', [rec.get('faction')])
            maps = self.dicts.resolve('campaign_map', [rec.get('campaign_map')])
            pick_id = self.conn.execute(
                "INSERT INTO corpus.ucb_pick (ts, c, k, scale, total_plays,"
                " campaign_map_id, faction_id, n, tied)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING pick_id",
                (rec.get('ts') or time.time(), rec.get('c') or 0.0, rec.get('k'),
                 rec.get('scale'), rec.get('total_plays') or 0,
                 maps.get(rec.get('campaign_map')),
                 factions.get(rec.get('faction')), rec.get('n') or 0,
                 rec.get('tied') or 0)).fetchone()[0]
            if req_id is not None:
                self._respond(req_id, None)
        log('write_ucb_pick exit %.1f ms pick_id=%d'
            % ((time.time() - t0) * 1000, pick_id))
        return pick_id

