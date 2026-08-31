from __future__ import annotations


import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.DECISIONS)

import features as F
import memory as MEM
import options as O

FAILED = []


def check(cond, what, detail=""):
    print("  %-4s %-54s %s" % ("ok" if cond else "FAIL", what, detail))
    if not cond:
        FAILED.append(what)


NEW_COLUMNS = (
    "opt_last_prebattle_choice_at_loc", "opt_actions_since_prebattle_at_loc",
    "opt_last_prebattle_result_at_loc", "opt_last_prebattle_casualties_at_loc",
    "opt_last_prebattle_choice_in_region", "opt_actions_since_prebattle_in_region",
    "opt_last_prebattle_result_in_region", "opt_last_prebattle_casualties_in_region",
    "opt_last_prebattle_same_lord", "lord_turns_since_moved",
    "lord_recruits_this_turn", "lord_queue_turns_to_clear",
    "lord_queue_queued_this_turn", "opt_queue_depth_after",
    "prov_pending_recruits_others", "prov_recruits_this_turn_others",
    "lrec_inprogress", "camp_taken_recruit_unit",
    "camp_taken_attack_army", "camp_taken_attack_settlement",
    "opt_enemy_reinf_nearest_dist", "opt_enemy_reinf_armies_r10",
    "opt_enemy_reinf_armies_r25", "opt_enemy_reinf_units_r10",
    "opt_enemy_reinf_units_r25", "opt_enemy_reinf_hp_r10",
    "opt_enemy_reinf_units_samefac_r10", "opt_target_garrison_nearby_units",
    "opt_own_reinf_nearest_dist", "opt_own_reinf_units_r10", "opt_own_reinf_units_r25",
)


def _world():
    return {
        "regions": [{"region": "reg_a", "province": "prov_a", "owner": "me",
                     "x": 10, "y": 10},
                    {"region": "reg_b", "province": "prov_b", "owner": "orcs",
                     "x": 30, "y": 30}],
        "settlements": [{"region": "reg_a", "units": 4, "x": 10, "y": 10}],
        "armies": [{"cqi": "1", "faction": "me", "region": "reg_a",
                    "province": "prov_a", "x": 20, "y": 20,
                    "has_army": True, "rank": 2, "units": 6, "hp": 10},
                   {"cqi": "2", "faction": "me", "region": "reg_a",
                    "province": "prov_a", "x": 23, "y": 20,
                    "has_army": True, "rank": 1, "units": 9, "hp": 12}],
        "hostiles": [{"kind": "army", "cqi": "9", "faction": "orcs",
                      "province": "prov_b", "x": 21, "y": 20, "units": 5, "hp": 4},
                     {"kind": "army", "cqi": "10", "faction": "orcs",
                      "province": "prov_b", "x": 25, "y": 20, "units": 8, "hp": 6},
                     {"kind": "settlement", "region": "reg_b", "faction": "orcs",
                      "x": 22, "y": 21, "units": 3},
                     {"kind": "army", "cqi": "77", "faction": "orcs",
                      "is_armed_citizenry": True, "x": 22, "y": 21,
                      "units": 3, "hp": 2}],
        "enemy_agents": [], "ruins": [], "citizenry": [],
    }


def _record(mem):
    campaign = {"faction": "wh_main_me_faction", "turn": 5, "treasury": 1000,
                "income": 100, "action_counts": {"recruit_unit": 2,
                                                 "attack_army": 1,
                                                 "attack_settlement": 1}}
    mem.stamp(campaign)
    lord = {"context_kind": "lord", "context_id": "1",
            "state": {"cqi": "1", "x": 20, "y": 20, "units": 6, "hp": 10,
                      "rank": 2, "region": "reg_a", "pending_recruits": 1,
                      "pending_recruit_keys": ["wh_main_vmp_inf_zombie"]},
            "offers": [
                {"action_type": "attack_army", "key": "cqi:9",
                 "params": {"target_cqi": 9, "target_faction": "orcs",
                            "x": 21, "y": 20}},
                {"action_type": "recruit_unit", "key": "wh_main_vmp_inf_zombie",
                 "params": {"unit": "wh_main_vmp_inf_zombie", "cost": 100}},
            ]}
    prov = {"context_kind": "province", "context_id": "reg_a",
            "state": {"region": "reg_a", "province": "prov_a",
                      "settlement_present": True, "free_slots": 1,
                      "settlement_level": 2}, "offers": []}
    return {"decision_id": 7, "turn": 5, "campaign_id": "c1", "campaign": campaign,
            "world": _world(), "entities": [lord, prov]}, lord


def _run():
    mem = MEM.CampaignMemory()
    mem.begin_turn(3)
    mem.note_pick("lord", "1", "recruit_unit",
                  {"x": 20, "y": 20, "pending_recruit_keys": ["wh_main_vmp_inf_zombie",
                                                              "wh_main_vmp_inf_zombie"]},
                  True)
    ages3 = mem.stamp({})["queue_ages"]["1"]
    check(ages3 == [["wh_main_vmp_inf_zombie", 0], ["wh_main_vmp_inf_zombie", 0]],
          "duplicate pending keys tracked as a multiset", repr(ages3))
    mem.begin_turn(4)
    mem.note_pick("lord", "1", "move",
                  {"x": 22, "y": 20,
                   "pending_recruit_keys": ["wh_main_vmp_inf_zombie"]}, True)
    ages4 = mem.stamp({})["queue_ages"]["1"]
    check(ages4 == [["wh_main_vmp_inf_zombie", 1]],
          "FIFO shrink keeps the older entry age", repr(ages4))
    check(mem.stamp({})["recruit_counts_turn"] == {},
          "recruit counts reset on the new turn")
    check(mem.stamp({})["last_move_turn"]["1"] == 4, "movement observed per turn")

    mem.begin_turn(5)
    mem.note_pick("lord", "1", "attack_settlement", {"x": 20, "y": 20}, True)
    mem.note_prebattle("lord", "1", "attack_settlement", "reg_b",
                       {"x": 22, "y": 21}, _world(), "button_retreat",
                       "defeat", "High")
    mem.note_pick("lord", "1", "attack_army", {"x": 20, "y": 20}, True)
    mem.note_prebattle("lord", "1", "attack_army", "cqi:9",
                       {"target_cqi": 9, "x": 21, "y": 20}, _world(),
                       "button_retreat", "defeat", "High")

    feats = MEM.prebattle_option_feats(
        mem.stamp({}), "attack_army", "cqi:9",
        {"target_cqi": 9, "x": 21, "y": 20}, _world(), "1")
    check(feats["opt_last_prebattle_choice_at_loc"] == "retreat",
          "tile-keyed memory returns the last choice")
    check(feats["opt_actions_since_prebattle_at_loc"] == 0.0,
          "actions-since counts from the pre-battle's action index",
          repr(feats["opt_actions_since_prebattle_at_loc"]))
    check(feats["opt_last_prebattle_result_at_loc"] == "defeat"
          and feats["opt_last_prebattle_casualties_at_loc"] == "High",
          "forecast result and casualties ride along")
    check(feats["opt_last_prebattle_same_lord"] == 1.0, "same-lord flag set")
    zfeats = MEM.prebattle_option_feats(
        mem.stamp({}), "attack_army", "cqi:10",
        {"target_cqi": 10, "x": 25, "y": 20}, _world(), "2")
    check(zfeats["opt_last_prebattle_choice_at_loc"] == "none"
          and zfeats["opt_last_prebattle_choice_in_region"] == "retreat",
          "province-keyed memory catches the co-defending cluster")
    check(zfeats["opt_last_prebattle_same_lord"] == 0.0,
          "another lord's memory is flagged as not its own")

    rec, lord = _record(mem)
    rows = {o["action_type"]: r for o, r in F.offer_rows(rec, lord)}
    atk = rows["attack_army"]
    check(atk["opt_enemy_reinf_units_r10"] == 8.0
          and atk["opt_enemy_reinf_armies_r10"] == 1.0,
          "enemy reinforcements near the target, target excluded",
          repr(atk["opt_enemy_reinf_units_r10"]))
    check(atk["opt_enemy_reinf_units_samefac_r10"] == 8.0,
          "same-faction reinforcement subtotal")
    check(atk["opt_target_garrison_nearby_units"] == 3.0,
          "nearby settlement garrison visible",
          repr(atk["opt_target_garrison_nearby_units"]))
    check(atk["opt_own_reinf_units_r10"] == 9.0,
          "own reinforcements near the target, self excluded")
    check(atk["opt_last_prebattle_choice_at_loc"] == "retreat",
          "attack row carries the pre-battle memory")
    check(atk["lord_recruits_this_turn"] == 0.0, "per-lord recruit counter in the row")
    rcr = rows["recruit_unit"]
    check(rcr["opt_queue_depth_after"] == 2.0, "queue depth after this recruit")
    check(rcr["lrec_inprogress"] == 1.0, "pending queue length visible")
    q = rcr["lord_queue_turns_to_clear"]
    check(q is None or q >= 0.0, "queue turns-to-clear computed", repr(q))
    union = set(atk) | set(rcr)
    missing = [c for c in NEW_COLUMNS if c not in union]
    check(not missing, "every new MODEL_COLUMNS name is emitted", repr(missing))
    check(set(NEW_COLUMNS) <= F.MODEL_COLUMNS, "new names are model columns")

    qlord = {"context_kind": "lord", "context_id": "1",
             "state": {"cqi": "1", "x": 20, "y": 20, "units": 6, "hp": 10,
                       "rank": 2, "region": "reg_a", "pending_recruits": 4,
                       "ap_pct": 50.0, "stance": "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT",
                       "pending_recruit_keys": ["wh_main_vmp_inf_zombie"] * 4,
                       "pending_queue": [
                           {"key": "wh_main_vmp_inf_zombie", "turns_left": 1},
                           {"key": "wh_main_vmp_inf_zombie", "turns_left": 1},
                           {"key": "wh_main_vmp_inf_zombie", "turns_left": 2},
                           {"key": "wh_main_vmp_inf_zombie", "turns_left": 2}],
                       "recruitable": [{"key": "wh_main_vmp_inf_zombie",
                                        "state": "disabled", "cost": 100}]},
             "offers": []}
    qrec = {"decision_id": 8, "turn": 5, "campaign_id": "c1",
            "campaign": {"faction": "wh_main_me_faction", "turn": 5, "treasury": 100},
            "world": _world(), "entities": [qlord]}
    gate = O.Gate()
    opts = gate.apply(qrec)
    cancels = [o for o in opts if o["action_type"] == "cancel_recruit"]
    check(len(cancels) == 4, "cancel offered for every queued unit", repr(len(cancels)))
    check(all(o["key"].endswith("@%d" % i) for i, o in enumerate(cancels)),
          "cancel keys carry the queue index")
    gate.note_result(cancels[0], True)
    opts2 = gate.apply(qrec)
    left = [o for o in opts2 if o["action_type"] == "cancel_recruit"]
    capped = [d for d in gate.last_drops if d["action_type"] == "cancel_recruit"
              and str(d.get("reason", "")).startswith("per_turn_cap")]
    check(not left and len(capped) == 4, "cancel capped at 1 per turn",
          "left=%d capped=%d" % (len(left), len(capped)))
    other = {"context_kind": "lord", "context_id": "2",
             "state": dict(qlord["state"], cqi="2"), "offers": []}
    orec = dict(qrec, entities=[other])
    left2 = [o for o in gate.apply(orec) if o["action_type"] == "cancel_recruit"]
    check(not left2, "the cancel cap is faction-wide, not per lord",
          repr(len(left2)))

    qrec["entities"][0]["offers"] = [
        {"action_type": "cancel_recruit", "key": "wh_main_vmp_inf_zombie@2",
         "params": {"unit": "wh_main_vmp_inf_zombie", "queue_index": 2,
                    "turns_left": 2}}]
    mem3 = MEM.CampaignMemory()
    mem3.begin_turn(4)
    mem3.note_pick("lord", "1", "move",
                   {"x": 20, "y": 20,
                    "pending_recruit_keys": ["wh_main_vmp_inf_zombie"] * 4,
                    "pending_queue": [
                        {"key": "wh_main_vmp_inf_zombie", "turns_left": 1},
                        {"key": "wh_main_vmp_inf_zombie", "turns_left": 1},
                        {"key": "wh_main_vmp_inf_zombie", "turns_left": 2},
                        {"key": "wh_main_vmp_inf_zombie", "turns_left": 2}]}, True)
    mem3.begin_turn(5)
    mem3.note_pick("lord", "1", "move", qlord["state"], True)
    check(mem3.stamp({})["queue_stall"]["1"] == [1.0, 1.0, 1.0, 1.0],
          "cross-turn stall flags every frozen queue entry",
          repr(mem3.stamp({}).get("queue_stall")))
    mem4 = MEM.CampaignMemory()
    mem4.begin_turn(4)
    mem4.observe_entity("lord", "9", {"x": 1, "y": 1,
                                      "pending_queue": [{"key": "u", "turns_left": 2}]})
    mem4.begin_turn(5)
    mem4.observe_entity("lord", "9", {"x": 1, "y": 1,
                                      "pending_queue": [{"key": "u", "turns_left": 1}]})
    check(mem4.stamp({})["queue_stall"]["9"] == [0.0],
          "a ticking entry is flagged as progressing",
          repr(mem4.stamp({}).get("queue_stall")))
    mem3.stamp(qrec["campaign"])
    crows = {o["action_type"]: r for o, r in F.offer_rows(qrec, qrec["entities"][0])}
    crow = crows["cancel_recruit"]
    check(crow["opt_cancel_stalled"] == 1.0, "cancel option flags its own frozen entry")
    check(crow["opt_cancel_turns_left"] == 2.0, "cancel option carries turns left")
    check(crow["opt_queue_depth_after"] == 3.0, "cancel option carries queue depth after")
    check(crow["optk_cancel_recruit"] == "wh_main_vmp_inf_zombie",
          "cancel option keyed by the unit")
    check(crow["lord_queue_on_hold"] == 1.0, "on-hold flag set when nothing recruitable")
    check(crow["lord_queue_turns_to_clear"] == 2.0, "exact turns-to-clear from the queue")
    check(crow["lord_queue_stalled_units"] == 4.0, "stalled-units count in the row")
    check("lord_queue_on_hold" in F.MODEL_COLUMNS
          and "lord_queue_stalled_units" in F.MODEL_COLUMNS
          and "opt_cancel_turns_left" in F.MODEL_COLUMNS, "new names are model columns")

    gate2 = O.Gate()
    et = {"action_type": "end_turn", "key": "end_turn", "available": True,
          "gate": None, "params": {}, "_state": {}}
    check(gate2.reason("campaign", "f", et, 3) is None,
          "end_turn free with no recent attack")
    gate2.note_result({"context_kind": "lord", "context_id": "1",
                       "action_type": "attack_settlement", "key": "r"}, True)
    check(gate2.reason("campaign", "f", et, 3) == "post_attack_gap:2",
          "end_turn gated right after an attack")
    gate2.note_result({"context_kind": "lord", "context_id": "1",
                       "action_type": "skills", "key": "s"}, True)
    check(gate2.reason("campaign", "f", et, 3) == "post_attack_gap:2",
          "end_turn still gated one action after an attack")
    gate2.note_result({"context_kind": "lord", "context_id": "1",
                       "action_type": "stance", "key": "st"}, True)
    check(gate2.reason("campaign", "f", et, 5) is None,
          "end_turn free again after two spacer actions")
    gate2.note_result({"context_kind": "lord", "context_id": "1",
                       "action_type": "colonize", "key": "r2"}, True)
    check(gate2.reason("campaign", "f", et, O.TURN_ACTION_CAP) is None,
          "the turn action cap overrides the post-attack gap")
    gate2.new_turn()
    check(gate2.reason("campaign", "f", et, 0) is None,
          "the gap clears on a new turn")

    mem2 = MEM.CampaignMemory()
    mem2.begin_turn(5)
    mem2.note_pick("lord", "1", "attack_army", {"x": 20, "y": 20}, True)
    mem2.note_exec({"context_kind": "lord", "context_id": "1",
                    "action_type": "attack_army", "key": "cqi:9",
                    "params": {"target_cqi": 9, "x": 21, "y": 20}},
                   _world(), ts=100.0)
    mem2.feed_interrupts([
        {"kind": "pre_battle", "counted": True, "chosen": "button_retreat",
         "ts": 105.0, "panel": {"result": {"state": "defeat", "text": "Close Defeat"},
                                "casualties": {"state": "3", "text": "High"}}},
        {"kind": "pre_battle", "counted": True, "chosen": "button_autoresolve",
         "ts": 400.0, "panel": {}},
    ])
    check(len(mem2.prebattle) == 1
          and mem2.prebattle[0]["choice"] == "retreat"
          and mem2.prebattle[0]["zone"] == "prov_b",
          "runtime attribution takes the in-window record only",
          repr(mem2.prebattle))


def test_memory():
    _run()
    assert not FAILED, FAILED


if __name__ == "__main__":
    _run()
    sys.exit(1 if FAILED else 0)
