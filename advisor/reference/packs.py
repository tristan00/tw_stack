from __future__ import annotations

import hashlib
import io
import os
import struct
import sys
import time

import zstandard as zstd

GAME_DIR = os.environ.get(
    'TW_GAME_DIR',
    r'D:\SteamLibrary\steamapps\common\Total War WARHAMMER III')
DATA_DIR = os.path.join(GAME_DIR, 'data')
SCHEMA_RON = os.environ.get(
    'TW_SCHEMA_RON', r'D:\twdata\reference\ui3_extraction\schema_wh3.ron')
APPMANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(GAME_DIR)), 'appmanifest_1142710.acf')
USED_MODS = os.path.join(GAME_DIR, 'used_mods.txt')

HAS_INDEX_WITH_TIMESTAMPS = 0x40

TYPE_ORDER = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}


def log(msg):
    sys.stderr.write('%.3f  packs %s\n' % (time.time(), msg))


def read_index(path):
    with open(path, 'rb') as fh:
        head = fh.read(28)
        if head[:4] != b'PFH5':
            return None, []
        bitmask, _pack_count, pidx, fcount, fidx = struct.unpack_from('<5I', head, 4)
        pack_type = bitmask & 15
        flags = bitmask & ~15
        fh.seek(28 + pidx)
        blob = fh.read(fidx)
    ents, fp = [], 0
    for _ in range(fcount):
        size = struct.unpack_from('<I', blob, fp)[0]
        fp += 4
        if flags & HAS_INDEX_WITH_TIMESTAMPS:
            fp += 4
        comp = blob[fp]
        fp += 1
        end = blob.index(b'\x00', fp)
        name = blob[fp:end].decode('latin-1').replace(chr(92), '/')
        fp = end + 1
        ents.append((name, size, comp))
    off = 28 + pidx + fidx
    out = []
    for name, size, comp in ents:
        out.append((name, off, size, comp))
        off += size
    return pack_type, out


def enabled_mods():
    if not os.path.exists(USED_MODS):
        return set()
    names = set()
    for line in io.open(USED_MODS, encoding='utf-8', errors='replace'):
        line = line.strip().strip('"')
        if line:
            names.add(os.path.basename(line))
    return names


def discover():
    t0 = time.time()
    log('discover enter %s' % DATA_DIR)
    mods = enabled_mods()
    found = []
    for name in sorted(os.listdir(DATA_DIR)):
        if not name.lower().endswith('.pack'):
            continue
        path = os.path.join(DATA_DIR, name)
        pack_type, index = read_index(path)
        if pack_type is None:
            continue
        if pack_type == 3 and name not in mods:
            continue
        st = os.stat(path)
        found.append({'name': name, 'path': path, 'type': pack_type,
                      'size': st.st_size, 'mtime': st.st_mtime, 'index': index})
    found.sort(key=lambda p: (TYPE_ORDER.get(p['type'], 9), p['name']))
    log('discover exit %.0f ms  %d packs, %d with db/, %d with .loc'
        % ((time.time() - t0) * 1000, len(found),
           sum(1 for p in found if any(e[0].startswith('db/') for e in p['index'])),
           sum(1 for p in found if any(e[0].endswith('.loc') for e in p['index']))))
    return found


def extract(path, entry):
    name, off, size, comp = entry
    with open(path, 'rb') as fh:
        fh.seek(off)
        b = fh.read(size)
    if comp:
        dsz = struct.unpack_from('<I', b, 0)[0]
        return zstd.ZstdDecompressor().decompress(b[4:], max_output_size=dsz)
    return b


def sha256_file(path, chunk=8 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def game_build_id():
    if not os.path.exists(APPMANIFEST):
        return None
    for line in io.open(APPMANIFEST, encoding='utf-8', errors='replace'):
        if '"buildid"' in line:
            return line.split('"')[-2]
    return None


def game_version():
    try:
        import win32api
        info = win32api.GetFileVersionInfo(os.path.join(GAME_DIR, 'Warhammer3.exe'), '\\')
        ms, ls = info['FileVersionMS'], info['FileVersionLS']
        return '%d.%d.%d.%d' % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None


def contributes(p):
    return any(e[0].startswith('db/') or e[0].endswith('.loc') for e in p['index'])


def fingerprint(found, previous=None):
    t0 = time.time()
    found = [p for p in found if contributes(p)]
    log('fingerprint enter %d contributing packs' % len(found))
    cheap = hashlib.sha256()
    for p in found:
        cheap.update(('%s|%d|%.6f' % (p['name'], p['size'], p['mtime'])).encode())
    cheap_hex = cheap.hexdigest()
    prev_cheap = (previous or {}).get('cheap')
    if previous and prev_cheap == cheap_hex:
        log('fingerprint exit %.0f ms  size/mtime unchanged, sha reused'
            % ((time.time() - t0) * 1000))
        return {'cheap': cheap_hex, 'full': previous.get('full'),
                'schema': previous.get('schema'), 'build_id': previous.get('build_id'),
                'version': previous.get('version')}
    full = hashlib.sha256()
    for p in found:
        full.update(('%s|%d|%.6f|' % (p['name'], p['size'], p['mtime'])).encode())
        full.update(sha256_file(p['path']).encode())
    schema_hex = sha256_file(SCHEMA_RON)
    full.update(schema_hex.encode())
    ver, build = game_version(), game_build_id()
    full.update((str(ver) + '|' + str(build)).encode())
    log('fingerprint exit %.0f ms  full sha computed' % ((time.time() - t0) * 1000))
    return {'cheap': cheap_hex, 'full': full.hexdigest(), 'schema': schema_hex,
            'build_id': build, 'version': ver}


if __name__ == '__main__':
    packs = discover()
    for p in packs[:5]:
        log('%-34s type=%d %6.1f MB %5d entries'
            % (p['name'], p['type'], p['size'] / 1e6, len(p['index'])))
    db = [p for p in packs if any(e[0].startswith('db/') for e in p['index'])]
    log('packs carrying db/: %s' % [p['name'] for p in db])
    log('total entries: %d' % sum(len(p['index']) for p in packs))
