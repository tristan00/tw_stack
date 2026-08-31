from __future__ import annotations


from decisions import pg

_CACHE = {}


def _open():
    con = pg.connect(autocommit=True, readonly=True, row_factory=pg.row_factory,
                     search_path="reference")
    if con.execute("SELECT to_regclass('reference.buildings')").fetchone()[0] is None:
        con.close()
        raise RuntimeError("mapgraph.catalogue: the reference schema is missing -- it is a "
                           "hard dependency; rebuild it (advisor/reference/"
                           "build_reference.py) before training or ranking")
    if con.execute(
            "SELECT to_regclass('reference.ancillary_effects')").fetchone()[0] is None:
        con.close()
        raise RuntimeError("mapgraph.catalogue: the reference extra tables are missing "
                           "(tech_links/skill_links/ancillaries/ancillary_effects/"
                           "effects_meta) -- run advisor/reference/build_reference.py "
                           "extra before training or ranking")
    return con


def _load():
    if _CACHE:
        return _CACHE
    out = {"building_chain": {}, "chain_super": {}, "chain_category": {},
           "tech_parents": {}, "skill_actions": {}, "agent_ability": {},
           "unit_caste": {}, "skill_parents": {}, "item_effects": {},
           "building_next": {}, "ok": True}
    cx = _open()
    try:
        levels: dict = {}
        for r in cx.execute("SELECT key, building_chain, level FROM buildings").fetchall():
            if r["building_chain"]:
                out["building_chain"][r["key"]] = r["building_chain"]
                lv = r["level"]
                if lv is not None:
                    levels.setdefault(r["building_chain"], []).append((int(lv), r["key"]))
        for chain, rows in levels.items():
            rows.sort()
            for (_l1, a), (_l2, b) in zip(rows, rows[1:]):
                out["building_next"][a] = b
        for r in cx.execute("SELECT key, superchain, chain_category "
                            "FROM building_chains").fetchall():
            out["chain_super"][r["key"]] = r["superchain"]
            out["chain_category"][r["key"]] = r["chain_category"]
        for r in cx.execute("SELECT key, agent, ability FROM agent_actions").fetchall():
            out["agent_ability"][r["key"]] = r["ability"]
        for r in cx.execute("SELECT key, caste, category, tier FROM units").fetchall():
            out["unit_caste"][r["key"]] = (r["caste"], r["category"], r["tier"])
        for skill, action in cx.execute(
                "SELECT skill, agent_action FROM skill_actions").fetchall():
            out["skill_actions"].setdefault(skill, []).append(action)
        for r in cx.execute("SELECT DISTINCT child, parent FROM tech_links").fetchall():
            if r["child"] and r["parent"]:
                out["tech_parents"].setdefault(r["child"], []).append(r["parent"])
        for r in cx.execute("SELECT DISTINCT child, parent FROM skill_links").fetchall():
            if r["child"] and r["parent"]:
                out["skill_parents"].setdefault(r["child"], []).append(r["parent"])
        for r in cx.execute("SELECT ancillary, effect, value "
                            "FROM ancillary_effects").fetchall():
            out["item_effects"].setdefault(r["ancillary"], []).append(
                (r["effect"], float(r["value"] or 0.0)))
    finally:
        cx.close()
    _CACHE.update(out)
    return _CACHE


_KIND_TABLE = {"building": "buildings", "chain": "building_chains", "unit": "units",
               "tech": "tech", "skill": "skills", "ritual": "rituals",
               "agent_action": "agent_actions", "item": "ancillaries"}
_KIND_COLUMN = {"agent_subtype": ("agent_permitted_subtypes", "subtype"),
                "effect": ("effects_meta", "effect")}
_DENSE = {}


def dense_ids():
    if _DENSE:
        return _DENSE
    out = {k: {} for k in list(_KIND_TABLE) + list(_KIND_COLUMN)}
    cx = _open()
    try:
        cols = [(k, t, "key") for k, t in _KIND_TABLE.items()]
        cols += [(k, t, c) for k, (t, c) in _KIND_COLUMN.items()]
        for kind, tab, col in cols:
            keys = sorted({r[0] for r in cx.execute(
                "SELECT %s FROM %s WHERE %s IS NOT NULL AND %s != ''"
                % (col, tab, col, col)) if r[0]})
            out[kind] = {k: i + 1 for i, k in enumerate(keys)}
    finally:
        cx.close()
    _DENSE.update(out)
    return _DENSE


_ATTR_QUERY = {
    "building": ("SELECT key, create_cost, level, create_time FROM buildings",
                 ("create_cost", "level", "create_time")),
    "unit": ("SELECT key, recruitment_cost, upkeep_cost, num_men, tier, create_time "
             "FROM units",
             ("recruitment_cost", "upkeep_cost", "num_men", "unit_tier", "create_time")),
    "skill": ("SELECT key, unlocked_at_rank, is_background_skill FROM skills",
              ("unlocked_at_rank", "is_background_skill")),
    "tech": ("SELECT key, tier, research_points_required FROM tech",
             ("tech_tier", "research_points_required")),
    "item": ("SELECT key, uniqueness_score, legendary, transferrable FROM ancillaries",
             ("uniqueness_score", "legendary", "transferrable")),
    "effect": ("SELECT effect, positive_good, priority FROM effects_meta",
               ("positive_good", "priority")),
}
_ATTRS = {}


def attrs():
    if _ATTRS:
        return _ATTRS
    out = {k: {} for k in _ATTR_QUERY}
    cx = _open()
    try:
        for kind, (sql, names) in _ATTR_QUERY.items():
            for row in cx.execute(sql).fetchall():
                key = row[0]
                if not key:
                    continue
                vals = {}
                for name, v in zip(names, row[1:]):
                    if v is None:
                        continue
                    try:
                        vals[name] = float(v)
                    except (TypeError, ValueError):
                        continue
                if vals:
                    out[kind][key] = vals
    finally:
        cx.close()
    _ATTRS.update(out)
    return _ATTRS


def chain_of(building_key):
    return _load()["building_chain"].get(building_key)


def next_level_of(building_key):
    return _load()["building_next"].get(building_key)


def tech_parents_of(tech_key):
    return _load()["tech_parents"].get(tech_key) or ()


def skill_parents_of(skill_key):
    return _load()["skill_parents"].get(skill_key) or ()


def item_effects_of(item_key):
    return _load()["item_effects"].get(item_key) or ()


def ability_of(agent_action_key):
    return _load()["agent_ability"].get(agent_action_key)


def ready():
    return _load()["ok"]


if __name__ == "__main__":
    c = _load()
    print("reference ok      :", c["ok"])
    print("buildings->chain  :", len(c["building_chain"]))
    print("chains->super     :", len(c["chain_super"]))
    print("agent_actions     :", len(c["agent_ability"]))
    print("units             :", len(c["unit_caste"]))
    print("skill->actions    :", len(c["skill_actions"]))
