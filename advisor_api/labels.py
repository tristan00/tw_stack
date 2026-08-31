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
