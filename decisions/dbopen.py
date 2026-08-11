from __future__ import annotations

"""One place that opens decisions.sqlite, because the schema needs two helper functions.

The store keeps its JSON blobs zlib-compressed and content-addressed, and its per-offer
scores packed as float32. The compatibility views (`decision_points`, `entity_snapshots`,
`action_offers`, `action_taken`) reconstruct the old flat columns out of those, which
means they call `unz()` and `f32()` -- and an application-defined SQLite function only
exists on the connection that registered it. Fifteen call sites used to spell
`sqlite3.connect("file:%s?mode=ro" % ...)` by hand; a view is invisible to all of them
unless they come through here.

    from decisions import dbopen
    con = dbopen.connect(path)                  # read-only by default
    con = dbopen.connect(path, readonly=False)  # writers
"""

import sqlite3
import zlib


ZRAW, ZDEFLATE = 0, 1


def pack(text, level=6):
    """Store whichever is smaller, tagged so the reader never has to guess.

    zlib costs ~11 bytes of header, and most entity states are shorter than that saves --
    at the toy end compressing a 65-byte state made it 75. Blob rows are dominated by
    small payloads by count and by big `world` blobs by bytes, so the store wants both
    behaviours and a one-byte tag buys them.
    """
    raw = text.encode("utf-8")
    z = zlib.compress(raw, level)
    if len(z) < len(raw):
        return bytes([ZDEFLATE]) + z
    return bytes([ZRAW]) + raw


def _unz(z):
    """A packed blob -> the text that was stored. NULL stays NULL."""
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
    """One float out of a packed float32 array. Out of range reads as NULL rather than
    raising, so a decision whose scores were pruned still selects."""
    if packed is None or i is None:
        return None
    import struct
    i = int(i)
    b = bytes(packed)
    if i < 0 or (i + 1) * 4 > len(b):
        return None
    v = struct.unpack_from("<f", b, i * 4)[0]
    return None if v != v else v          # NaN is "not recorded"


def register(con):
    """Teach a connection the two functions the views are written in terms of."""
    con.create_function("unz", 1, _unz)
    con.create_function("f32", 2, _f32)
    return con


def connect(path, readonly=True, timeout=10.0, uri=None):
    """Open decisions.sqlite with the view helpers registered."""
    p = str(path).replace("\\", "/")
    if uri or (readonly and not str(path).startswith("file:")):
        con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=timeout)
    elif str(path).startswith("file:"):
        con = sqlite3.connect(p, uri=True, timeout=timeout)
    else:
        con = sqlite3.connect(path, timeout=timeout)
    return register(con)
