from __future__ import annotations


import os
import sqlite3
import sys

from advisor.mapgraph import schema as S

_CACHE = {}


def _load():
    if _CACHE:
        return _CACHE
    path = S.REFERENCE_DB
    out = {"building_chain": {}, "chain_super": {}, "chain_category": {},
           "tech_parents": {}, "skill_actions": {}, "agent_ability": {},
           "unit_caste": {}, "ok": False}
    if not os.path.exists(path):
        sys.stderr.write("mapgraph.catalogue: %s missing -- catalogue edges will be "
                         "skipped; the graph still builds, with less structure\n" % path)
        _CACHE.update(out)
        return _CACHE
    try:
        cx = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
        cx.row_factory = sqlite3.Row
        try:
            for r in cx.execute("SELECT key, building_chain FROM buildings"):
                if r["building_chain"]:
                    out["building_chain"][r["key"]] = r["building_chain"]
            for r in cx.execute("SELECT key, superchain, chain_category "
                                "FROM building_chains"):
                out["chain_super"][r["key"]] = r["superchain"]
                out["chain_category"][r["key"]] = r["chain_category"]
            for r in cx.execute("SELECT key, agent, ability FROM agent_actions"):
                out["agent_ability"][r["key"]] = r["ability"]
            for r in cx.execute("SELECT key, caste, category, tier FROM units"):
                out["unit_caste"][r["key"]] = (r["caste"], r["category"], r["tier"])
            out["ok"] = True
        finally:
            cx.close()
    except Exception as e:
        sys.stderr.write("mapgraph.catalogue: load failed -> %s\n" % repr(e)[:200])

    unlocks = os.path.join(os.path.dirname(path), "agent_action_unlocks.sqlite")
    if os.path.exists(unlocks):
        try:
            cx = sqlite3.connect("file:%s?mode=ro" % unlocks.replace("\\", "/"), uri=True)
            try:
                for skill, action in cx.execute("SELECT skill, agent_action "
                                                "FROM skill_actions"):
                    out["skill_actions"].setdefault(skill, []).append(action)
            finally:
                cx.close()
        except Exception as e:
            sys.stderr.write("mapgraph.catalogue: unlocks load failed -> %s\n"
                             % repr(e)[:160])

    _CACHE.update(out)
    return _CACHE


_KIND_TABLE = {"building": "buildings", "chain": "building_chains", "unit": "units",
               "tech": "tech", "skill": "skills", "ritual": "rituals",
               "agent_action": "agent_actions"}
_KIND_COLUMN = {"agent_subtype": ("agent_permitted_subtypes", "subtype")}
_DENSE = {}


def dense_ids():
    if _DENSE:
        return _DENSE
    path = S.REFERENCE_DB
    out = {k: {} for k in list(_KIND_TABLE) + list(_KIND_COLUMN)}
    if not os.path.exists(path):
        sys.stderr.write("mapgraph.catalogue: %s missing -- catalogue ids fall back to "
                         "hashing, which is NOT injective\n" % path)
        _DENSE.update(out)
        return _DENSE
    try:
        cx = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
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
    except Exception as e:
        sys.stderr.write("mapgraph.catalogue: dense id load failed -> %s\n" % repr(e)[:200])
    _DENSE.update(out)
    return _DENSE


def chain_of(building_key):
    return _load()["building_chain"].get(building_key)


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
