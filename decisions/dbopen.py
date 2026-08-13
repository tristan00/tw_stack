from __future__ import annotations


import sqlite3
import zlib


ZRAW, ZDEFLATE = 0, 1


def pack(text, level=6):
    raw = text.encode("utf-8")
    z = zlib.compress(raw, level)
    if len(z) < len(raw):
        return bytes([ZDEFLATE]) + z
    return bytes([ZRAW]) + raw


def _unz(z):
    if z is None:
        return None
    try:
        b = bytes(z)
        if not b:
            return ""
        if b[0] == ZDEFLATE:
            return zlib.decompress(b[1:]).decode("utf-8")
        return b[1:].decode("utf-8")
    except (zlib.error, UnicodeDecodeError, TypeError, IndexError):
        return None


def _f32(packed, i):
    if packed is None or i is None:
        return None
    import struct
    i = int(i)
    b = bytes(packed)
    if i < 0 or (i + 1) * 4 > len(b):
        return None
    v = struct.unpack_from("<f", b, i * 4)[0]
    return None if v != v else v


def register(con):
    con.create_function("unz", 1, _unz)
    con.create_function("f32", 2, _f32)
    return con


def connect(path, readonly=True, timeout=10.0, uri=None):
    p = str(path).replace("\\", "/")
    if uri or (readonly and not str(path).startswith("file:")):
        con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=timeout)
    elif str(path).startswith("file:"):
        con = sqlite3.connect(p, uri=True, timeout=timeout)
    else:
        con = sqlite3.connect(path, timeout=timeout)
    return register(con)
