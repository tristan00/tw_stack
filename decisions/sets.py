from __future__ import annotations

import sys
import time

from decisions import dicts as dicts_mod
from decisions import hydrate, schema_map


def log(msg):
    sys.stderr.write('%.3f  sets %s\n' % (time.time(), msg))


def collections_of(record):
    out = {}
    for key, value in record.items():
        if key in schema_map.NOT_SETS or key not in schema_map.COLLECTIONS:
            continue
        if not hydrate.is_collection(key, value):
            continue
        out[key] = value
    return out


def prepare(record):
    out = {}
    for key, value in collections_of(record).items():
        kind = schema_map.COLLECTIONS[key][0]
        rows = schema_map.members(key, value)
        members = [tuple(r[c] for c in sorted(r)) for r in rows]
        out[key] = {'kind': kind, 'rows': rows, 'n': len(rows),
                    'hash': hydrate.set_hash(kind, members),
                    'candidates': schema_map.candidates(value) if key == 'lord_pools' else None}
    return out


class SetWriter:

    def __init__(self, con, dicts):
        self.con = con
        self.dicts = dicts
        self.known = {}

    def ensure(self, prepared):
        t0 = time.time()
        want = {}
        for key, s in prepared.items():
            ident = (s['kind'], s['hash'])
            if ident not in self.known:
                want[ident] = s
        new = {}
        if want:
            idents = sorted(want)
            for kind, digest, set_id in self.con.execute(
                    "SELECT kind, hash, set_id FROM corpus.state_set"
                    " WHERE (kind, hash) IN (SELECT * FROM unnest(%s::smallint[], %s::bytea[]))",
                    ([k for k, _ in idents], [h for _, h in idents])):
                self.known[(kind, bytes(digest))] = set_id
            missing = [i for i in idents if i not in self.known]
            for kind, digest in missing:
                row = self.con.execute(
                    "INSERT INTO corpus.state_set (kind, hash, n) VALUES (%s, %s, %s)"
                    " ON CONFLICT (kind, hash) DO NOTHING RETURNING set_id",
                    (kind, digest, want[(kind, digest)]['n'])).fetchone()
                if row is None:
                    row = self.con.execute(
                        "SELECT set_id FROM corpus.state_set WHERE kind = %s AND hash = %s",
                        (kind, digest)).fetchone()
                else:
                    new[(kind, digest)] = want[(kind, digest)]
                self.known[(kind, digest)] = row[0]
        ids = {key: self.known[(s['kind'], s['hash'])] for key, s in prepared.items()}
        self.write_members(prepared, new, ids)
        log('ensure exit %.1f ms sets=%d new=%d'
            % ((time.time() - t0) * 1000, len(prepared), len(new)))
        return ids

    def write_members(self, prepared, new, ids):
        by_table, written = {}, set()
        for key, s in prepared.items():
            ident = (s['kind'], s['hash'])
            if ident not in new or ident in written:
                continue
            written.add(ident)
            table = schema_map.COLLECTIONS[key][1]
            by_table.setdefault(table, []).append((ids[key], key, s))
        for table, batch in by_table.items():
            columns = sorted(schema_map.COLLECTIONS[batch[0][1]][3])
            resolvers, arrays = {}, {}
            for col in columns:
                spec = self.dicts.family_of(table, col)
                if spec is not None:
                    keys = [r[col] for _, _, s in batch for r in s['rows']]
                    resolvers[col] = self.dicts.ids_for(table, col, keys)
                fam = dicts_mod.ARRAY_FAMILIES.get((table, col))
                if fam is not None:
                    keys = [k for _, _, s in batch for r in s['rows']
                            for k in (r[col] or [])]
                    arrays[col] = self.dicts.resolve(fam, keys)
            rows = []
            for set_id, _, s in batch:
                for ord_, r in enumerate(s['rows']):
                    values = [set_id, ord_]
                    for col in columns:
                        v = r[col]
                        if col in resolvers and v is not None:
                            v = resolvers[col].get(v)
                        elif col in arrays and v is not None:
                            v = [arrays[col].get(k) for k in v]
                        values.append(v)
                    rows.append(tuple(values))
            if not rows:
                continue
            cols = ', '.join(['set_id', 'ord'] + columns)
            with self.con.cursor().copy(
                    "COPY corpus.%s (%s) FROM STDIN" % (table, cols)) as cp:
                for row in rows:
                    cp.write_row(row)
        self.write_candidates(prepared, new, ids)

    def write_candidates(self, prepared, new, ids):
        rows, seen = [], set()
        for key, s in prepared.items():
            ident = (s['kind'], s['hash'])
            if key != 'lord_pools' or ident not in new or ident in seen:
                continue
            seen.add(ident)
            subtypes = [c['subtype_id'] for c in s['candidates']]
            skills = [c['bg_skill_id'] for c in s['candidates']]
            cand = [c['cand_subtype_id'] for c in s['candidates']]
            sub_ids = self.dicts.resolve('agent_subtype', subtypes + cand)
            skill_ids = self.dicts.resolve('skill', skills)
            trait_keys = [k for c in s['candidates'] for k in (c['trait_ids'] or [])]
            trait_ids = self.dicts.resolve('trait', trait_keys)
            for c in s['candidates']:
                traits = ([trait_ids.get(k) for k in c['trait_ids']]
                          if c['trait_ids'] is not None else None)
                rows.append((ids[key], sub_ids.get(c['subtype_id']), c['ord'],
                             c['can'], c['agent'], skill_ids.get(c['bg_skill_id']),
                             sub_ids.get(c['cand_subtype_id']), traits))
        if not rows:
            return
        with self.con.cursor().copy(
                "COPY corpus.lord_pool_candidate (set_id, subtype_id, ord, can, agent,"
                " bg_skill_id, cand_subtype_id, trait_ids) FROM STDIN") as cp:
            for row in rows:
                cp.write_row(row)


def read_collection(con, dicts, key, set_id):
    kind, table, shape, cols = schema_map.COLLECTIONS[key]
    columns = sorted(cols)
    rows = list(con.execute(
        "SELECT %s FROM corpus.%s WHERE set_id = %%s ORDER BY ord"
        % (', '.join(columns), table), (set_id,)))
    back, arr = {}, {}
    for i, col in enumerate(columns):
        if dicts.family_of(table, col) is not None:
            back[col] = dicts.keys_for(table, col, [r[i] for r in rows])
        fam = dicts_mod.ARRAY_FAMILIES.get((table, col))
        if fam is not None:
            ids = [k for r in rows for k in (r[i] or [])]
            arr[col] = dicts.keys_for_family(fam, ids)

    def val(i, col, r):
        v = r[i]
        if col in back and v is not None:
            return back[col].get(v)
        if col in arr and v is not None:
            return [arr[col].get(k) for k in v]
        return v
    if shape == schema_map.LIST:
        out = []
        for r in rows:
            out.append({cols[col]: val(i, col, r) for i, col in enumerate(columns)})
        return out
    if shape == schema_map.DICT_SCALAR:
        kcol = [c for c, s in cols.items() if s == '{key}'][0]
        vcol = [c for c, s in cols.items() if s == '{value}'][0]
        ki, vi = columns.index(kcol), columns.index(vcol)
        return {val(ki, kcol, r): val(vi, vcol, r) for r in rows}
    if key == 'lord_pools':
        return read_lord_pools(con, dicts, set_id, rows, columns, cols, val)
    if shape == schema_map.DICT_DICT:
        kcol = [c for c, s in cols.items() if s == '{key}'][0]
        ki = columns.index(kcol)
        out = {}
        for r in rows:
            item = {cols[col]: val(i, col, r)
                    for i, col in enumerate(columns) if col != kcol}
            out[val(ki, kcol, r)] = item
        return out
    if shape == schema_map.DICT_LIST:
        kcol = [c for c, s in cols.items() if s == '{key}'][0]
        ki = columns.index(kcol)
        out = {}
        for r in rows:
            group = out.setdefault(val(ki, kcol, r), [])
            item = {cols[col]: val(i, col, r)
                    for i, col in enumerate(columns) if col != kcol}
            if all(v is None for v in item.values()):
                continue
            for col, src in cols.items():
                if (key, src) in schema_map.OMIT_WHEN_NULL and item.get(src) is None:
                    item.pop(src, None)
            group.append(item)
        return out
    raise ValueError('unknown shape %r' % shape)


def read_lord_pools(con, dicts, set_id, rows, columns, cols, val):
    ki = columns.index('subtype_id')
    ni = columns.index('n')
    out = {}
    for r in rows:
        out[val(ki, 'subtype_id', r)] = {'n': r[ni], 'agents': [], 'bg_skills': [],
                                         'can': [], 'subtypes': [], 'traits': [],
                                         'cqis': [], 'ranks': [], 'units': []}
    cand = list(con.execute(
        "SELECT subtype_id, ord, can, agent, bg_skill_id, cand_subtype_id, trait_ids"
        " FROM corpus.lord_pool_candidate WHERE set_id = %s ORDER BY subtype_id, ord",
        (set_id,)))
    sub_keys = dicts.keys_for_family('agent_subtype',
                                     [c[0] for c in cand] + [c[5] for c in cand])
    skill_keys = dicts.keys_for_family('skill', [c[4] for c in cand])
    trait_keys = dicts.keys_for_family(
        'trait', [t for c in cand for t in (c[6] or [])])
    for subtype_id, ord_, can, agent, bg, cand_sub, traits in cand:
        pool = out[sub_keys.get(subtype_id)]
        pool['can'].append(can)
        pool['agents'].append(agent)
        pool['bg_skills'].append(skill_keys.get(bg) if bg is not None else None)
        pool['subtypes'].append(sub_keys.get(cand_sub))
        pool['traits'].append([trait_keys.get(t) for t in traits] if traits else [])
        pool['cqis'].append(0)
        pool['ranks'].append(0)
        pool['units'].append(None)
    return out
