from __future__ import annotations

import os
import re
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

_lock = threading.Lock()
_con = None
_cache: dict = {}

_TR_RE = re.compile(r"^\{\{tr:(.+)\}\}$")
_GAME_PREFIX = re.compile(r"^wh\d?_(main|dlc\d+|pro\d+|twa\d+|cpl)_")
_CULT_PREFIX = re.compile(
    r"^(cth|hef|def|vmp|dwf|brt|chs|kho|nor|bst|emp|grn|lzd|skv|tmb|wef|cst|dae|ksl|ogr"
    r"|sla|tze|nur|arc|ie)_")


def _ref():
    global _con
    if _con is None or getattr(_con, "closed", True):
        _con = pg.connect(autocommit=True, readonly=True, row_factory=pg.row_factory,
                          search_path="reference")
    return _con


def _one(sql, args):
    with _lock:
        try:
            return _ref().execute(sql, args).fetchone()
        except Exception:
            global _con
            _con = None
            return None


def _loc(key, depth=0):
    hit = _cache.get(key, "?")
    if hit != "?":
        return hit
    row = _one("SELECT text FROM loc WHERE key=%s", (key,))
    txt = (row["text"] or "").strip() if row else ""
    m = _TR_RE.match(txt)
    if m and depth < 2:
        txt = (_loc(m.group(1), depth + 1)
               or _loc("ui_text_replacements_localised_text_" + m.group(1), depth + 1)
               or "")
    _cache[key] = txt or None
    return _cache[key]


def pretty(key) -> str:
    k = str(key or "")
    k = _GAME_PREFIX.sub("", k)
    k = _CULT_PREFIX.sub("", k)
    return k.replace("_", " ").strip() or str(key or "")


_LOC_PREFIX = {
    "research": "technologies_onscreen_name_",
    "skills": "character_skills_localised_name_",
    "items": "ancillaries_onscreen_name_",
    "item_unequip": "ancillaries_onscreen_name_",
    "recruit_unit": "land_units_onscreen_name_",
    "recruit_ror": "land_units_onscreen_name_",
}

_REGION_TYPES = ("attack_settlement", "garrison", "leave_garrison", "colonize",
                 "building_dismantle")


def _region(key):
    return _loc("regions_onscreen_" + key)


def name_for(family: str, key: str) -> str | None:
    fam = str(family or "")
    k = str(key or "")
    if not k:
        return None
    if k.startswith("settlement:"):
        k = k.split(":", 1)[1]
    if fam == "research":
        return tech_name(k)
    pre = _LOC_PREFIX.get(fam)
    if pre:
        return _loc(pre + k)
    if fam in _REGION_TYPES:
        return _region(k)
    if fam == "building":
        return building_names([k]).get(k)
    if fam == "edict":
        return (_loc("effect_bundles_localised_title_" + k)
                or _loc("provincial_initiative_records_localised_name_" + k))
    if fam in ("recruit_lord", "recruit_hero"):
        sub = k.split("@", 1)[0]
        return (_loc("agent_subtypes_onscreen_name_override_" + sub)
                or _loc("names_name_" + sub))
    if fam == "diplomacy" and ":" in k:
        fac, treaty = k.split(":", 1)
        fl = _loc("factions_screen_name_" + fac) or pretty(fac)
        return "%s → %s" % (treaty.replace("_", " "), fl)
    return None


_ITEM_CATS = (("_anc_weapon", "weapon"), ("_anc_armour", "armour"),
              ("_anc_talisman", "talisman"), ("_anc_enchanted", "enchanted"),
              ("_anc_arcane", "arcane"), ("_anc_banner", "banner"),
              ("_anc_follower", "follower"), ("_anc_mount", "mount"),
              ("_anc_magic", "magic"), ("_anc_rune", "rune"))


def _rows(sql, args=()):
    with _lock:
        try:
            return _ref().execute(sql, args).fetchall()
        except Exception:
            global _con
            _con = None
            return []


_HAVE: dict = {}


def _have(table: str) -> bool:
    if table not in _HAVE:
        row = _one("SELECT 1 FROM information_schema.tables"
                   " WHERE table_schema = 'reference' AND table_name = %s", (table,))
        _HAVE[table] = bool(row)
    return _HAVE[table]


def item_category(key: str) -> str | None:
    row = _one("SELECT category FROM ancillaries WHERE key = %s",
               (key,)) if _have("ancillaries") else None
    if row and row["category"] and row["category"] != "general":
        return str(row["category"]).replace("_", " ")
    k = str(key or "")
    for frag, cat in _ITEM_CATS:
        if frag in k:
            return cat
    return None


_NUM_RE = re.compile(r"%\+?\.?\d*[nfd]")

_SCOPES = (("character_to_character", "self"), ("character_to_force", "army"),
           ("character_to_province", "province"), ("character_to_faction", "faction"),
           ("character_to_region", "region"), ("faction_to_faction", "faction"),
           ("faction_to_character", "self"), ("faction_to_force", "army"),
           ("faction_to_province", "province"))


_effect_good: dict | None = None


def _effect_positive() -> dict:
    global _effect_good
    if _effect_good is None:
        _effect_good = ({r["effect"]: bool(r["positive_good"]) for r in _rows(
            "SELECT effect, positive_good FROM effects_meta")}
            if _have("effects_meta") else {})
    return _effect_good


def _scope_short(scope) -> str:
    s = str(scope or "")
    for frag, short in _SCOPES:
        if s.startswith(frag):
            return short
    return "self"


def item_effect_rows(key: str) -> list:
    if not _have("ancillary_effects"):
        return []
    good = _effect_positive()
    out = []
    for r in _rows("SELECT effect, effect_scope, value FROM ancillary_effects"
                   " WHERE ancillary = %s ORDER BY effect", (key,)):
        txt = re.sub(r"\[\[.*?\]\]", "",
                     _loc("effects_description_" + str(r["effect"] or ""))
                     or pretty(r["effect"]))
        txt = re.sub(r"\s+", " ", txt).strip()
        v = r["value"]
        m = _NUM_RE.search(txt)
        if m:
            name = txt[:m.start()].rstrip(" :")
            unit = txt[m.end():].strip()
            value = (("%+g" % v) + (unit if unit == "%" else " " + unit if unit else "")
                     if v is not None else None)
        else:
            name = txt
            value = "%+g" % v if v is not None and v not in (0.0, 1.0) else None
        if len(name) > 1 and name[0] == '"' and name[-1] == '"':
            name = name[1:-1]
        state = "neutral"
        pos = good.get(r["effect"])
        if v and value is not None and pos is not None:
            state = "ok" if (v > 0) == pos else "bad"
        out.append({"name": name, "value": value, "state": state,
                    "scope": _scope_short(r["effect_scope"])})
    out.sort(key=lambda e: e["name"])
    return out


_RES_PREFIX = re.compile(r"^wh\d?_(main|dlc\d+|pro\d+|twa\d+|cp\d+)_effect_")
_RES_LEAD = ("character_stat_", "character_", "agent_", "force_all_", "force_",
             "faction_", "attribute_enable_", "enable_", "economy_", "mod_")
_RES_TAIL = ("_mod_all", "_mod", "_add", "_characters", "_enemy", "_all")

_item_res_cache: dict | None = None


def _resource_name(effect: str) -> str:
    k = _RES_PREFIX.sub("", str(effect))
    changed = True
    while changed:
        changed = False
        for lead in _RES_LEAD:
            if k.startswith(lead) and len(k) > len(lead):
                k = k[len(lead):]
                changed = True
    for tail in _RES_TAIL:
        if k.endswith(tail):
            k = k[:-len(tail)]
            break
    return k.replace("_", " ").strip() or str(effect)


def item_resources() -> dict:
    global _item_res_cache
    if _item_res_cache is None:
        out: dict = {}
        if _have("ancillary_effects"):
            for r in _rows("SELECT ancillary, effect, value FROM ancillary_effects"):
                name = _resource_name(r["effect"])
                e = out.setdefault(r["ancillary"], {})
                e[name] = round(e.get(name, 0.0) + (r["value"] or 0.0), 2)
        _item_res_cache = out
    return _item_res_cache


_bname_rows: list | None = None


def _building_name_rows() -> list:
    global _bname_rows
    if _bname_rows is None:
        got = []
        for r in _rows("SELECT key, text FROM loc WHERE key LIKE %s"
                       " AND text <> ''", ("building\\_culture\\_variants\\_name\\_%",)):
            txt = r["text"].strip()
            m = _TR_RE.match(txt)
            if m:
                txt = (_loc(m.group(1))
                       or _loc("ui_text_replacements_localised_text_" + m.group(1))
                       or "")
            if txt:
                got.append((r["key"][len("building_culture_variants_name_"):], txt))
        if not got:
            return []
        _bname_rows = sorted(got)
    return _bname_rows


def building_names(keys) -> dict:
    import bisect
    rows = _building_name_rows()
    rems = [r[0] for r in rows]
    out = {}
    for k in keys:
        k = str(k or "")
        if not k:
            continue
        i = bisect.bisect_left(rems, k)
        if i < len(rems) and rems[i].startswith(k):
            out[k] = rows[i][1]
    return out


def building_info(keys) -> dict:
    ks = [str(k) for k in keys if k]
    if not (_have("buildings") and ks):
        return {}
    return {r["key"]: dict(r) for r in _rows(
        "SELECT b.key, b.building_chain, b.level, b.create_cost, b.upkeep_cost,"
        " b.create_time, c.chain_category FROM buildings b"
        " LEFT JOIN building_chains c ON c.key = b.building_chain"
        " WHERE b.key = ANY(%s)", (ks,))}


def building_chain_levels(chain: str) -> list:
    if not (_have("buildings") and chain):
        return []
    return [dict(r) for r in _rows(
        "SELECT key, level, create_cost FROM buildings"
        " WHERE building_chain = %s ORDER BY level, key", (chain,))]


def skill_unlock_ranks(keys) -> dict:
    ks = [str(k) for k in keys if k]
    if not (_have("skills") and ks):
        return {}
    return {r["key"]: r["unlocked_at_rank"] for r in _rows(
        "SELECT key, unlocked_at_rank FROM skills WHERE key = ANY(%s)", (ks,))}


def tech_parents() -> dict:
    if not _have("tech_links"):
        return {}
    out: dict = {}
    for r in _rows("SELECT child, parent FROM tech_links ORDER BY child, parent"):
        if r["parent"]:
            out.setdefault(r["child"], []).append(r["parent"])
    return out


def tech_roots() -> dict:
    if not _have("tech"):
        return {}
    parents = tech_parents()
    out: dict = {}
    for r in _rows("SELECT key, node_set FROM tech"
                   " WHERE node_set IS NOT NULL"
                   " AND COALESCE(is_hidden, 0) = 0 ORDER BY key"):
        if not parents.get(r["key"]):
            out.setdefault(str(r["node_set"]), []).append(str(r["key"]))
    return {ns: ks for ns, ks in out.items() if len(ks) > 1}


def tech_rows_for(keys) -> list:
    ks = [str(k) for k in keys if k]
    if not (_have("tech") and ks):
        return []
    return [dict(r) for r in _rows(
        "SELECT key, technology_key, tier, research_points_required"
        " FROM tech WHERE key = ANY(%s)"
        " AND COALESCE(is_hidden, 0) = 0", (ks,))]


def tech_universe(keys) -> list:
    ks = [str(k) for k in keys if k]
    if not (_have("tech") and ks):
        return []
    return [dict(r) for r in _rows(
        "SELECT key, technology_key, tier, research_points_required"
        " FROM tech WHERE (key = ANY(%s) OR node_set IN"
        " (SELECT DISTINCT node_set FROM tech WHERE key = ANY(%s)"
        "  AND node_set IS NOT NULL))"
        " AND COALESCE(is_hidden, 0) = 0", (ks, ks))]


_tech_key_cache: dict = {}


def _tech_key(key: str) -> str | None:
    hit = _tech_key_cache.get(key, "?")
    if hit != "?":
        return hit
    row = _one("SELECT technology_key FROM tech WHERE key = %s",
               (key,)) if _have("tech") else None
    got = row["technology_key"] if row and row["technology_key"] else None
    _tech_key_cache[key] = got
    return got


def tech_name(key: str, technology_key: str | None = None) -> str | None:
    k = str(key or "")
    tkey = technology_key or _tech_key(k)
    return (_loc("technologies_onscreen_name_" + (tkey or k))
            or _loc("technologies_onscreen_name_" + k))


_MARKUP_RE = re.compile(r"\[\[.*?\]\]|\\\\n")


def _clean_text(txt) -> str | None:
    got = re.sub(r"\s+", " ", _MARKUP_RE.sub(" ", str(txt or ""))).strip()
    return got or None


def tech_description(key: str, technology_key: str | None = None) -> str | None:
    return _clean_text(
        _loc("technologies_short_description_" + (technology_key or key))
        or _loc("technologies_short_description_" + key))


def skill_description(key: str) -> str | None:
    return _clean_text(_loc("character_skills_localised_description_" + key))


_skill_line_cache: dict = {}


def skill_lines(subtype: str | None) -> dict:
    key = str(subtype or "")
    if key in _skill_line_cache:
        return _skill_line_cache[key]
    if not (_have("skill_categories") and _have("skill_indents")):
        _skill_line_cache[key] = {}
        return {}
    cats = [dict(r) for r in _rows(
        "SELECT key, min_indent, max_indent, ord, subtype_override"
        " FROM skill_categories")]
    base = {c["ord"]: c["key"] for c in cats if not c["subtype_override"]}
    mine = ([c for c in cats if c["subtype_override"] == key]
            or [c for c in cats if not c["subtype_override"]])
    modal: dict = {}
    for r in _rows("SELECT DISTINCT ON (skill) skill, indent FROM skill_indents"
                   " ORDER BY skill, n DESC"):
        modal[r["skill"]] = r["indent"]
    out = {}
    for skill, ind in modal.items():
        row = next((c for c in sorted(mine, key=lambda c2: c2["ord"])
                    if c["min_indent"] <= ind and ind <= c["max_indent"]), None)
        if row:
            out[skill] = base.get(row["ord"], row["key"])
    _skill_line_cache[key] = out
    return out


def skill_line_of(lines: dict, key: str) -> str | None:
    got = lines.get(key)
    if got:
        return got
    return "unique" if "_unique_" in str(key) else None


_tech_groups_cache: dict | None = None


def tech_groups() -> dict:
    global _tech_groups_cache
    if _tech_groups_cache is None:
        _tech_groups_cache = {r["node_key"]: r["ui_group"] for r in _rows(
            "SELECT node_key, ui_group FROM tech_groups")} \
            if _have("tech_groups") else {}
    return _tech_groups_cache


def tech_group_name(group: str | None) -> str | None:
    if not group:
        return None
    got = (_loc("technology_ui_groups_onscreen_name_" + group)
           or _loc("uied_component_texts_localised_string_" + group))
    return None if not got or got.isdigit() else got


def skill_parents() -> dict:
    if not _have("skill_links"):
        return {}
    out: dict = {}
    for r in _rows("SELECT DISTINCT child, parent FROM skill_links ORDER BY 1, 2"):
        out.setdefault(r["child"], []).append(r["parent"])
    return out


def _invert(parents: dict) -> dict:
    out: dict = {}
    for child, ps in parents.items():
        for p in ps:
            out.setdefault(p, []).append(child)
    return out


def tech_children() -> dict:
    return _invert(tech_parents())


def skill_children() -> dict:
    return _invert(skill_parents())


def pooled_resource_name(key: str) -> str:
    return _loc("pooled_resources_display_name_" + str(key)) or pretty(key)


def subtype_name(subtype: str) -> str | None:
    got = _loc("agent_subtypes_onscreen_name_override_" + str(subtype))
    if got and got.lower() != "legendary lord":
        return got
    return pretty(subtype).title() or None


def target_for(family: str, key: str) -> str | None:
    k = str(key or "")
    if not k or k == "end_turn":
        return None
    if k.startswith("xy:"):
        return "to (%s)" % k[3:].replace(",", ", ")
    if k.startswith("cqi:"):
        return "army %s" % k[4:]
    if "@cqi:" in k:
        return k.split("@cqi:", 1)[0].replace("_", " ")
    if k.startswith("MILITARY_FORCE_ACTIVE_STANCE_TYPE_"):
        return k.rsplit("_", 1)[-1].capitalize()
    got = name_for(family, k)
    if got:
        return got
    if str(family) in ("move", "stance", "attack_army", "hero_action"):
        return pretty(k)
    return None
