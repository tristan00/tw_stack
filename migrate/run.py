import argparse
import io
import json
import os
import pickle
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, hydrate, pg

SQL_FILES = ['sql/03_tables.sql', 'sql/03_seed.sql', 'sql/03_views.sql', 'sql/03_constraints.sql']

PROBE_DDL = """
CREATE SCHEMA IF NOT EXISTS migrate;
CREATE TABLE IF NOT EXISTS migrate.set_member_probe (
  set_id BIGINT NOT NULL, ord INTEGER NOT NULL, payload BYTEA NOT NULL,
  PRIMARY KEY (set_id, ord));
CREATE TABLE IF NOT EXISTS migrate.scalar_probe (
  snapshot_id BIGINT NOT NULL, role TEXT NOT NULL, entity_seq SMALLINT NOT NULL,
  scalars BYTEA NOT NULL, set_map BYTEA NOT NULL,
  PRIMARY KEY (snapshot_id, role, entity_seq));
"""

def log(msg):
    sys.stderr.write('%.3f  run %s %s\n' % (time.time(), os.getpid(), msg))


def text_of(z):
    if isinstance(z, memoryview):
        z = z.tobytes()
    if isinstance(z, (bytes, bytearray)):
        try:
            return zlib.decompress(bytes(z)).decode('utf-8')
        except zlib.error:
            return bytes(z).decode('utf-8')
    return z


def checkpoint(con, stage, state, rows_in=None, rows_out=None, started=None):
    con.execute(
        "INSERT INTO migrate.checkpoint"
        " (stage,range_lo,range_hi,state,worker,started_ts,finished_ts,rows_in,rows_out)"
        " VALUES (%s,0,0,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (stage,range_lo) DO UPDATE SET state=EXCLUDED.state,"
        " finished_ts=EXCLUDED.finished_ts, rows_in=EXCLUDED.rows_in, rows_out=EXCLUDED.rows_out",
        (stage, state, str(os.getpid()), started, time.time(), rows_in, rows_out))


NEW_SCHEMAS = ('corpus', 'dict', 'ref', 'ops', 'analytics', 'migrate')


def stage_schema(con):
    log('S3 schema enter')
    t0 = time.time()
    for schema in NEW_SCHEMAS:
        con.execute('DROP SCHEMA IF EXISTS %s CASCADE' % schema)
    for path in SQL_FILES:
        con.execute(io.open(path, encoding='utf-8').read())
    ms = (time.time() - t0) * 1000
    log('S3 schema exit %.0f ms' % ms)
    return ms


def fetch_sample(con, modulus, residue):
    log('read sample enter')
    t0 = time.time()
    rows = []
    for role, col in (('CB', 'campaign_blob'), ('WB', 'world_blob')):
        sql = ("SELECT d.decision_id, %%s, 0, b.z FROM decisions d JOIN blobs b ON b.blob_id=d.%s"
               " WHERE d.decision_id %%%% %%s = %%s" % col)
        rows += list(con.execute(sql, (role, modulus, residue)))
    rows += list(con.execute(
        "SELECT d.decision_id, 'EB', e.entity_seq, b.z FROM decisions d"
        " JOIN entities e ON e.decision_id=d.decision_id JOIN blobs b ON b.blob_id=e.features_blob"
        " WHERE d.decision_id %% %s = %s", (modulus, residue)))
    ms = (time.time() - t0) * 1000
    log('read sample exit %.0f ms rows=%d' % (ms, len(rows)))
    return rows, ms


def stage_encode(rows):
    log('M1 encode enter')
    t0 = time.time()
    out, sets = [], {}
    for decision_id, role, entity_seq, z in rows:
        rec = canon.normalise(json.loads(text_of(z)))
        enc = hydrate.encode_record(rec)
        set_map = {}
        for key, s in enc['sets'].items():
            ident = (s['kind'], s['hash'])
            sets.setdefault(ident, s)
            set_map[key] = {'kind': s['kind'], 'hash': s['hash'],
                            'shape': s['shape'], 'fields': s['fields']}
        out.append((decision_id, role, entity_seq, enc['scalars'], set_map))
    ms = (time.time() - t0) * 1000
    log('M1 encode exit %.0f ms records=%d distinct_sets=%d (%.3f ms/record)'
        % (ms, len(rows), len(sets), ms / max(len(rows), 1)))
    return out, sets, ms


def stage_copy(con, records, sets, sync_commit):
    log('M1 copy enter synchronous_commit=%s' % sync_commit)
    con.execute("SET synchronous_commit = %s" % sync_commit)
    t0 = time.time()
    con.execute("TRUNCATE migrate.set_member_probe, migrate.scalar_probe")
    con.execute("DELETE FROM corpus.state_set WHERE n >= 0")
    ids = {}
    with con.cursor().copy("COPY corpus.state_set (kind, hash, n) FROM STDIN (FORMAT binary)") as cp:
        cp.set_types(['int2', 'bytea', 'int2'])
        for (kind, digest), s in sets.items():
            cp.write_row((kind, digest, s['n']))
    for kind, digest, set_id in con.execute("SELECT kind, hash, set_id FROM corpus.state_set"):
        ids[(kind, bytes(digest))] = set_id
    member_rows = 0
    with con.cursor().copy("COPY migrate.set_member_probe (set_id, ord, payload) FROM STDIN (FORMAT binary)") as cp:
        cp.set_types(['int8', 'int4', 'bytea'])
        for (kind, digest), s in sets.items():
            set_id = ids[(kind, digest)]
            for ord_, member in enumerate(s['members']):
                cp.write_row((set_id, ord_, hydrate.encode_members(kind, [member])))
                member_rows += 1
    with con.cursor().copy("COPY migrate.scalar_probe (snapshot_id, role, entity_seq, scalars, set_map) FROM STDIN (FORMAT binary)") as cp:
        cp.set_types(['int8', 'text', 'int2', 'bytea', 'bytea'])
        for decision_id, role, entity_seq, scalars, set_map in records:
            packed = {k: (v['kind'], ids[(v['kind'], v['hash'])], v['shape'], v['fields'])
                      for k, v in set_map.items()}
            cp.write_row((decision_id, role, entity_seq,
                          pickle.dumps(scalars, 4), pickle.dumps(packed, 4)))
    con.commit()
    ms = (time.time() - t0) * 1000
    total = len(sets) + member_rows + len(records)
    log('M1 copy exit %.0f ms rows=%d (%.0f rows/s) sets=%d members=%d'
        % (ms, total, total / (ms / 1000.0), len(sets), member_rows))
    return {'ms': ms, 'rows': total, 'rows_per_s': round(total / (ms / 1000.0)),
            'sets': len(sets), 'members': member_rows, 'synchronous_commit': sync_commit}


def stage_verify(con, rows):
    log('V1 verify enter')
    t0 = time.time()
    members = {}
    for set_id, ord_, payload in con.execute(
            "SELECT set_id, ord, payload FROM migrate.set_member_probe ORDER BY set_id, ord"):
        members.setdefault(set_id, []).append(bytes(payload))
    hydrate_ms = 0.0
    got = {}
    for snapshot_id, role, entity_seq, scalars, set_map in con.execute(
            "SELECT snapshot_id, role, entity_seq, scalars, set_map FROM migrate.scalar_probe"):
        t1 = time.time()
        rec = pickle.loads(bytes(scalars))
        for key, (kind, set_id, shape, fields) in pickle.loads(bytes(set_map)).items():
            rec[key] = hydrate.decode_collection(
                {'shape': shape, 'fields': fields,
                 'members': [decode_payload(p) for p in members.get(set_id, [])]})
        hydrate_ms += (time.time() - t1) * 1000.0
        got[(snapshot_id, role, entity_seq)] = canon.canon(rec)
    ok = bad = 0
    mismatches = []
    for decision_id, role, entity_seq, z in rows:
        want = canon.canon(canon.normalise(json.loads(text_of(z))))
        have = got.get((decision_id, role, entity_seq))
        if have == want:
            ok += 1
        else:
            bad += 1
            if len(mismatches) < 5:
                mismatches.append({'snapshot_id': decision_id, 'role': role,
                                   'entity_seq': entity_seq, 'have_len': len(have or ''),
                                   'want_len': len(want)})
    ms = (time.time() - t0) * 1000
    log('V1 verify exit %.0f ms ok=%d bad=%d hydrate=%.3f ms/record'
        % (ms, ok, bad, hydrate_ms / max(len(got), 1)))
    return {'ok': ok, 'bad': bad, 'ms': ms, 'mismatches': mismatches,
            'hydrate_ms_per_record': round(hydrate_ms / max(len(got), 1), 4)}


def decode_payload(payload):
    import struct

    def one(buf, i):
        tag = buf[i:i + 1]
        i += 1
        if tag == b'N':
            return None, i
        if tag == b'M':
            return hydrate.MISSING, i
        if tag == b'T':
            return True, i
        if tag == b'F':
            return False, i
        if tag == b'i':
            return struct.unpack('>q', buf[i:i + 8])[0], i + 8
        if tag == b'd':
            return struct.unpack('>d', buf[i:i + 8])[0], i + 8
        if tag == b's':
            n = struct.unpack('>I', buf[i:i + 4])[0]
            i += 4
            return buf[i:i + n].decode('utf-8'), i + n
        if tag == b'a':
            n = struct.unpack('>I', buf[i:i + 4])[0]
            i += 4
            out = []
            for _ in range(n):
                v, i = one(buf, i)
                out.append(v)
            return out, i
        if tag == b'o':
            n = struct.unpack('>I', buf[i:i + 4])[0]
            i += 4
            out = {}
            for _ in range(n):
                k, i = one(buf, i)
                v, i = one(buf, i)
                out[k] = v
            return out, i
        raise SystemExit('bad tag %r' % tag)

    body, i, out = payload[6:], 0, []
    while i < len(body):
        value, i = one(body, i)
        out.append(value)
    return tuple(out)


def preflight():
    import shutil
    import subprocess

    t0 = time.time()
    log('preflight enter')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    import common
    from advisor.reference import packs
    checks = []

    def check(name, ok, detail):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})
        log('preflight %-24s %s  %s' % (name, 'PASS' if ok else 'FAIL', detail))

    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=root,
                           capture_output=True, text=True).stdout.strip()
    check('git_clean', dirty == '', dirty.replace('\n', '; ') or 'clean')
    ver = io.open(os.path.join(root, 'VERSION'), encoding='utf-8').read().strip()
    tagver = subprocess.run(['git', 'show', 'pre-migration:VERSION'], cwd=root,
                            capture_output=True, text=True).stdout.strip()
    check('version_bumped', ver != tagver, '%s (pre-migration %s)' % (ver, tagver))
    free_gb = shutil.disk_usage('D:\\').free / 2 ** 30
    check('d_free_100gb', free_gb >= 100, '%.0f GB free' % free_gb)
    pgpass = os.path.join(os.environ['APPDATA'], 'postgresql', 'pgpass.conf')
    check('pgpass_present', os.path.exists(pgpass), pgpass)
    old_port = pg.PORT
    try:
        pg.PORT = 55432
        with pg.connect(app_name='tw-preflight', readonly=True, autocommit=True) as con:
            n = con.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE usename='tw'"
                " AND backend_type='client backend' AND pid <> pg_backend_pid()"
            ).fetchone()[0]
            check('c_reachable', True, 'tw@55432')
            check('stack_down', n == 0, '%d other tw backends on 55432' % n)
    except Exception as e:
        check('c_reachable', False, repr(e)[:120])
    finally:
        pg.PORT = old_port
    try:
        pg.PORT = 55433
        with pg.connect(app_name='tw-preflight', readonly=True, autocommit=True) as con:
            con.execute('SELECT 1').fetchone()
            check('d_reachable', True, 'tw@55433')
    except Exception as e:
        check('d_reachable', False, repr(e)[:120])
    finally:
        pg.PORT = old_port
    check('harness_off', os.path.exists(common.HARNESS_OFF), common.HARNESS_OFF)
    loc = [p for p in os.listdir(packs.DATA_DIR)
           if p.startswith('local_en') and p.endswith('.pack')] \
        if os.path.isdir(packs.DATA_DIR) else []
    for name, path in (
            ('ref_db_pack', os.path.join(packs.DATA_DIR, 'db.pack')),
            ('ref_schema_ron', packs.SCHEMA_RON),
            ('ref_game_exe', os.path.join(packs.GAME_DIR, 'Warhammer3.exe'))):
        check(name, os.path.exists(path), path)
    check('ref_loc_pack', len(loc) > 0, ','.join(loc[:3]) or 'no local_en*.pack')
    ok = all(c['ok'] for c in checks)
    out = {'ts': time.time(), 'ok': ok, 'checks': checks}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preflight.json')
    io.open(path, 'w', encoding='utf-8', newline='\n').write(json.dumps(out, indent=2) + '\n')
    log('preflight exit %.0f ms %s' % ((time.time() - t0) * 1000, 'CLEAN' if ok else 'FAILED'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', default='100:7')
    ap.add_argument('--sync-commit', default='off', choices=['on', 'off'])
    ap.add_argument('--preflight', action='store_true')
    args = ap.parse_args()
    if args.preflight:
        raise SystemExit(preflight())
    modulus, residue = (int(x) for x in args.sample.split(':'))

    t0 = time.time()
    log('dry run enter sample=%s port=%s' % (args.sample, os.environ.get('TW_PG_PORT')))
    out = {'sample': args.sample, 'ts': time.time()}
    with pg.connect() as con:
        con.execute("SET statement_timeout='1800s'")
        out['S3_schema_ms'] = stage_schema(con)
        con.commit()
        con.execute(PROBE_DDL)
        con.commit()
        rows, out['read_ms'] = fetch_sample(con, modulus, residue)
        records, sets, encode_ms = stage_encode(rows)
        out['A1_encode_ms_per_record'] = round(encode_ms / max(len(rows), 1), 4)
        out['records'] = len(rows)
        out['copy'] = stage_copy(con, records, sets, args.sync_commit)
        checkpoint(con, 'M1', 'done', out['records'], out['copy']['rows'])
        con.commit()
        out['V1'] = stage_verify(con, rows)
        checkpoint(con, 'V1', 'done' if out['V1']['bad'] == 0 else 'failed', out['records'], out['V1']['ok'])
        con.commit()
    out['A2_hydrate_ms_per_record'] = out['V1']['hydrate_ms_per_record']
    out['A5_copy_rows_per_s'] = out['copy']['rows_per_s']
    out['total_ms'] = (time.time() - t0) * 1000
    out['projected_full_run_min'] = round(out['total_ms'] * modulus / 1000.0 / 60.0, 1)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dryrun.json')
    io.open(path, 'w', encoding='utf-8', newline='\n').write(json.dumps(out, indent=2) + '\n')
    log('dry run exit %.0f ms  V1 ok=%d bad=%d  projected full run %.1f min'
        % (out['total_ms'], out['V1']['ok'], out['V1']['bad'], out['projected_full_run_min']))


if __name__ == '__main__':
    main()
