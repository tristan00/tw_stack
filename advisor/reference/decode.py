from __future__ import annotations

import struct
import sys
import time

GUID_MARKER = b'\xfd\xfe\xfc\xff'
VERSION_MARKER = b'\xfc\xfd\xfe\xff'


def log(msg):
    sys.stderr.write('%.3f  decode %s\n' % (time.time(), msg))


class Reader:

    __slots__ = ('b', 'p')

    def __init__(self, b, p=0):
        self.b = b
        self.p = p

    def u16(self):
        v = struct.unpack_from('<H', self.b, self.p)[0]
        self.p += 2
        return v

    def u32(self):
        v = struct.unpack_from('<I', self.b, self.p)[0]
        self.p += 4
        return v

    def s_u8(self):
        n = self.u16()
        s = self.b[self.p:self.p + n].decode('utf-8', 'replace')
        self.p += n
        return s

    def s_u16(self):
        n = self.u16()
        s = self.b[self.p:self.p + n * 2].decode('utf-16-le', 'replace')
        self.p += n * 2
        return s


def read_field(r, ft):
    b, p = r.b, r.p
    if ft == 'StringU8':
        return r.s_u8()
    if ft == 'OptionalStringU8':
        flag = b[p]
        r.p = p + 1
        return r.s_u8() if flag else None
    if ft == 'StringU16':
        return r.s_u16()
    if ft == 'OptionalStringU16':
        flag = b[p]
        r.p = p + 1
        return r.s_u16() if flag else None
    if ft == 'Boolean':
        r.p = p + 1
        return b[p] != 0
    if ft == 'I16':
        r.p = p + 2
        return struct.unpack_from('<h', b, p)[0]
    if ft == 'I32':
        r.p = p + 4
        return struct.unpack_from('<i', b, p)[0]
    if ft == 'I64':
        r.p = p + 8
        return struct.unpack_from('<q', b, p)[0]
    if ft == 'F32':
        r.p = p + 4
        return struct.unpack_from('<f', b, p)[0]
    if ft == 'F64':
        r.p = p + 8
        return struct.unpack_from('<d', b, p)[0]
    if ft == 'ColourRGB':
        r.p = p + 4
        return struct.unpack_from('<I', b, p)[0] & 0xFFFFFF
    if ft == 'OptionalI32':
        flag = b[p]
        r.p = p + 1
        if not flag:
            return None
        v = struct.unpack_from('<i', b, r.p)[0]
        r.p += 4
        return v
    raise ValueError('unknown field_type %r' % ft)


def parse_header(b):
    p = 0
    guid = None
    if b[0:4] == GUID_MARKER:
        p = 4
        n = struct.unpack_from('<H', b, p)[0]
        p += 2
        guid = b[p:p + n * 2].decode('utf-16-le', 'replace')
        p += n * 2
    version = 0
    if b[p:p + 4] == VERSION_MARKER:
        p += 4
        version = struct.unpack_from('<I', b, p)[0]
        p += 4
    p += 1
    row_count = struct.unpack_from('<I', b, p)[0]
    p += 4
    return guid, version, row_count, p


def decode_table(blob, fields):
    guid, version, row_count, p = parse_header(blob)
    types = [f['field_type'] for f in fields]
    r = Reader(blob, p)
    rows = []
    for _ in range(row_count):
        rows.append(tuple(read_field(r, ft) for ft in types))
    return version, rows


def decode_loc(blob):
    if blob[0:2] != bytes([0xFF, 0xFE]) or blob[2:5] != b'LOC':
        raise ValueError('not a .loc file')
    count = struct.unpack_from('<I', blob, 10)[0]
    p = 14
    out = []
    for _ in range(count):
        kl = struct.unpack_from('<H', blob, p)[0]
        p += 2
        key = blob[p:p + kl * 2].decode('utf-16-le', 'replace')
        p += kl * 2
        vl = struct.unpack_from('<H', blob, p)[0]
        p += 2
        text = blob[p:p + vl * 2].decode('utf-16-le', 'replace')
        p += vl * 2 + 1
        out.append((key, text))
    return out
