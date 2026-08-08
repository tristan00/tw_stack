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
    p += 1
    row_count = struct.unpack_from("<I", b, p)[0]; p += 4
    return guid, version, row_count, p


def load_db_schema(path=SCHEMA_JSON):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def decode_db_table(files, d, table, schema):
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
_CAPTIVE_CRIT = {"campaign_group_member_criteria_cultures_tables": ("culture", 0, 1),
                 "campaign_group_member_criteria_factions_tables": ("faction", 0, 1),
                 "campaign_group_member_criteria_subcultures_tables": ("subculture", 1, 0)}


def _agent_reference(cur, files, d, schema, report):
    def bail(table, rows, expect):
        if rows and not any(k in rows[0] for k in expect):
            print("  !! %s decoded with UNEXPECTED columns %s -- expected one of %s"
                  % (table, sorted(rows[0].keys()), expect))
            return True
        return False

    acts, ameta = decode_db_table(files, d, "agent_actions_tables", schema)
    report["agent_actions_tables"] = ameta
    if ameta["ok"] and not bail("agent_actions_tables", acts, ("unique_id",)):
        cur.execute("DROP TABLE IF EXISTS agent_actions")
        cur.execute("CREATE TABLE agent_actions (key TEXT PRIMARY KEY, agent TEXT, ability TEXT, "
                    "attribute TEXT, chance_of_success INTEGER, cannot_fail_result TEXT, "
                    "succeed_always INTEGER, crit_success_mod REAL, opportune_failure_mod REAL, "
                    "crit_failure_mod REAL, show_in_ui INTEGER, subculture TEXT, "
                    "loc_name TEXT)")
        for r in acts:
            cur.execute("INSERT OR REPLACE INTO agent_actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                r.get("unique_id"), r.get("agent"), r.get("ability"), r.get("attribute"),
                r.get("chance_of_success"), r.get("cannot_fail"),
                int(bool(r.get("succeed_always_override"))),
                r.get("critical_success_proportion_modifier"),
                r.get("opportune_failure_proportion_modifier"),
                r.get("critical_failure_proportion_modifier"),
                int(bool(r.get("show_action_info_in_ui"))), r.get("subculture"),
                r.get("localised_action_name")))
        report["_written"]["agent_actions"] = len(acts)

    types, tmeta = decode_db_table(files, d, "agents_tables", schema)
    report["agents_tables"] = tmeta
    if tmeta["ok"] and not bail("agents_tables", types, ("key",)):
        cur.execute("DROP TABLE IF EXISTS agent_types")
        cur.execute("CREATE TABLE agent_types (key TEXT PRIMARY KEY, move_points INTEGER, "
                    "faction_total_cap INTEGER, playable INTEGER)")
        for r in types:
            cur.execute("INSERT OR REPLACE INTO agent_types VALUES (?,?,?,?)", (
                r.get("key"), r.get("move_points"), r.get("faction_total_cap"),
                int(bool(r.get("playable")))))
        report["_written"]["agent_types"] = len(types)

    abil, bmeta = decode_db_table(files, d, "abilities_tables", schema)
    report["abilities_tables"] = bmeta
    if bmeta["ok"] and not bail("abilities_tables", abil, ("ability", "key")):
        cur.execute("DROP TABLE IF EXISTS agent_abilities")
        cur.execute("CREATE TABLE agent_abilities (key TEXT PRIMARY KEY, category TEXT)")
        for r in abil:
            cur.execute("INSERT OR REPLACE INTO agent_abilities VALUES (?,?)",
                        (r.get("ability") or r.get("key"), r.get("category")))
        report["_written"]["agent_abilities"] = len(abil)

    res, rmeta = decode_db_table(files, d, "action_results_tables", schema)
    report["action_results_tables"] = rmeta
    if rmeta["ok"] and not bail("action_results_tables", res, ("key",)):
        cur.execute("DROP TABLE IF EXISTS action_results")
        cur.execute("CREATE TABLE action_results (key TEXT PRIMARY KEY, actor_bundle TEXT, "
                    "target_bundle TEXT, actor_bundle_turns INTEGER, target_bundle_turns INTEGER)")
        for r in res:
            cur.execute("INSERT OR REPLACE INTO action_results VALUES (?,?,?,?,?)", (
                r.get("key"), r.get("actor_effect_bundle"), r.get("target_effect_bundle"),
                r.get("actor_effect_bundle_turns"), r.get("target_effect_bundle_turns")))
        report["_written"]["action_results"] = len(res)

    outc, ometa = decode_db_table(files, d, "action_results_additional_outcomes_tables", schema)
    report["action_results_additional_outcomes_tables"] = ometa
    if ometa["ok"] and not bail("action_results_additional_outcomes_tables", outc,
                                ("action_result_key",)):
        cur.execute("DROP TABLE IF EXISTS action_result_outcomes")
        cur.execute("CREATE TABLE action_result_outcomes (key TEXT PRIMARY KEY, "
                    "action_result_key TEXT, outcome TEXT, effect TEXT, effect_scope TEXT, "
                    "value REAL, affects_target INTEGER, advancement_stage TEXT)")
        for r in outc:
            cur.execute("INSERT OR REPLACE INTO action_result_outcomes VALUES (?,?,?,?,?,?,?,?)", (
                r.get("key"), r.get("action_result_key"), r.get("outcome"),
                r.get("effect_record"), r.get("effect_scope_record"), r.get("value"),
                int(bool(r.get("affects_target"))), r.get("advancement_stage")))
        report["_written"]["action_result_outcomes"] = len(outc)

    perm, pmeta = decode_db_table(files, d, "faction_agent_permitted_subtypes_tables", schema)
    report["faction_agent_permitted_subtypes_tables"] = pmeta
    if pmeta["ok"] and not bail("faction_agent_permitted_subtypes_tables", perm,
                                ("faction", "subtype")):
        cur.execute("DROP TABLE IF EXISTS agent_permitted_subtypes")
        cur.execute("CREATE TABLE agent_permitted_subtypes (faction TEXT, agent TEXT, "
                    "subtype TEXT, PRIMARY KEY (faction, agent, subtype))")
        for r in perm:
            cur.execute("INSERT OR REPLACE INTO agent_permitted_subtypes VALUES (?,?,?)",
                        (r.get("faction"), r.get("agent"), r.get("subtype")))
        report["_written"]["agent_permitted_subtypes"] = len(perm)


def _captive_reference(cur, files, d, schema, report):
    def loc(rk):
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
    key2recs = {}
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

    member_crit = {}
    for t, (typ, vi, mi) in _CAPTIVE_CRIT.items():
        rows, cmeta = decode_db_table(files, d, t, schema)
        report[t] = cmeta
        for r in rows:
            vals = (r["col0"], r["col1"], r["col2"])
            if not _CAPTIVE_ROLE.match(vals[2]):
                continue
            member_crit.setdefault(vals[mi], []).append((typ, vals[vi], vals[2]))

    def originators(g, maxdepth=6):
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
    _agent_reference(cur, files, d, schema, report)
    _merc_reference(cur, files, d, schema, report)

    con.commit()


def _merc_reference(cur, files, d, schema, report):
    def src(table):
        rows, meta = decode_db_table(files, d, table, schema)
        report[table] = meta
        return rows, meta

    groups, gmeta = src("mercenary_unit_groups_tables")
    junctions, jmeta = src("mercenary_pool_to_groups_junctions_tables")
    pools, pmeta = src("mercenary_pools_tables")
    if not (gmeta["ok"] and jmeta["ok"] and pmeta["ok"]):
        print("  !! merc_units NOT built -- groups=%s junctions=%s pools=%s"
              % (gmeta["reason"], jmeta["reason"], pmeta["reason"]))
        return
    group_by_key = {}
    for g in groups:
        group_by_key.setdefault(g["key"], []).append(g)
    flavor_by_pool = {p["key"]: p.get("ui_recruitment_info") or "" for p in pools}
    cur.execute("DROP TABLE IF EXISTS merc_units")
    cur.execute("CREATE TABLE merc_units (unit TEXT, pool TEXT, flavor TEXT, subculture TEXT, "
                "faction TEXT, tech TEXT, group_key TEXT, base_count INTEGER, max_count INTEGER, "
                "replenish_chance REAL)")
    n = 0
    orphans = set()
    for j in junctions:
        gs = group_by_key.get(j["group"])
        if not gs:
            orphans.add(j["group"])
            continue
        flavor = flavor_by_pool.get(j["pool"])
        if flavor is None:
            orphans.add(j["pool"])
            continue
        for g in gs:
            cur.execute("INSERT INTO merc_units VALUES (?,?,?,?,?,?,?,?,?,?)", (
                g["unit_record"], j["pool"], flavor,
                j.get("subculture_requirement") or "", j.get("faction_requirement") or "",
                j.get("tech_requirement") or "", j["group"],
                j.get("initial_unit_count"), g.get("max_count"), g.get("chance_to_replenish")))
            n += 1
    cur.execute("CREATE INDEX idx_merc_units_unit ON merc_units (unit)")
    if orphans:
        print("  !! merc_units: %d junction rows referenced missing groups/pools: %s"
              % (len(orphans), sorted(orphans)[:8]))
    report["_written"]["merc_units"] = n


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

    r = one("SELECT a.key, a.agent, a.cannot_fail_result, r.target_bundle, r.target_bundle_turns, "
            "o.effect, o.effect_scope, o.value FROM agent_actions a "
            "JOIN action_results r ON r.key=a.cannot_fail_result "
            "LEFT JOIN action_result_outcomes o ON o.action_result_key=r.key "
            "WHERE a.ability='assist_army' AND a.key LIKE '%assist_army_training' LIMIT 1")
    print(f"  assist training {r and r[0]!r}: agent={r and r[1]} bundle={r and r[3]!r} "
          f"turns={r and r[4]} effect={r and r[5]!r} scope={r and r[6]!r} value={r and r[7]}")

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
