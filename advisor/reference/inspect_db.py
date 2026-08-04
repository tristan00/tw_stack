r"""Confirm (not assume) what db.pack actually contains for the numeric option features."""
import re
import struct

import build_reference as B

files, d = B.parse_pack(B.GAME + "/db.pack")
tabs = {}
for name, off, size, comp in files:
    parts = name.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == "db":
        tabs.setdefault(parts[1], []).append((name, off, size, comp))

print("total db tables:", len(tabs))
for want in ("building_levels_tables", "technologies_tables", "main_units_tables",
             "land_units_tables", "unit_stats_land_tables"):
    print("  present:", want, want in tabs)


def dump(table):
    name, off, size, comp = tabs[table][0]
    b = B.read_file(files, d, name)
    print("\n== %s ==  raw_len=%d comp=%s" % (table, len(b), comp))
    print("head hex:", " ".join("%02x" % x for x in b[:44]))
    p = 0
    if b[:4] == b"\xfd\xfe\xfc\xff":
        ln = struct.unpack_from("<H", b, 4)[0]
        p = 6 + ln * 2
        print("  GUID present (len %d)" % ln)
    if b[p:p + 4] == b"\xfc\xfd\xfe\xff":
        ver = struct.unpack_from("<I", b, p + 4)[0]
        rc = struct.unpack_from("<I", b, p + 8)[0]
        print("  version=%d  ROWCOUNT=%d  (rows start @%d)" % (ver, rc, p + 12))
    else:
        print("  (no version marker at %d: %s)" % (p, b[p:p + 4].hex()))
    toks = re.findall(rb"[ -~]{4,}", b)
    print("  ascii tokens (first 16):", [t.decode("latin-1")[:34] for t in toks[:16]])


for t in ("building_levels_tables", "technologies_tables"):
    if t in tabs:
        dump(t)
