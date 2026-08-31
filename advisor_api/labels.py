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


def _loc_like(pattern):
    hit = _cache.get(pattern, "?")
    if hit != "?":
        return hit
    row = _one("SELECT text FROM loc WHERE key LIKE %s AND text<>'' LIMIT 1", (pattern,))
    _cache[pattern] = row["text"].strip() if row and row["text"] else None
    return _cache[pattern]


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
    pre = _LOC_PREFIX.get(fam)
    if pre:
        return _loc(pre + k)
    if fam in _REGION_TYPES:
        return _region(k)
    if fam == "building":
        return _loc_like("building_culture_variants_name_" + k + "%")
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
           ("character_to_region", "region"), ("faction_to_faction", "faction"))


def _fmt_effect(desc, value, scope):
    txt = str(desc or "")
    if _NUM_RE.search(txt):
        txt = _NUM_RE.sub(("%+g" % value) if value is not None else "", txt, count=1)
    elif value is not None and value not in (0.0, 1.0):
        txt = "%+g %s" % (value, txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if len(txt) > 1 and txt[0] == '"' and txt[-1] == '"':
        txt = txt[1:-1]
    sc = ""
    for frag, short in _SCOPES:
        if str(scope or "").startswith(frag):
            sc = short
            break
    return "%s (%s)" % (txt, sc) if sc and sc != "self" else txt


def item_effects(key: str) -> str | None:
    if not _have("ancillary_effects"):
        return None
    rows = _rows("SELECT effect, effect_scope, value FROM ancillary_effects"
                 " WHERE ancillary = %s ORDER BY effect", (key,))
    parts = []
    for r in rows:
        desc = _loc("effects_description_" + str(r["effect"] or ""))
        parts.append(_fmt_effect(desc or pretty(r["effect"]), r["value"],
                                 r["effect_scope"]))
    return " · ".join(p for p in parts if p) or None


def tech_parents() -> dict:
    if not _have("tech_links"):
        return {}
    out: dict = {}
    for r in _rows("SELECT child, parent FROM tech_links ORDER BY child, parent"):
        if r["parent"]:
            out.setdefault(r["child"], []).append(r["parent"])
    return out


def tech_rows(prefixes) -> list:
    pats = [str(p) + "%" for p in prefixes if p]
    if not pats:
        return []
    return [dict(r) for r in _rows(
        "SELECT key, technology_key, tier, research_points_required"
        " FROM tech WHERE key LIKE ANY(%s)"
        " AND COALESCE(is_hidden, 0) = 0", (pats,))]


def tech_name(key: str, technology_key: str | None = None) -> str | None:
    return (_loc("technologies_onscreen_name_" + (technology_key or key))
            or _loc("technologies_onscreen_name_" + key))


def skill_parents() -> dict:
    if not _have("skill_links"):
        return {}
    out: dict = {}
    for r in _rows("SELECT DISTINCT child, parent FROM skill_links ORDER BY 1, 2"):
        out.setdefault(r["child"], []).append(r["parent"])
    return out


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
