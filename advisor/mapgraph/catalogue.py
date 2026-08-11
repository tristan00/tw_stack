from __future__ import annotations

"""Static game vocabulary from reference.sqlite, loaded once per process.

These are the shared nodes: the same `wh3_main_tze_horror_barracks_1` node is pointed at
by every province that built it and every offer that proposes it, across every campaign.
That sharing is what lets statistics pool across 546 campaigns instead of being trapped
in run-local instance ids.

Every key observed in the corpus joins: 357/357 buildings, 3214/3214 skills, 1766/1766
tech, 184/184 units, 97/97 edicts, 561/561 regions, 214/214 provinces.

CatBoost reads two columns of this database. It is the largest structure in the game
that the peer model cannot see.
"""

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

    # tech prerequisites live in the pack, not in reference.sqlite; the DAG edge is
    # written the moment that table is extracted. Absent for now, and said out loud
    # rather than quietly skipped.
    _CACHE.update(out)
    return _CACHE


# Dense catalogue ids, one table per kind, so no two live game keys share an embedding
# row. schema.cat_index used crc32 % buckets, and a hash over thousands of keys collides
# whatever the bucket count: 9,393 of 19,313 reference keys (48.6%) shared a row with a
# different key -- 60.6% of building chains, 51.1% of skills, 47.9% of buildings. The
# docstring above already promised every key joins this database, so the ids were always
# available and the hash was never needed.
_KIND_TABLE = {"building": "buildings", "chain": "building_chains", "unit": "units",
               "tech": "tech", "skill": "skills", "ritual": "rituals",
               "agent_action": "agent_actions"}
# tables whose key column is not called "key"
_KIND_COLUMN = {"agent_subtype": ("agent_permitted_subtypes", "subtype")}
_DENSE = {}


def dense_ids():
    """{kind: {key: 1..n}}. Sorted by key so an id is a property of the game data and not
    of row order -- a rebuilt reference.sqlite must not silently renumber the embeddings."""
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
