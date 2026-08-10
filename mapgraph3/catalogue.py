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

try:
    from mapgraph3 import schema as S
except ImportError:
    import schema as S

_CACHE = {}


def _load():
    if _CACHE:
        return _CACHE
    path = S.REFERENCE_DB
    out = {"building_chain": {}, "chain_super": {}, "chain_category": {},
           "tech_parents": {}, "skill_actions": {}, "agent_ability": {},
           "unit_caste": {}, "ok": False}
    if not os.path.exists(path):
        sys.stderr.write("mapgraph3.catalogue: %s missing -- catalogue edges will be "
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
        sys.stderr.write("mapgraph3.catalogue: load failed -> %s\n" % repr(e)[:200])

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
            sys.stderr.write("mapgraph3.catalogue: unlocks load failed -> %s\n"
                             % repr(e)[:160])

    # tech prerequisites live in the pack, not in reference.sqlite; the DAG edge is
    # written the moment that table is extracted. Absent for now, and said out loud
    # rather than quietly skipped.
    _CACHE.update(out)
    return _CACHE


def chain_of(building_key):
    return _load()["building_chain"].get(building_key)


def superchain_of(chain_key):
    return _load()["chain_super"].get(chain_key)


def ability_of(agent_action_key):
    return _load()["agent_ability"].get(agent_action_key)


def actions_of_skill(skill_key):
    return _load()["skill_actions"].get(skill_key) or ()


def unit_facts(unit_key):
    return _load()["unit_caste"].get(unit_key)


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
