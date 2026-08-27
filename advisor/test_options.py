from __future__ import annotations


import ast
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.DECISIONS)

FAILED = []


def check(cond, what, detail=""):
    print("  %-4s %-54s %s" % ("ok" if cond else "FAIL", what, detail))
    if not cond:
        FAILED.append(what)


def _record():
    world = {
        "regions": [{"region": "reg_a", "province": "prov_a", "owner": "me",
                     "x": 10, "y": 10, "adjacent": []},
                    {"region": "reg_b", "province": "prov_b", "owner": "orcs",
                     "x": 30, "y": 30, "adjacent": []}],
        "settlements": [{"region": "reg_a", "units": 4, "x": 10, "y": 10}],
        "armies": [{"cqi": "1", "faction": "me", "region": "reg_a", "x": 10, "y": 10,
                    "has_army": True, "rank": 2, "units": 6, "hp": 18},
                   {"cqi": "2", "faction": "me", "region": "reg_a", "x": 10, "y": 10,
                    "has_army": False, "rank": 1, "agent_type": "spy"}],
        "hostiles": [{"kind": "army", "cqi": "9", "faction": "orcs", "x": 20, "y": 20,
                      "units": 5, "is_armed_citizenry": False},
                     {"kind": "settlement", "region": "reg_b", "faction": "orcs",
                      "x": 30, "y": 30, "units": 3}],
        "enemy_agents": [], "ruins": [], "citizenry": [],
        "relations": [{"faction": "orcs", "at_war": False, "allied": False, "trade": False,
                       "their_vassal": False, "standing": -50, "excluded": False}],
        "stationed": {"reg_a": None},
    }
    lord = {"cqi": "1", "rank": 2, "skill_points": 1, "region": "reg_a", "x": 10, "y": 10,
            "garrisoned": False, "stance": "none", "acted": False, "pending_recruits": 0,
            "stances": [{"key": "st_march", "active": False, "can_activate": True,
                         "can_afford": True},
                        {"key": "st_ambush", "active": True, "can_activate": True,
                         "can_afford": True}],
            "skills": [{"key": "sk_a", "status": "active", "level": 0, "total_levels": 3},
                       {"key": "sk_b", "status": "locked", "level": 0, "total_levels": 3},
                       {"key": "sk_mid", "status": "active", "level": 1, "total_levels": 3},
                       {"key": "sk_max", "status": "active", "level": 1, "total_levels": 1}],
            "recruitable": [{"key": "unit_a", "state": "active", "cost": 100,
                             "disabled": False}],
            "reach_chars": {"9": True}, "reach_setts": {"reg_a": True, "reg_b": False},
            "move_tiles": [{"x": 12, "y": 12, "sample_index": 0, "reach_rays": [3],
                            "reach_max": 3}],
            "equipped": [], "horde_slots": None, "merc_pools": {}}
    hero = {"cqi": "2", "rank": 1, "skill_points": 0, "region": "reg_a", "x": 10, "y": 10,
            "is_agent": True, "can_embed": True, "agent_type": "spy", "hidden_skills": [],
            "skills": [{"key": "sk_h", "status": "active", "level": 1, "total_levels": 2}],
            "reach_chars": {"9": True}, "reach_setts": {"reg_b": True},
            "move_tiles": [{"x": 11, "y": 11, "sample_index": 0}], "equipped": []}
    prov = {"region": "reg_a", "settlement_present": True, "province": "prov_a",
            "complete_owner": True, "is_capital": True, "max_slots": 2, "free_slots": 1,
            "built": {"0": "bldg_a"}, "locked_slots": [], "building_now": {},
            "corruption": {}, "selected_edict": None,
            "buildable": [{"slot_index": 1, "key": "bldg_b", "active": True, "empty": True,
                           "can_upgrade": False, "cost": 500, "upkeep": 10, "level": 1,
                           "can_afford": True},
                          {"slot_index": 0, "key": "bldg_c", "active": False,
                           "empty": False, "can_upgrade": True, "cost": 900, "upkeep": 20,
                           "level": 2, "can_afford": False}],
            "edicts": ["ed_a"],
            "slot_states": [{"index": 0, "damaged": False, "can_repair": False,
                             "repairing": False, "can_dismantle": True, "refund": 50,
                             "queued": False, "queued_key": None, "empty": False,
                             "key": "bldg_a", "health": 10, "max_health": 10,
                             "ruined": False, "repair_cost": 0, "upgrading": False,
                             "dismantling": False}]}
    camp = {"faction": "wh_main_emp", "turn": 5, "treasury": 5000, "campaign_uuid": "u-1",
            "faction_cqi": "7", "hero_type_counts": {"spy": 1},
            "anc_pool": [], "equipped_all": [], "lord_pools": {},
            "current_research": None, "research_points": 10,
            "tech": [{"key": "tech_a", "researched": False, "can_research": True,
                      "cost": 100}],
            "rites": [{"index": 1, "can_perform": True, "key": "rit_a",
                       "invalid_reason": None}]}
    return {"ts": 1000.0, "campaign": camp, "world": world, "entities": [
        {"context_kind": "lord", "context_id": "1", "state": lord},
        {"context_kind": "hero", "context_id": "2", "state": hero},
        {"context_kind": "province", "context_id": "reg_a", "state": prov},
        {"context_kind": "campaign", "context_id": "wh_main_emp", "state": camp}]}


def _io_names(path):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    bad = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""])
            for m in mods:
                if m.split(".")[-1] in ("sqlite3", "bus", "store", "dbopen", "journal",
                                        "socket", "requests"):
                    bad.add(m)
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name in ("open", "send", "send_batch", "connect"):
                bad.add(name)
    return sorted(bad)


def main():
    import options as O

    rec = _record()
    cands = list(O.generate(rec))
    kinds = {ck for ck, _cid, _o in cands}
    check(kinds == {"lord", "hero", "province", "campaign"},
          "every entity kind generates from state alone", ",".join(sorted(kinds)))
    check(len(cands) > 10, "the universe is non-trivial", "%d candidates" % len(cands))

    types = {o["action_type"] for _ck, _cid, o in cands}
    for want in ("stance", "skills", "recruit_unit", "move", "building", "edict",
                 "research", "rites", "diplomacy", "end_turn", "noop"):
        check(want in types, "generates %s" % want)

    gate = O.Gate()
    gate.new_turn()
    survivors = gate.apply(rec, actions_taken=9)
    check(bool(survivors), "the gate lets something through",
          "%d survivors" % len(survivors))
    check(bool(gate.last_drops), "the gate drops something",
          "%d dropped" % len(gate.last_drops))
    surv_ids = {(s["context_kind"], str(s["context_id"]), s["action_type"], str(s["key"]))
                for s in survivors}
    drop_ids = {(d["context_kind"], str(d["context_id"]), d["action_type"], str(d["key"]))
                for d in gate.last_drops}
    check(not (surv_ids & drop_ids), "nothing gated is also a survivor")
    check(all(d.get("reason") for d in gate.last_drops), "every drop carries a reason")
    st_drops = {d["key"] for d in gate.last_drops if d["action_type"] == "stance"}
    check("st_ambush" in st_drops, "the already-active stance is gated, not offered")

    cap = O.Gate()
    cap.new_turn()
    col = {"action_type": "colonize", "key": "reg_z", "available": True, "gate": None,
           "params": {}}
    check(cap.reason("lord", "2", col, 9) is None,
          "the first colonize of the turn is allowed")
    cap.note_result({"context_kind": "lord", "context_id": "1",
                     "action_type": "colonize", "key": "reg_y"}, True)
    check(cap.reason("lord", "2", col, 9) == "per_turn_cap:1",
          "a second colonize is capped faction-wide, not per lord",
          str(cap.reason("lord", "2", col, 9)))

    sk = {o["key"]: o for _ck, cid, o in cands
          if cid == "1" and o["action_type"] == "skills"}
    check(sk["sk_max"]["available"] is False and sk["sk_max"]["gate"] == "at_max",
          "a skill at its last level is gated", str(sk["sk_max"]["gate"]))
    check(sk["sk_mid"]["available"] is True,
          "level 1 of 3 is still offered", str(sk["sk_mid"]["gate"]))
    check(sk["sk_a"]["available"] is True, "an untaken skill is offered")

    for stance, attackable in (("MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT", True),
                               ("MILITARY_FORCE_ACTIVE_STANCE_TYPE_AMBUSH", True),
                               ("MILITARY_FORCE_ACTIVE_STANCE_TYPE_SET_CAMP", False),
                               ("MILITARY_FORCE_ACTIVE_STANCE_TYPE_TUNNELING", False),
                               ("MILITARY_FORCE_ACTIVE_STANCE_TYPE_SETTLE", False),
                               ("MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH", False)):
        r = _record()
        lord = r["entities"][0]["state"]
        lord["stance"] = stance
        lord["reach_setts"]["reg_b"] = True
        atk = [o for _ck, cid, o in O.generate(r) if cid == "1"
               and o["action_type"] in ("attack_army", "attack_settlement")]
        short = stance.rsplit("_TYPE_", 1)[-1]
        check(len(atk) == 2, "%s offers both attacks" % short, "%d offers" % len(atk))
        check(all(o["available"] is attackable for o in atk),
              "%s attack available is %s" % (short, attackable),
              ",".join("%s=%s" % (o["action_type"], o["available"]) for o in atk))
        if not attackable:
            check(all(o["gate"] == "stance_forbids_attack" for o in atk),
                  "%s attack carries the stance reason" % short,
                  ",".join(str(o["gate"]) for o in atk))

    from decisions import pgtest
    from store import DecisionStore
    pgtest.fresh()
    try:
        run = "optstore/run"
        st = DecisionStore(run)
        st.register_collector("sha-options")
        did = st.write_decision(rec, decision_seq=0, policy="test")
        n = st.attach_options(did, survivors)
        check(n == len(survivors), "every survivor is stored", "%d rows" % n)
        check(st.layout_violations() == 0, "n_offers matches the rows stored")
        back = st.read_decision(did)
        got = sum(len(e["offers"]) for e in back["entities"])
        check(got == len(survivors), "the options read back", "%d" % got)
        check(all("available" not in o and "gate" not in o
                  for e in back["entities"] for o in e["offers"]),
              "a stored option carries no verdict column")
        rec2 = _record()
        rec2["entities"][0]["offers"] = [{"action_type": "noop", "key": "noop"}]
        try:
            st.write_decision(rec2, decision_seq=1)
            check(False, "write_decision refuses a snapshot carrying offers")
        except ValueError:
            check(True, "write_decision refuses a snapshot carrying offers")
        st.close()
    finally:
        pgtest.drop()

    import re as _re
    import importlib
    cco = io.open(os.path.join(common.LAUNCHER, "cco_actions.py"), encoding="utf-8").read()
    for mod_fn, module in (("_collect_mod", "collect"), ("_options_mod", "options")):
        wanted = sorted(set(_re.findall(r"%s\(\)\.([A-Za-z_][A-Za-z0-9_]*)" % mod_fn, cco)))
        if not wanted:
            continue
        m = importlib.import_module(module)
        missing = [w for w in wanted if not hasattr(m, w)]
        check(not missing, "cco_actions %s() names all exist" % mod_fn,
              "%s -> %s" % (",".join(wanted), ("MISSING " + ",".join(missing))
                            if missing else "all present"))

    bad = _io_names(os.path.join(_HERE, "options.py"))
    check(not bad, "options.py touches no bus, db or file", ", ".join(bad) or "clean")

    stream = io.open(os.path.join(common.DECISIONS, "decisions_stream.py"),
                     encoding="utf-8").read()
    check('["offers"]' not in stream and "['offers']" not in stream,
          "the recorder never indexes offers on a snapshot")
    snap_ents = _record()["entities"]
    check(all("offers" not in e for e in snap_ents),
          "a snapshot carries no offers key at all")

    print("\n%s" % ("options OK" if not FAILED else "%d FAILED" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
