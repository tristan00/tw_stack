from __future__ import annotations

import json
import sys
import time

from decisions import canon, dicts, pg, rowmap, schema_map, sets

MAX_ENTITIES = 64


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
        return '%s:%s' % (faction or '', uuid or '')

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
