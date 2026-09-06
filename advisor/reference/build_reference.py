from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from advisor.reference import decode, packs, ron_schema
from decisions import pg

PG_TYPE = {
    'StringU8': 'TEXT', 'OptionalStringU8': 'TEXT', 'StringU16': 'TEXT',
    'OptionalStringU16': 'TEXT', 'I16': 'SMALLINT', 'I32': 'INTEGER',
    'I64': 'BIGINT', 'OptionalI32': 'INTEGER', 'F32': 'REAL',
    'F64': 'DOUBLE PRECISION', 'Boolean': 'BOOLEAN', 'ColourRGB': 'INTEGER',
}

NULLABLE = {'OptionalStringU8', 'OptionalStringU16', 'OptionalI32'}

FK_MIN_RESOLVED = 0.999
FK_INDEX_MIN_ROWS = 1000
LOC_PACK_PREFIX = 'local_en'


def log(msg):
    sys.stderr.write('%.3f  refbuild %s\n' % (time.time(), msg))


def table_name(stem):
    return stem[:-7] if stem.endswith('_tables') else stem


MAX_IDENT = 63


def db_column(name):
    if len(name.encode('utf-8')) <= MAX_IDENT:
        return name
    digest = hashlib.sha1(name.encode('utf-8')).hexdigest()[:6]
    return name[:MAX_IDENT - 7] + '_' + digest


def quote(name):
    return '"%s"' % db_column(name).replace('"', '""')


def column_sql(field, in_key):
    ft = field['field_type']
    pg_type = PG_TYPE[ft]
    if ft in NULLABLE and not in_key:
        return '%s %s' % (quote(field['name']), pg_type)
    if ft in NULLABLE and in_key:
        return "%s %s NOT NULL DEFAULT ''" % (quote(field['name']), pg_type)
    return '%s %s NOT NULL' % (quote(field['name']), pg_type)


def load_packs_and_schema():
    defs, schema_version = ron_schema.load(packs.SCHEMA_RON)
    found = packs.discover()
    return defs, schema_version, found


def collect_tables(defs, found):
    t0 = time.time()
    log('decode enter')
    tables, meta, missing = {}, {}, []
    for pack in found:
        for entry in pack['index']:
            name = entry[0]
            if not name.startswith('db/'):
                continue
            parts = name.split('/')
            if len(parts) < 3:
                continue
            stem = parts[1]
            blob = packs.extract(pack['path'], entry)
            if stem not in defs:
                missing.append(stem)
                meta[stem] = {'pack_table': stem, 'version': 0, 'source_pack': pack['name'],
                              'n_rows': 0, 'n_cols': 0, 'key_cols': [], 'key_unique': True,
                              'raw_bytes': entry[2], 'fields': []}
                continue
            guid, version, n, p = decode.parse_header(blob)
            if version not in defs[stem]:
                version = max(v for v in defs[stem] if isinstance(v, int))
            decl = defs[stem][version]
            _v, raw = decode.decode_table(blob, decl)
            order = sorted(range(len(decl)), key=lambda i: decl[i]['ca_order'])
            fields = [decl[i] for i in order]
            rows = [tuple(row[i] for i in order) for row in raw]
            key_cols = [f['name'] for f in fields if f['is_key']]
            tables[stem] = rows
            meta[stem] = {'pack_table': stem, 'version': version,
                          'source_pack': pack['name'], 'n_rows': len(rows),
                          'n_cols': len(fields), 'key_cols': key_cols,
                          'key_unique': True, 'raw_bytes': entry[2], 'fields': fields}
    log('decode exit %.0f ms  %d tables  %d rows  %d without a definition'
        % ((time.time() - t0) * 1000, len(tables),
           sum(len(v) for v in tables.values()), len(missing)))
    return tables, meta, missing


def collect_loc(found, defs):
    t0 = time.time()
    log('loc enter')
    prefixes = {}
    for stem, versions in defs.items():
        tbl = table_name(stem)
        for vkey, fields in versions.items():
            if isinstance(vkey, tuple):
                for f in fields:
                    name = f['name'] if isinstance(f, dict) else f
                    prefixes['%s_%s_' % (tbl, name)] = (tbl, name)
                continue
            for f in fields:
                if f['field_type'] in ('StringU8', 'OptionalStringU8'):
                    prefixes.setdefault('%s_%s_' % (tbl, f['name']), (tbl, f['name']))
    log('loc prefix map %d entries' % len(prefixes))
    rows, seen = [], set()
    packs_used = []
    for pack in found:
        if not pack['name'].startswith(LOC_PACK_PREFIX):
            continue
        used = 0
        for entry in pack['index']:
            name = entry[0]
            if not name.endswith('.loc'):
                continue
            blob = packs.extract(pack['path'], entry)
            if blob[0:2] != bytes([0xFF, 0xFE]) or blob[2:5] != b'LOC':
                continue
            stem = table_name(os.path.basename(name)[:-4].rstrip('_'))
            for key, text in decode.decode_loc(blob):
                if key in seen:
                    continue
                seen.add(key)
                best = None
                cut = 0
                pos = key.find('_')
                while pos != -1:
                    cand = prefixes.get(key[:pos + 1])
                    if cand is not None:
                        best, cut = cand, pos + 1
                    pos = key.find('_', pos + 1)
                if best is not None:
                    rows.append((best[0], best[1], key[cut:], key, text))
                else:
                    rows.append((stem, '', key, key, text))
            used += 1
        if used:
            packs_used.append('%s(%d)' % (pack['name'], used))
    matched = sum(1 for r in rows if r[1])
    log('loc exit %.0f ms  %d entries from %s  %d matched a <tbl>_<col>_ prefix (%.1f%%)'
        % ((time.time() - t0) * 1000, len(rows), ','.join(packs_used), matched,
           100.0 * matched / max(len(rows), 1)))
    return rows


def drop_schema_batched(con, schema, batch=200):
    rows = [r[0] for r in con.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
        " WHERE n.nspname = %s AND c.relkind = 'r'", (schema,))]
    for i in range(0, len(rows), batch):
        con.execute('DROP TABLE IF EXISTS %s CASCADE'
                    % ', '.join('%s.%s' % (schema, quote(r)) for r in rows[i:i + batch]))
        con.commit()
    con.execute('DROP SCHEMA IF EXISTS %s CASCADE' % schema)
    con.commit()


VIEWS_SQL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'sql', '04_ref_views.sql')


def apply_views(con):
    t0 = time.time()
    with open(VIEWS_SQL, encoding='utf-8') as fh:
        con.execute(fh.read())
    con.commit()
    n = con.execute("SELECT count(*) FROM information_schema.views"
                    " WHERE table_schema='refc'").fetchone()[0]
    log('views applied %d in refc %.0f ms' % (n, (time.time() - t0) * 1000))


def create_and_copy(con, tables, meta):
    t0 = time.time()
    log('create+copy enter')
    total = 0
    done = 0
    for stem, rows in tables.items():
        info = meta[stem]
        fields = info['fields']
        keys = set(info['key_cols'])
        cols = ', '.join(column_sql(f, f['name'] in keys) for f in fields)
        tbl = table_name(stem)
        con.execute('CREATE TABLE ref_build.%s (%s)' % (quote(tbl), cols))
        if not rows:
            continue
        names = ', '.join(quote(f['name']) for f in fields)
        nullable_key = [i for i, f in enumerate(fields)
                        if f['field_type'] in NULLABLE and f['name'] in keys]
        with con.cursor().copy(
                'COPY ref_build.%s (%s) FROM STDIN (FORMAT binary)' % (quote(tbl), names)) as cp:
            cp.set_types([sql_copy_type(f) for f in fields])
            for row in rows:
                if nullable_key:
                    row = list(row)
                    for i in nullable_key:
                        if row[i] is None:
                            row[i] = ''
                cp.write_row(row)
        total += len(rows)
        done += 1
        if done % 200 == 0:
            con.commit()
    con.commit()
    log('create+copy exit %.0f ms  %d tables  %d rows'
        % ((time.time() - t0) * 1000, len(tables), total))
    return total


COPY_TYPE = {
    'StringU8': 'text', 'OptionalStringU8': 'text', 'StringU16': 'text',
    'OptionalStringU16': 'text', 'I16': 'int2', 'I32': 'int4', 'I64': 'int8',
    'OptionalI32': 'int4', 'F32': 'float4', 'F64': 'float8',
    'Boolean': 'bool', 'ColourRGB': 'int4',
}


def sql_copy_type(field):
    return COPY_TYPE[field['field_type']]


def add_primary_keys(con, meta):
    t0 = time.time()
    log('primary keys enter')
    n = dup = 0
    for stem, info in meta.items():
        if not info['key_cols'] or not info['fields']:
            continue
        tbl = table_name(stem)
        cols = ', '.join(quote(c) for c in info['key_cols'])
        try:
            con.execute('ALTER TABLE ref_build.%s ADD PRIMARY KEY (%s)' % (quote(tbl), cols))
            n += 1
            if n % 200 == 0:
                con.commit()
        except Exception as exc:
            con.rollback()
            info['key_unique'] = False
            dup += 1
            if dup <= 3:
                log('duplicate key on %s: %s' % (tbl, str(exc)[:90]))
    log('primary keys exit %.0f ms  %d declared  %d tables with duplicate keys'
        % ((time.time() - t0) * 1000, n, dup))
    return dup


def relations(defs, meta):
    out = []
    present = {table_name(s) for s in meta}
    for stem, info in meta.items():
        for f in info.get('fields') or []:
            ref = f.get('is_reference')
            if not ref:
                continue
            if isinstance(ref, dict):
                ref = ref.get('_positional') or ref.get('_args') or []
            if isinstance(ref, (list, tuple)) and len(ref) >= 2:
                tgt_tbl, tgt_col = table_name(str(ref[0])), str(ref[1])
            elif isinstance(ref, str) and '.' in ref:
                a, b = ref.split('.', 1)
                tgt_tbl, tgt_col = table_name(a), b
            else:
                continue
            if tgt_tbl not in present:
                out.append((table_name(stem), f['name'], tgt_tbl, tgt_col, False, None))
                continue
            out.append((table_name(stem), f['name'], tgt_tbl, tgt_col, True, None))
    return out


def add_foreign_keys(con, rels, meta):
    t0 = time.time()
    log('foreign keys enter %d candidate relations' % len(rels))
    rows = {table_name(s): i['n_rows'] for s, i in meta.items()}
    unique = set()
    for tbl, col, ucol in con.execute(
            "SELECT c.relname, a.attname, a.attname FROM pg_class c"
            " JOIN pg_namespace n ON n.oid=c.relnamespace"
            " JOIN pg_index x ON x.indrelid=c.oid AND x.indisunique"
            " JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.indkey[0]"
            " WHERE n.nspname='ref_build' AND array_length(x.indkey,1)=1"):
        unique.add((tbl, col))
    types = {}
    for tbl, col, typ in con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
            " WHERE table_schema = 'ref_build'"):
        types[(tbl, col)] = typ
    declared = skipped = mismatched = 0
    resolved = {}
    for src, col, tgt, tcol, target_present, _ in rels:
        if not target_present or (tgt, tcol) not in unique:
            skipped += 1
            continue
        st = types.get((src, db_column(col)))
        tt = types.get((tgt, db_column(tcol)))
        if st is None or tt is None:
            skipped += 1
            continue
        same_type = st == tt
        lhs = 't.%s' % quote(tcol) if same_type else 't.%s::text' % quote(tcol)
        rhs = 's.%s' % quote(col) if same_type else 's.%s::text' % quote(col)
        row = con.execute(
            'SELECT count(*), count(*) FILTER (WHERE t.%s IS NOT NULL)'
            ' FROM ref_build.%s s LEFT JOIN ref_build.%s t ON %s = %s'
            ' WHERE s.%s IS NOT NULL%s'
            % (quote(tcol), quote(src), quote(tgt), lhs, rhs, quote(col),
               (" AND s.%s <> ''" % quote(col)) if st == 'text' else '')).fetchone()
        total, hit = row
        ratio = 1.0 if total == 0 else hit / float(total)
        resolved[(src, col)] = ratio
        if ratio < FK_MIN_RESOLVED:
            skipped += 1
            continue
        if not same_type:
            mismatched += 1
            continue
        try:
            con.execute('ALTER TABLE ref_build.%s ADD FOREIGN KEY (%s)'
                        ' REFERENCES ref_build.%s (%s)'
                        % (quote(src), quote(col), quote(tgt), quote(tcol)))
            declared += 1
            if rows.get(src, 0) > FK_INDEX_MIN_ROWS:
                con.execute('CREATE INDEX ON ref_build.%s (%s)' % (quote(src), quote(col)))
            if declared % 200 == 0:
                con.commit()
        except Exception as exc:
            con.rollback()
            skipped += 1
            if skipped <= 3:
                log('fk %s.%s -> %s.%s failed: %s' % (src, col, tgt, tcol, str(exc)[:80]))
    log('foreign keys exit %.0f ms  %d declared  %d skipped  %d resolve but the'
        ' referencing type differs (4.4 d)'
        % ((time.time() - t0) * 1000, declared, skipped, mismatched))
    return declared, resolved


def write_metadata(con, build_id, meta, rels, resolved, loc_rows, found):
    t0 = time.time()
    con.execute('TRUNCATE ops.table_meta, ops.column_meta')
    with con.cursor().copy(
            'COPY ops.table_meta (tbl, pack_table, version, source_pack, n_rows,'
            ' n_cols, key_cols, key_unique, raw_bytes) FROM STDIN') as cp:
        for stem, info in meta.items():
            cp.write_row((table_name(stem), info['pack_table'], info['version'],
                          info['source_pack'], info['n_rows'], info['n_cols'],
                          info['key_cols'], info['key_unique'], info['raw_bytes']))
    rel_by = {(s, c): (t, tc, present) for s, c, t, tc, present, _ in rels}
    with con.cursor().copy(
            'COPY ops.column_meta (tbl, col, ca_order, ron_type, pg_type, is_key,'
            ' is_reference, ref_tbl, ref_col, ref_resolved, fk_declared,'
            ' default_value, description) FROM STDIN') as cp:
        for stem, info in meta.items():
            tbl = table_name(stem)
            keys = set(info['key_cols'])
            for f in info.get('fields') or []:
                rel = rel_by.get((tbl, f['name']))
                ratio = resolved.get((tbl, f['name']))
                cp.write_row((
                    tbl, db_column(f['name']), int(f['ca_order']), f['field_type'],
                    PG_TYPE[f['field_type']], f['name'] in keys, rel is not None,
                    rel[0] if rel else None, rel[1] if rel else None, ratio,
                    bool(ratio is not None and ratio >= FK_MIN_RESOLVED),
                    "''" if (f['field_type'] in NULLABLE and f['name'] in keys) else None,
                    ((('ron_name=' + f['name'] + ' ')
                      if db_column(f['name']) != f['name'] else '')
                     + (f.get('description') or ''))[:400]))
    con.execute('CREATE TABLE ref_build.loc (tbl TEXT NOT NULL, col TEXT NOT NULL,'
                ' key TEXT NOT NULL, loc_key TEXT NOT NULL PRIMARY KEY, text TEXT NOT NULL)')
    with con.cursor().copy(
            'COPY ref_build.loc (tbl, col, key, loc_key, text) FROM STDIN') as cp:
        for row in loc_rows:
            cp.write_row(row)
    con.execute('CREATE INDEX ON ref_build.loc (tbl, col, key)')
    con.execute('DELETE FROM ops.manifest_pack WHERE build_id = %s', (build_id,))
    with con.cursor().copy(
            'COPY ops.manifest_pack (build_id, pack, sha256, size, mtime,'
            ' load_order, pack_type, n_db_files, n_loc_files) FROM STDIN') as cp:
        for i, p in enumerate(found):
            cp.write_row((build_id, p['name'],
                          hashlib.sha256(p['name'].encode()).digest(), p['size'],
                          p['mtime'], i, p['type'],
                          sum(1 for e in p['index'] if e[0].startswith('db/')),
                          sum(1 for e in p['index'] if e[0].endswith('.loc'))))
    log('metadata written %.0f ms' % ((time.time() - t0) * 1000))


def live_manifest(con):
    row = con.execute(
        "SELECT build_id, encode(fingerprint,'hex'), encode(schema_sha256,'hex'),"
        " encode(fingerprint_cheap,'hex') FROM ops.manifest"
        " WHERE status = 'live'").fetchone()
    if not row:
        return None
    return {'build_id': row[0], 'full': row[1], 'schema': row[2],
            'cheap': row[3] or None}


def resolve_dictionaries(con, build_id):
    t0 = time.time()
    flips = 0
    for family, ref_tbl, ref_col in con.execute(
            'SELECT family, ref_tbl, ref_col FROM dict.family'):
        tbl = table_name(ref_tbl)
        exists = con.execute(
            "SELECT to_regclass('ref.%s')" % tbl).fetchone()[0]
        if exists is None:
            continue
        n = con.execute(
            'UPDATE dict.%s d SET is_reference = e.hit,'
            ' ref_build_id = CASE WHEN e.hit THEN %%s ELSE NULL END'
            ' FROM (SELECT d2.id, EXISTS (SELECT 1 FROM ref.%s r WHERE r.%s = d2.key) AS hit'
            '         FROM dict.%s d2) e'
            ' WHERE e.id = d.id AND d.is_reference IS DISTINCT FROM e.hit'
            % (family, tbl, quote(ref_col), family), (build_id,)).rowcount
        flips += max(0, n)
    log('dict.resolve exit %.0f ms  %d flips' % ((time.time() - t0) * 1000, flips))
    return flips


def build(con, defs, schema_version, found, fp):
    t0 = time.time()
    build_id = con.execute(
        "INSERT INTO ops.manifest (started_ts, status, exe_version, steam_build_id,"
        " schema_sha256, schema_version, fingerprint, fingerprint_cheap)"
        " VALUES (%s,'building',%s,%s,decode(%s,'hex'),%s,decode(%s,'hex'),"
        " decode(%s,'hex')) RETURNING build_id",
        (time.time(), fp.get('version') or '', fp.get('build_id'),
         fp['schema'], schema_version, fp['full'], fp['cheap'])).fetchone()[0]
    log('build %d enter' % build_id)
    drop_schema_batched(con, 'ref_build')
    con.execute('CREATE SCHEMA ref_build')
    con.commit()

    tables, meta, missing = collect_tables(defs, found)
    loc_rows = collect_loc(found, defs)
    n_rows = create_and_copy(con, tables, meta)
    con.commit()
    add_primary_keys(con, meta)
    con.commit()
    rels = relations(defs, meta)
    declared, resolved = add_foreign_keys(con, rels, meta)
    con.commit()
    write_metadata(con, build_id, meta, rels, resolved, loc_rows, found)
    con.commit()

    t1 = time.time()
    con.execute('ANALYZE')
    log('analyze %.0f ms' % ((time.time() - t1) * 1000))

    drop_schema_batched(con, 'ref_prev')
    con.execute("UPDATE ops.manifest SET status='superseded' WHERE status='live'")
    con.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name='ref'")
    if con.execute("SELECT 1 FROM information_schema.schemata"
                   " WHERE schema_name='ref'").fetchone():
        con.execute('ALTER SCHEMA ref RENAME TO ref_prev')
    con.execute('ALTER SCHEMA ref_build RENAME TO ref')
    seconds = time.time() - t0
    con.execute(
        "UPDATE ops.manifest SET status='live', finished_ts=%s, n_tables=%s,"
        " n_rows=%s, n_loc=%s, build_seconds=%s WHERE build_id=%s",
        (time.time(), len(meta), n_rows, len(loc_rows), seconds, build_id))
    con.commit()
    log('swap done, build %d live' % build_id)
    resolve_dictionaries(con, build_id)
    drop_schema_batched(con, 'ref_prev')
    apply_views(con)
    log('build %d exit %.1f s  %d tables  %d rows  %d loc  %d fks'
        % (build_id, seconds, len(meta), n_rows, len(loc_rows), declared))
    return build_id, seconds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    t0 = time.time()
    log('enter port=%s' % pg.PORT)
    con = pg.connect(app_name='tw-refbuild', autocommit=False)
    previous = live_manifest(con)
    found = packs.discover()
    fp = packs.fingerprint(found, None if args.force else previous)
    if previous and not args.force and fp.get('full') == previous.get('full'):
        log('reference unchanged build_id=%d (%.0f ms)'
            % (previous['build_id'], (time.time() - t0) * 1000))
        con.close()
        return 0
    if args.check:
        log('reference CHANGED, a build is needed')
        con.close()
        return 1
    defs, schema_version = ron_schema.load(packs.SCHEMA_RON)
    build_id, seconds = build(con, defs, schema_version, found, fp)
    con.close()
    log('exit %.1f s  build_id=%d' % (time.time() - t0, build_id))
    return 0


if __name__ == '__main__':
    sys.exit(main())
