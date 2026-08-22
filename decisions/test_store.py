from __future__ import annotations


import json
import os
import shutil
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import common
from decisions import dbopen
from decisions import store_schema as S
from decisions.store import DecisionStore, IncompatibleStore

FAILED = []


def check(cond, what):
    print("  %-4s %s" % ("ok" if cond else "FAIL", what))
    if not cond:
        FAILED.append(what)


def _snapshot(turn, faction="wh_main_emp", uuid="camp-1", n_lords=2, settlements=2.0,
              lord_level=3.0):
    ents = []
    for i in range(n_lords):
        ents.append({
            "context_kind": "lord", "context_id": 100 + i,
            "state": {"cqi": str(100 + i), "rank": 3 + i, "garrisoned": i == 0,
                      "region": "reg_%d" % i},
            "offers": [
                {"action_type": "move", "key": "xy:1,%d" % i,
                 "params": {"x": 1, "y": i}},
                {"action_type": "building", "key": "b_a",
                 "params": {"slot_index": 0, "cost": 100}},
                {"action_type": "building", "key": "b_a",
                 "params": {"slot_index": 1, "cost": 120}},
                {"action_type": "noop", "key": "noop", "params": {}},
            ]})
    ents.append({"context_kind": "campaign", "context_id": faction,
                 "state": {"turn": turn, "faction": faction, "defeated": False},
                 "offers": [{"action_type": "end_turn", "key": "end_turn",
                             "params": {}}]})
    return {"ts": 1000.0 + turn,
            "campaign": {"faction": faction, "campaign_uuid": uuid, "turn": turn,
                         "treasury": 2500 + turn, "settlements": settlements,
                         "lord_level": lord_level, "income": 100.0 + turn,
                         "allies": 0.0, "vassals": 0.0, "power_rank": -40.0},
            "world": {"hostiles": [{"kind": "army", "cqi": 9, "faction": "orcs"}],
                      "relations": [{"faction": "orcs", "at_war": True}]},
            "entities": ents}


def main():
    d = tempfile.mkdtemp(prefix="storetest_")
    try:
        run = os.path.join(d, "run")
        os.makedirs(run)
        st = DecisionStore(run)
        st.register_collector("sha-abc", git_sha="deadbeef", note="test")

        def _write(snap, decision_seq, policy):
            opts = [dict(o, context_kind=e["context_kind"], context_id=e["context_id"])
                    for e in snap["entities"] for o in e.pop("offers", [])]
            did = st.write_decision(snap, decision_seq=decision_seq, policy=policy)
            st.attach_options(did, opts)
            return did

        dids = [_write(_snapshot(t), decision_seq=t, policy="random")
                for t in range(1, 6)]
        check(len(dids) == 5 and all(dids), "write_decision returns ids")

        check(st.layout_violations() == 0, "n_offers equals the rows actually stored")
        n_off, = st.con.execute(
            "SELECT n_offers FROM decisions WHERE decision_id=?",
            (dids[0],)).fetchone()
        check(n_off == 9, "counts: %d options stored" % n_off)
        check(st.con.execute("SELECT count(*) FROM offers WHERE decision_id=?",
                             (dids[0],)).fetchone()[0] == n_off,
              "every stored option is a candidate -- nothing gated reaches the database")

        rec = st.read_decision(dids[0])
        check(rec["campaign"]["treasury"] == 2501, "campaign blob round-trips")
        check(rec["world"]["hostiles"][0]["faction"] == "orcs", "world blob round-trips")
        check(len(rec["entities"]) == 3, "entity count")
        got = {(e["context_kind"], e["context_id"]) for e in rec["entities"]}
        check(("lord", "100") in got and ("campaign", "wh_main_emp") in got,
              "entity identity round-trips")
        lord0 = [e for e in rec["entities"] if e["context_id"] == "100"][0]
        check(lord0["state"]["garrisoned"] is True, "entity features round-trip")
        check(len(lord0["offers"]) == 4, "options land on their own entity")
        bs = [o for o in lord0["offers"] if o["key"] == "b_a"]
        check(len(bs) == 2 and {o["params"]["slot_index"] for o in bs} == {0, 1},
              "two offers sharing action_key keep their distinct params")

        n_actions = st.con.execute("SELECT count(*) FROM actions").fetchone()[0]
        n_offers = st.con.execute("SELECT count(*) FROM offers").fetchone()[0]
        check(n_offers == 45 and n_actions == 9,
              "%d options interned to %d actions" % (n_offers, n_actions))
        n_blobs = st.con.execute("SELECT count(*) FROM blobs").fetchone()[0]
        check(n_blobs == 13, "25 payloads content-addressed to %d blobs" % n_blobs)
        n_world = st.con.execute(
            "SELECT count(DISTINCT world_blob) FROM decisions").fetchone()[0]
        check(n_world == 1, "an unchanged world is stored once, not 5 times")
        raw, stored = st.con.execute("SELECT SUM(n), SUM(length(z)) FROM blobs").fetchone()
        check(stored <= raw + n_blobs,
              "stored bytes never exceed the text (%d -> %d over %d blobs)"
              % (raw, stored, n_blobs))

        st.attach_taken(dids[0], {"context_kind": "lord", "context_id": 100,
                                  "action_type": "building", "key": "b_a",
                                  "executed": True, "confirmed": True, "counted": True,
                                  "confirm": {"signal": "queued", "latency_ms": 12}})
        tm = st.taken_map()
        check(tm.get(dids[0]) == (("lord", "100", "building", "b_a"), True),
              "taken_map identity")
        st.attach_taken(dids[1], {"context_kind": "lord", "context_id": 100,
                                  "action_type": "move", "key": "xy:1,0",
                                  "executed": True, "confirmed": False, "counted": False})
        check(len(st.taken_map()) == 2, "attach_taken is one row per decision")
        st.attach_taken(dids[1], {"context_kind": "lord", "context_id": 100,
                                  "action_type": "move", "key": "xy:1,0",
                                  "executed": True, "confirmed": True, "counted": True})
        check(len(st.taken_map()) == 2 and st.taken_map()[dids[1]][1] is True,
              "re-attaching replaces rather than duplicates")

        st.attach_scores(dids[0], [
            {"context_kind": "lord", "context_id": 100, "action_type": "building",
             "key": "b_a", "score": 0.75, "rank": 1, "gnn_impact": 0.5,
             "models": {"greedy_gnn": {"score": 2.5, "rank": 2}}},
            {"context_kind": "lord", "context_id": 100, "action_type": "move",
             "key": "xy:1,0", "score": 0.25, "rank": 2,
             "models": {"greedy_gnn": {"score": 3.5, "rank": 1}}},
        ])
        con = dbopen.connect(os.path.join(run, "decisions.sqlite"))
        rows = con.execute(
            "SELECT action_key,params,score,rank,ggnn_score,ggnn_rank FROM action_offers"
            " WHERE decision_id=? AND context_id='100' AND action_type='building'"
            " ORDER BY offer_id", (dids[0],)).fetchall()
        scored = [r for r in rows if r[2] is not None]
        check(len(rows) == 2 and len(scored) == 1,
              "a score reaches one of two identically-keyed offers, not both")
        check(abs(scored[0][2] - 0.75) < 1e-6 and scored[0][3] == 1.0,
              "score and rank survive the float32 packing")
        check(abs(scored[0][4] - 2.5) < 1e-6 and scored[0][5] == 2.0,
              "a model_scores row lands beside the legacy packed row, same offer")
        unscored = [r for r in rows if r[2] is None]
        check(unscored and unscored[0][4] is None and unscored[0][5] is None,
              "the offer the scorer did not name reads NULL in model_scores too")

        lab = st.labelled_decisions()
        check(len(lab) == 2, "labelled_decisions returns the labelled ones")
        check([r[0]["decision_id"] for r in lab] == sorted(r[0]["decision_id"] for r in lab),
              "labelled_decisions is in decision_id order")
        rec0 = lab[0][0]
        check(len(rec0["entities"][0]["offers"]) >= 1,
              "labelled_decisions carries the stored options")
        one = st.labelled_decisions(after=dids[0], before=dids[1])
        check(len(one) == 1 and one[0][0]["decision_id"] == dids[1],
              "after/before bound the read")
        check(json.dumps(one[0][0]["entities"], sort_keys=True, default=str)
              == json.dumps([e for r, _k, _c in lab if r["decision_id"] == dids[1]
                             for e in r["entities"]], sort_keys=True, default=str),
              "a bounded read emits exactly what the unbounded one emits")
        check(lab[0][1] == ("lord", "100", "building", "b_a") and lab[0][2] is True,
              "labelled_decisions carries the label")

        dp = con.execute("SELECT decision_id,campaign_id,turn,n_entities,n_offers,campaign,"
                         "world FROM decision_points ORDER BY decision_id").fetchall()
        check(len(dp) == 5 and dp[0][1] == "camp-1", "decision_points view")
        check(json.loads(dp[0][5])["treasury"] == 2501, "decision_points.campaign is text")
        es = con.execute("SELECT snapshot_id,decision_id,context_kind,context_id,features"
                         " FROM entity_snapshots WHERE decision_id=?", (dids[0],)).fetchall()
        check(len(es) == 3 and json.loads(es[0][4])["rank"] == 3, "entity_snapshots view")
        check(len({r[0] for r in con.execute("SELECT snapshot_id FROM entity_snapshots")})
              == 15, "synthetic snapshot_ids are unique across decisions")
        check(len({r[0] for r in con.execute("SELECT offer_id FROM action_offers")}) == 45,
              "synthetic offer_ids are unique across decisions")
        joined = con.execute(
            "SELECT COUNT(*) FROM action_offers o JOIN entity_snapshots e"
            " ON e.snapshot_id=o.snapshot_id").fetchone()[0]
        check(joined == 45, "action_offers.snapshot_id joins entity_snapshots")
        at = con.execute("SELECT decision_id,context_kind,context_id,action_type,action_key,"
                         "counted FROM action_taken ORDER BY decision_id").fetchall()
        check(at[0][1:] == ("lord", "100", "building", "b_a", 1), "action_taken view")
        tj = con.execute("SELECT tree_json FROM interrupt_decisions").fetchall()
        check(tj == [], "interrupt_decisions view exists and is empty")

        st.write_interrupt({"campaign": {"faction": "wh_main_emp", "campaign_uuid": "camp-1",
                                         "turn": 2},
                            "screen": "dilemma", "options": {"a": 1}, "chosen": "a",
                            "executed": True, "confirmed": True,
                            "tree": {"huge": "x" * 10000}, "world": {"w": 1},
                            "panel": {"p": 2}})
        ir = st.interrupt_rows()
        check(len(ir) == 1 and ir[0]["chosen"] == "a" and ir[0]["world"] == {"w": 1},
              "interrupt round-trips")
        check(con.execute("SELECT tree_json FROM interrupt_decisions").fetchone()[0] is None,
              "tree_json is accepted and not stored")

        series = st.target_series()["camp-1"]
        check(set(series) == {1, 2, 3, 4, 5}, "target_series has one row per turn")
        check(series[1]["income"] == 101.0 and series[1]["settlements"] == 2.0
              and series[1]["lord_level"] == 3.0 and series[1]["power_rank"] == -40.0,
              "target_series derives from the turn_open snapshot, sign untouched")
        check(st.entity_series()["camp-1"][("lord", "100")][1] == 3.0, "entity_series")
        seq = st.action_sequence()
        check(len(seq) == 2 and seq[0][0] == "camp-1", "action_sequence")
        cs = st.campaign_snapshots()
        check(len(cs) == 5 and cs[0][0] == "camp-1", "campaign_snapshots")
        s = st.summary()
        check(s["decisions"] == 5 and s["offers"] == 45 and s["taken"] == 2, "summary")
        check(st.max_decision_id() == dids[-1], "max_decision_id")

        av = st.con.execute("PRAGMA auto_vacuum").fetchone()[0]
        check(av == 2, "auto_vacuum is INCREMENTAL (2), got %s" % av)

        _write(_snapshot(7, uuid="camp-2", settlements=2.0), decision_seq=0, policy="random")
        _write(_snapshot(7, uuid="camp-2", settlements=2.0), decision_seq=1, policy="random")
        _write(_snapshot(7, uuid="camp-2", settlements=3.0), decision_seq=2, policy="random")
        _write(_snapshot(8, uuid="camp-2", settlements=3.0), decision_seq=3, policy="random")
        to = {int(r[0]): r for r in con.execute(
            "SELECT turn, settlements, decision_id FROM turn_open"
            " WHERE campaign_id='camp-2'")}
        tc = {int(r[0]): r for r in con.execute(
            "SELECT turn, settlements, decision_id FROM turn_close"
            " WHERE campaign_id='camp-2'")}
        check(to[7][1] == 2.0, "turn_open is the FIRST snapshot of the turn (2, not 3)")
        check(tc[7][1] == 3.0, "turn_close is the LAST snapshot of the turn (3, not 2)")
        check(8 in to and 8 in tc, "a single-decision terminal turn is present in both views")
        check(to[8][2] == tc[8][2], "single-decision turn: open and close are the same row")
        check(st.target_series()["camp-2"][7]["settlements"] == 2.0,
              "target_series reads turn_open, not turn_close")
        by_id, = con.execute(
            "SELECT tb.open_id FROM turn_bounds tb JOIN campaigns c"
            " ON c.campaign_id=tb.campaign_id WHERE c.campaign_key='camp-2' AND tb.turn=7"
        ).fetchone()
        by_seq, = con.execute(
            "SELECT d.decision_id FROM decisions d JOIN campaigns c"
            " ON c.campaign_id=d.campaign_id WHERE c.campaign_key='camp-2' AND d.turn=7"
            " ORDER BY d.decision_seq LIMIT 1").fetchone()
        check(by_id == by_seq, "decision_id order and decision_seq order agree on turn open")
        views = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
        check({"turn_bounds", "turn_open", "turn_close"} <= views,
              "turn_bounds/turn_open/turn_close exist as VIEWS")

        st.close()
        con.close()

        old = os.path.join(d, "v1")
        os.makedirs(old)
        c = sqlite3.connect(os.path.join(old, "decisions.sqlite"))
        c.execute("CREATE TABLE decision_points(decision_id INTEGER PRIMARY KEY, world TEXT)")
        c.commit()
        c.close()
        try:
            DecisionStore(old)
            check(False, "a v1 store raises IncompatibleStore")
        except IncompatibleStore:
            check(True, "a v1 store raises IncompatibleStore")

        st2 = DecisionStore(run)
        check(st2.summary()["decisions"] == 9, "reopen keeps the rows")
        check(st2.layout_violations() == 0, "reopen: layout invariant still holds")
        st2.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n%s" % ("store OK" if not FAILED else "%d FAILED" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
