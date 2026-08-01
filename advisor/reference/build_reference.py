r"""Decode db.pack + local_en.pack into reference.sqlite (loc, buildings, tech, units, skills,
rituals, captive options/binding), driven by the vendored schema_db.json.

    python build_reference.py      # builds ./reference.sqlite + prints verified joins
"""
import json
import os
import re
import sqlite3
import struct

import zstandard as zstd

GAME = r"D:/SteamLibrary/steamapps/common/Total War WARHAMMER III/data"
HAS_INDEX_WITH_TIMESTAMPS = 0x40

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_JSON = os.path.join(HERE, "schema_db.json")

GUID_MARKER = b"\xfd\xfe\xfc\xff"
VERSION_MARKER = b"\xfc\xfd\xfe\xff"


def parse_pack(path):
    """PFH5 reader -> ([(name, offset, size, compressed)], raw_bytes)."""
    d = open(path, "rb").read()
    assert d[:4] == b"PFH5", d[:4]
    bitmask, _pack_count, pidx, fcount, fidx = struct.unpack_from("<5I", d, 4)
    flags = bitmask & ~15
    fp = 28 + pidx
    ents = []
    for _ in range(fcount):
        size = struct.unpack_from("<I", d, fp)[0]; fp += 4
        if flags & HAS_INDEX_WITH_TIMESTAMPS:
            fp += 4
        comp = d[fp]; fp += 1
        end = d.index(b"\x00", fp); name = d[fp:end].decode("latin-1"); fp = end + 1
        ents.append((name, size, comp))
    off = 28 + pidx + fidx
    out = []
    for name, size, comp in ents:
        out.append((name, off, size, comp)); off += size
    return out, d


def read_file(files, d, name):
    for n, off, size, comp in files:
        if n == name:
            b = d[off:off + size]
            if comp:
                dsz = struct.unpack_from("<I", b, 0)[0]
                return zstd.ZstdDecompressor().decompress(b[4:], max_output_size=dsz)
            return b
    return None


def decode_loc(b):
    """BOM(2) + 'LOC'(3) + pad(1) + u32 ver + u32 count + entries[u16 klen, utf16 key, u16 vlen, utf16 val, u8]."""
    assert b[0:2] == b"\xff\xfe" and b[2:5] == b"LOC"
    count = struct.unpack_from("<I", b, 10)[0]
    p = 14
    out = {}
    for _ in range(count):
        kl = struct.unpack_from("<H", b, p)[0]; p += 2
        k = b[p:p + kl * 2].decode("utf-16-le"); p += kl * 2
        vl = struct.unpack_from("<H", b, p)[0]; p += 2
        v = b[p:p + vl * 2].decode("utf-16-le", errors="replace"); p += vl * 2
        p += 1
        out[k] = v
    return out


class _Reader:
    """Little-endian cursor over a decoded DB table body."""

    def __init__(self, b, p=0):
        self.b = b
        self.p = p

    def u16(self):
        v = struct.unpack_from("<H", self.b, self.p)[0]; self.p += 2; return v

    def i16(self):
        v = struct.unpack_from("<h", self.b, self.p)[0]; self.p += 2; return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4; return v

    def i64(self):
        v = struct.unpack_from("<q", self.b, self.p)[0]; self.p += 8; return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.p)[0]; self.p += 4; return v

    def f32(self):
        v = struct.unpack_from("<f", self.b, self.p)[0]; self.p += 4; return v

    def f64(self):
        v = struct.unpack_from("<d", self.b, self.p)[0]; self.p += 8; return v

    def boolean(self):
        v = self.b[self.p]; self.p += 1; return bool(v)

    def s_u8(self):
        n = self.u16(); s = self.b[self.p:self.p + n].decode("utf-8", "replace"); self.p += n; return s

    def opt_s_u8(self):
        flag = self.b[self.p]; self.p += 1
        return self.s_u8() if flag else ""

    def s_u16(self):
        n = self.u16(); s = self.b[self.p:self.p + n * 2].decode("utf-16-le", "replace"); self.p += n * 2; return s

    def opt_s_u16(self):
        flag = self.b[self.p]; self.p += 1
        return self.s_u16() if flag else ""


def _read_field(r, ft):
    if ft == "StringU8": return r.s_u8()
    if ft == "OptionalStringU8": return r.opt_s_u8()
    if ft == "StringU16": return r.s_u16()
    if ft == "OptionalStringU16": return r.opt_s_u16()
    if ft == "Boolean": return r.boolean()
    if ft == "I16": return r.i16()
    if ft == "I32": return r.i32()
    if ft == "I64": return r.i64()
    if ft == "F32": return round(r.f32(), 4)
    if ft == "F64": return round(r.f64(), 4)
    if ft == "ColourRGB":
        v = r.u32(); return "#%06X" % (v & 0xFFFFFF)
    if ft == "OptionalI32":
        flag = r.b[r.p]; r.p += 1; return r.i32() if flag else None
    raise ValueError("unknown field_type " + ft)


def parse_db_header(b):
    """RPFM-DB header ([GUID]? [VERSION]? flag byte, u32 row_count) -> (guid, version, rows, offset)."""
    p = 0
    guid = None
    if b[0:4] == GUID_MARKER:
        p = 4
        n = struct.unpack_from("<H", b, p)[0]; p += 2
        guid = b[p:p + n * 2].decode("utf-16-le", "replace"); p += n * 2
    version = 0
    if b[p:p + 4] == VERSION_MARKER:
        p += 4
        version = struct.unpack_from("<I", b, p)[0]; p += 4
    p += 1  # 1-byte flag between (optional) version and the row count
    row_count = struct.unpack_from("<I", b, p)[0]; p += 4
    return guid, version, row_count, p


def load_db_schema(path=SCHEMA_JSON):
    """Vendored trimmed schema: {table: {version_str: [[field_name, field_type], ...]}}."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def decode_db_table(files, d, table, schema):
    """Decode every db/<table>/* file at its pack-declared version -> (rows, meta)."""
    entries = [(n, off, sz, c) for (n, off, sz, c) in files
               if n.replace("\\", "/").startswith("db/%s/" % table)]
    meta = {"files": len(entries), "rows": 0, "version": None, "ok": False, "reason": ""}
    if not entries:
        meta["reason"] = "no files in pack"
        return [], meta
    versions = schema.get(table)
    if not versions:
        meta["reason"] = "no schema for table"
        return [], meta

    out = []
    for (n, off, sz, c) in entries:
        b = read_file(files, d, n)
        guid, version, row_count, p = parse_db_header(b)
        meta["version"] = version
        fields = versions.get(str(version))
        if fields is None:
            meta["reason"] = "pack version %d not in schema %s" % (version, sorted(int(k) for k in versions))
            return [], meta
        r = _Reader(b, p)
        start = len(out)
        try:
            for _ in range(row_count):
                out.append({nm: _read_field(r, ft) for nm, ft in fields})
        except Exception as e:
            meta["reason"] = "decode fail at row %d: %s" % (len(out) - start, e)
            return [], meta
        if r.p != len(b):
            meta["reason"] = "not CLEAN-EOF (consumed %d/%d)" % (r.p, len(b))
            return [], meta

    meta["rows"] = len(out)
    meta["ok"] = True
    meta["reason"] = "CLEAN-EOF"
    return out, meta


_CAPTIVE_BUTTON = {"kill": "kill", "release": "release", "enslave": "enslave",
                   "enslave_slaves_only": "enslave", "enslave_replenishment_only": "enslave"}
_CAPTIVE_ROLE = re.compile(r"^[A-Z][A-Z_]*$")
# criteria-junction column layout: (entity_type, value_index, member_index); role is always col2
_CAPTIVE_CRIT = {"campaign_group_member_criteria_cultures_tables": ("culture", 0, 1),
                 "campaign_group_member_criteria_factions_tables": ("faction", 0, 1),
                 "campaign_group_member_criteria_subcultures_tables": ("subculture", 1, 0)}


def _captive_reference(cur, files, d, schema, report):
    """Build `captive_options` (record_key -> option name) and `captive_binding`
    ((entity_type, entity_key, button) -> record_key)."""
    def loc(rk):
        """On-screen captive-option name for a record key, following {{tr:<loc_key>}} redirects."""
        row = cur.execute("SELECT text FROM loc WHERE key=?",
                          ("campaign_post_battle_captive_options_onscreen_name_%s" % rk,)).fetchone()
        v = row[0] if row else None
        for _ in range(5):
            if not v or not v.startswith("{{tr:"):
                break
            m = re.match(r"\{\{tr:([\w.]+)\}\}", v)
            if not m:
                break
            row = cur.execute("SELECT text FROM loc WHERE key=?", (m.group(1),)).fetchone()
            v = row[0] if row else None
        return v

    opts, ometa = decode_db_table(files, d, "campaign_post_battle_captive_options_tables", schema)
    report["campaign_post_battle_captive_options_tables"] = ometa
    if not ometa["ok"]:
        return
    key2recs = {}          # option KEY -> [(record_key, outcome)]
    cur.execute("DROP TABLE IF EXISTS captive_options")
    cur.execute("CREATE TABLE captive_options (record_key TEXT PRIMARY KEY, option_key TEXT, "
                "outcome TEXT, onscreen_name TEXT)")
    for r in opts:
        rk = r["record_key"]
        key2recs.setdefault(r["key"], []).append((rk, r["outcome"]))
        cur.execute("INSERT OR REPLACE INTO captive_options VALUES (?,?,?,?)",
                    (rk, r["key"], r["outcome"], loc(rk)))
    option_keys = set(key2recs)

    mem, mmeta = decode_db_table(files, d, "campaign_group_members_tables", schema)
    report["campaign_group_members_tables"] = mmeta
    grp2mem, member_universe = {}, set()
    for r in mem:
        g, m = r["campaign_group"], r["campaign_group_member"]
        grp2mem.setdefault(g, set()).add(m)
        member_universe.add(g); member_universe.add(m)

    member_crit = {}       # campaign_group_member -> [(entity_type, entity_value, role)]
    for t, (typ, vi, mi) in _CAPTIVE_CRIT.items():
        rows, cmeta = decode_db_table(files, d, t, schema)
        report[t] = cmeta
        for r in rows:
            vals = (r["col0"], r["col1"], r["col2"])
            if not _CAPTIVE_ROLE.match(vals[2]):
                continue
            member_crit.setdefault(vals[mi], []).append((typ, vals[vi], vals[2]))

    def originators(g, maxdepth=6):
        """Transitive ORIGINATOR (captor) entity criteria reachable from an option group, each with
        a `conditional` flag (its member also carries a non-ORIGINATOR role)."""
        seen, res, stack = set(), [], [(g, 0)]
        while stack:
            node, dep = stack.pop()
            if node in seen or dep > maxdepth:
                continue
            seen.add(node)
            crits = member_crit.get(node, ())
            has_other = any(role != "ORIGINATOR" for (_, _, role) in crits)
            for (etype, val, role) in crits:
                if role == "ORIGINATOR":
                    res.append((etype, val, has_other))
            for m in grp2mem.get(node, ()):
                stack.append((m, dep + 1))
        return res

    # (entity_type, entity_key, button) -> best (rank, record_key)
    best = {}
    for g in option_keys:
        for (etype, ekey, cond) in originators(g):
            for (rk, outcome) in key2recs[g]:
                button = _CAPTIVE_BUTTON.get(outcome)
                if button is None:
                    continue
                try:
                    rk_int = int(rk)
                except (TypeError, ValueError):
                    rk_int = 0
                rank = (0 if outcome == button else 1, 1 if cond else 0,
                        1 if "_to_" in g else 0, rk_int)
                k = (etype, ekey, button)
                if k not in best or rank < best[k][0]:
                    best[k] = (rank, rk)

    cur.execute("DROP TABLE IF EXISTS captive_binding")
    cur.execute("CREATE TABLE captive_binding (entity_type TEXT, entity_key TEXT, button TEXT, "
                "record_key TEXT, PRIMARY KEY (entity_type, entity_key, button))")
    for (etype, ekey, button), (rank, rk) in best.items():
        cur.execute("INSERT OR REPLACE INTO captive_binding VALUES (?,?,?,?)",
                    (etype, ekey, button, rk))
    report["_written"]["captive_options"] = len(opts)
    report["_written"]["captive_binding"] = len(best)


def decode_db_tables(con, files, d, schema, report):
    """Decode the advisor-relevant db tables into `con`, filling `report` with per-table outcomes."""
    cur = con.cursor()

    def src(table):
        rows, meta = decode_db_table(files, d, table, schema)
        report[table] = meta
        return rows, meta

    chains, _ = src("building_chains_tables")
    cur.execute("DROP TABLE IF EXISTS building_chains")
    cur.execute("CREATE TABLE building_chains (key TEXT PRIMARY KEY, superchain TEXT, "
                "chain_category TEXT, sort_order INTEGER)")
    for r in chains:
        cur.execute("INSERT OR REPLACE INTO building_chains VALUES (?,?,?,?)",
                    (r["key"], r.get("building_superchain"), r.get("chain_category"),
                     r.get("optional_sort_order")))
    report["_written"] = report.get("_written", {})
    report["_written"]["building_chains"] = len(chains)

    blevels, _ = src("building_levels_tables")
    cur.execute("DROP TABLE IF EXISTS buildings")
    cur.execute("CREATE TABLE buildings (key TEXT PRIMARY KEY, building_chain TEXT, level INTEGER, "
                "create_cost INTEGER, create_time INTEGER, upkeep_cost INTEGER, food_cost INTEGER, "
                "dev_point_cost INTEGER, building_instance_key TEXT)")
    for r in blevels:
        cur.execute("INSERT OR REPLACE INTO buildings VALUES (?,?,?,?,?,?,?,?,?)", (
            r["level_name"], r.get("chain"), r.get("level"),
            r.get("create_cost"), r.get("create_time"), r.get("upkeep_cost"),
            r.get("food_cost"), r.get("development_point_cost"), r.get("building_instance_key")))
    report["_written"]["buildings"] = len(blevels)

    nodes, nmeta = src("technology_nodes_tables")
    techs, tmeta = src("technologies_tables")
    tech_by_key = {t["key"]: t for t in techs}
    cur.execute("DROP TABLE IF EXISTS tech")
    cur.execute("CREATE TABLE tech (key TEXT PRIMARY KEY, technology_key TEXT, node_set TEXT, "
                "tier INTEGER, research_points_required INTEGER, cost_per_round INTEGER, "
                "food_cost INTEGER, required_parents INTEGER, building_level TEXT, "
                "is_civil INTEGER, is_engineering INTEGER, is_military INTEGER, is_hidden INTEGER)")
    written = set()
    for r in nodes:
        tk = r.get("technology_key")
        t = tech_by_key.get(tk, {})
        cur.execute("INSERT OR REPLACE INTO tech VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            r["key"], tk, r.get("technology_node_set"), r.get("tier"),
            r.get("research_points_required"), r.get("cost_per_round"), r.get("food_cost"),
            r.get("required_parents"), t.get("building_level"),
            int(bool(t.get("is_civil"))), int(bool(t.get("is_engineering"))),
            int(bool(t.get("is_military"))), int(bool(t.get("is_hidden")))))
        written.add(r["key"])
    node_tks = {r.get("technology_key") for r in nodes} | written
    for tk, t in tech_by_key.items():
        if tk in node_tks:
            continue
        cur.execute("INSERT OR REPLACE INTO tech VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            tk, tk, None, None, None, None, None, None, t.get("building_level"),
            int(bool(t.get("is_civil"))), int(bool(t.get("is_engineering"))),
            int(bool(t.get("is_military"))), int(bool(t.get("is_hidden")))))
    report["_written"]["tech"] = cur.execute("SELECT COUNT(*) FROM tech").fetchone()[0]

    mains, mmeta = src("main_units_tables")
    lands, lmeta = src("land_units_tables")
    land_by_key = {r["key"]: r for r in lands}
    cur.execute("DROP TABLE IF EXISTS units")
    cur.execute("CREATE TABLE units (key TEXT PRIMARY KEY, land_unit TEXT, caste TEXT, "
                "category TEXT, class TEXT, recruitment_cost INTEGER, upkeep_cost INTEGER, "
                "create_time INTEGER, food_cost INTEGER, multiplayer_cost INTEGER, tier INTEGER, "
                "num_men INTEGER, is_naval INTEGER, ui_unit_group_land TEXT)")
    for r in mains:
        lu = land_by_key.get(r.get("land_unit"), {})
        cur.execute("INSERT OR REPLACE INTO units VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            r["unit"], r.get("land_unit"), r.get("caste"),
            lu.get("category"), lu.get("class"),
            r.get("recruitment_cost"), r.get("upkeep_cost"), r.get("create_time"),
            r.get("food_cost"), r.get("multiplayer_cost"), r.get("tier"),
            r.get("num_men"), int(bool(r.get("is_naval"))), r.get("ui_unit_group_land")))
    report["_written"]["units"] = len(mains)

    skills, smeta = src("character_skills_tables")
    if smeta["ok"]:
        cur.execute("DROP TABLE IF EXISTS skills")
        cur.execute("CREATE TABLE skills (key TEXT PRIMARY KEY, unlocked_at_rank INTEGER, "
                    "influence_cost INTEGER, is_background_skill INTEGER, background_weighting REAL)")
        for r in skills:
            cur.execute("INSERT OR REPLACE INTO skills VALUES (?,?,?,?,?)", (
                r["key"], r.get("unlocked_at_rank"), r.get("influence_cost"),
                int(bool(r.get("is_background_skill"))), r.get("background_weighting")))
        report["_written"]["skills"] = len(skills)

    rituals, rmeta = src("rituals_tables")
    if rmeta["ok"]:
        cur.execute("DROP TABLE IF EXISTS rituals")
        cur.execute("CREATE TABLE rituals (key TEXT PRIMARY KEY, category TEXT, cast_time INTEGER, "
                    "cooldown_time INTEGER, slave_cost INTEGER, influence_cost INTEGER, "
                    "required_resources TEXT, expended_resources TEXT)")
        for r in rituals:
            cur.execute("INSERT OR REPLACE INTO rituals VALUES (?,?,?,?,?,?,?,?)", (
                r["key"], r.get("category"), r.get("cast_time"), r.get("cooldown_time"),
                r.get("slave_cost"), r.get("influence_cost"),
                r.get("required_resources"), r.get("expended_resources")))
        report["_written"]["rituals"] = len(rituals)

    _captive_reference(cur, files, d, schema, report)

    con.commit()


def build():
    out = r"D:\twdata\reference\reference.sqlite"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    con = sqlite3.connect(out)
    cur = con.cursor()

    lfiles, ld = parse_pack(GAME + "/local_en.pack")
    cur.execute("DROP TABLE IF EXISTS loc")
    cur.execute("CREATE TABLE loc (key TEXT PRIMARY KEY, text TEXT)")
    n = 0
    for name, off, size, comp in lfiles:
        if name.endswith(".loc"):
            b = read_file(lfiles, ld, name)
            try:
                for k, v in decode_loc(b).items():
                    cur.execute("INSERT OR REPLACE INTO loc VALUES (?,?)", (k, v)); n += 1
            except Exception as e:
                print(f"  skip {name}: {e}")
    con.commit()
    print(f"loc: {n} entries from {sum(1 for f in lfiles if f[0].endswith('.loc'))} .loc files")

    schema = load_db_schema()
    dfiles, dd = parse_pack(GAME + "/db.pack")
    report = {}
    decode_db_tables(con, dfiles, dd, schema, report)

    print("\n-- db feature tables written --")
    for t, cnt in sorted(report.get("_written", {}).items()):
        print(f"  {t:16s} {cnt} rows")
    print("\n-- source decode outcomes --")
    for t, m in report.items():
        if t == "_written":
            continue
        tag = "OK" if m["ok"] else "SKIP"
        print(f"  [{tag}] {t:38s} ver={m['version']} rows={m['rows']} files={m['files']} :: {m['reason']}")

    _verify(cur)
    con.close()


def _verify(cur):
    print("\n== VERIFIED REAL JOINS ==")

    def one(sql, args=()):
        return cur.execute(sql, args).fetchone()

    b = one("SELECT building_chain, level, create_cost, create_time, upkeep_cost, food_cost, "
            "dev_point_cost FROM buildings WHERE key=?", ("wh2_main_hef_resource_marble_1",))
    nm = one("SELECT text FROM loc WHERE key=?", ("building_culture_variants_name_wh2_main_hef_resource_marble_1",))
    print(f"  building marble_1 {nm and nm[0]!r}: chain={b[0]} level={b[1]} create_cost={b[2]} "
          f"create_time={b[3]} upkeep={b[4]} food={b[5]} dev={b[6]}")

    t = one("SELECT node_set, tier, research_points_required, cost_per_round, food_cost, "
            "required_parents, building_level, is_military FROM tech WHERE key=?", ("wh2_main_tech_hef_0_00",))
    tn = one("SELECT text FROM loc WHERE key=?", ("technologies_onscreen_name_wh2_main_tech_hef_0_00",))
    print(f"  tech hef_0_00 {tn and tn[0]!r}: node_set={t[0]} tier={t[1]} research_pts={t[2]} "
          f"cost/round={t[3]} food={t[4]} parents={t[5]} unlocks_building={t[6]!r} is_military={t[7]}")

    u = one("SELECT caste, category, class, recruitment_cost, upkeep_cost, create_time, tier, num_men "
            "FROM units WHERE key=?", ("wh2_main_hef_inf_spearmen_0",))
    un = one("SELECT text FROM loc WHERE key=?", ("land_units_onscreen_name_wh2_main_hef_inf_spearmen_0",))
    print(f"  unit spearmen {un and un[0]!r}: caste={u[0]} category={u[1]} class={u[2]} "
          f"recruit={u[3]} upkeep={u[4]} create_time={u[5]} tier={u[6]} num_men={u[7]}")

    for tbl, key in (("skills", None), ("rituals", None)):
        row = one(f"SELECT * FROM {tbl} LIMIT 1")
        cnt = one(f"SELECT COUNT(*) FROM {tbl}")[0]
        print(f"  {tbl}: {cnt} rows; sample={row}")

    for label, etype, ekey in (("High Elves", "culture", "wh2_main_hef_high_elves"),
                               ("Slaanesh/Masque", "faction", "wh3_dlc27_sla_masque_of_slaanesh"),
                               ("Slaanesh (culture)", "culture", "wh3_main_sla_slaanesh")):
        got = {}
        for button in ("kill", "enslave", "release"):
            r = one("SELECT o.onscreen_name FROM captive_binding b JOIN captive_options o "
                    "ON o.record_key=b.record_key WHERE b.entity_type=? AND b.entity_key=? "
                    "AND b.button=?", (etype, ekey, button))
            got[button] = r[0] if r else None
        print(f"  captives {label:20s}: kill={got['kill']!r} enslave={got['enslave']!r} "
              f"release={got['release']!r}")


if __name__ == "__main__":
    build()
