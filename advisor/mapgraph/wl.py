from __future__ import annotations


import collections
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import common
from advisor.mapgraph import build as B
from advisor.mapgraph import schema as S

MAX_ROUNDS = 40


def colours(g, max_rounds=MAX_ROUNDS):
    n = len(g.node_type)
    init = []
    for i in range(n):
        init.append((g.node_type[i], g.atype_idx[i], g.term_idx[i], g.cat_idx[i],
                     g.race_idx[i], g.agent_idx[i], g.stance_idx[i], g.subtype_idx[i],
                     tuple(round(float(v), 6) for v in g.x[i]), g.own_mask[i]))
    pal = {}
    cur = [pal.setdefault(c, len(pal)) for c in init]

    adj = collections.defaultdict(list)
    for s, d, r in zip(g.src, g.dst, g.rel):
        adj[s].append((r, d))

    for _ in range(max_rounds):
        pal = {}
        nxt = []
        for i in range(n):
            sig = (cur[i], tuple(sorted((r, cur[d]) for r, d in adj[i])))
            nxt.append(pal.setdefault(sig, len(pal)))
        if len(set(nxt)) == len(set(cur)):
            return nxt
        cur = nxt
    return cur


def _offers_in_order(record):
    out = []
    for e in record.get("entities") or []:
        for o in e.get("offers") or []:
            out.append(o)
    return out


def violations(record, g=None):
    g = g or B.build_graph(record)
    if g is None:
        return [], 0
    col = colours(g)
    offers = _offers_in_order(record)
    if len(offers) != len(g.action_nodes):
        raise RuntimeError("wl: %d offers but %d action nodes -- the orders have diverged"
                           % (len(offers), len(g.action_nodes)))
    by_colour = collections.defaultdict(list)
    for ai, o in zip(g.action_nodes, offers):
        by_colour[col[ai]].append(o)

    bad = []
    for c, os_ in by_colour.items():
        if len(os_) < 2:
            continue
        ident = {(str(o.get("action_type")), str(o.get("key")),
                  json.dumps(o.get("params") or {}, sort_keys=True, default=str))
                 for o in os_}
        if len(ident) > 1:
            bad.append((c, os_))
    return bad, len(g.action_nodes)


def _synthetic(item_keys, building_keys):
    world = {
        "regions": [{"region": "reg_a", "province": "prov_a", "owner": "me",
                     "x": 10, "y": 10, "adjacent": []}],
        "settlements": [{"region": "reg_a", "units": 4, "x": 10, "y": 10}],
        "armies": [{"cqi": "1", "faction": "me", "region": "reg_a", "x": 10, "y": 10,
                    "has_army": True, "rank": 2, "units": 6, "hp": 18}],
        "hostiles": [], "relations": [],
    }
    prov_offers = []
    for i, bk in enumerate(building_keys):
        for at in ("building_repair", "building_dismantle", "building_cancel"):
            prov_offers.append({"action_type": at, "key": "reg_a@%d" % i,
                                "params": {"region": "reg_a", "slot_index": i,
                                           "building_key": bk}})
    lord_offers = [{"action_type": "items", "key": ik or "item_%d" % i,
                    "params": {"pool_index": i, "item_name": "Warhorse", "item_key": ik}}
                   for i, ik in enumerate(item_keys)]
    return {"campaign": {"faction": "me", "turn": 5, "treasury": 1000},
            "world": world,
            "entities": [
                {"context_kind": "province", "context_id": "reg_a",
                 "state": {"region": "reg_a", "province": "prov_a", "max_slots": 2,
                           "built": {"0": building_keys[0], "1": building_keys[1]},
                           "public_order": 0.0},
                 "offers": prov_offers},
                {"context_kind": "lord", "context_id": "1",
                 "state": {"cqi": "1", "rank": 2, "region": "reg_a"},
                 "offers": lord_offers}]}


def selftest():
    broken = _synthetic(item_keys=["nil", "nil"],
                        building_keys=[None, None])
    fixed = _synthetic(item_keys=["anc_horse_a", "anc_horse_b"],
                       building_keys=["bldg_a", "bldg_b"])
    bad_b, n_b = violations(broken)
    bad_f, n_f = violations(fixed)
    n_broken = sum(len(o) for _c, o in bad_b)
    n_fixed = sum(len(o) for _c, o in bad_f)
    print("  old collector output : %d of %d action nodes inseparable" % (n_broken, n_b))
    print("  fixed output         : %d of %d action nodes inseparable" % (n_fixed, n_f))
    ok = n_broken > 0 and n_fixed == 0
    print("  %s the check fails on the bug and passes on the fix"
          % ("ok  " if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--selftest" in argv:
        return selftest()
    target = common.cli_path(argv, ("--n",))
    if not target:
        target = common.RUN_DIR
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 40
    verbose = "--verbose" in argv

    from decisions.store import DecisionStore
    run = target if os.path.isdir(target) else os.path.dirname(target)
    st = DecisionStore(run, readonly=True)
    hi = st.max_decision_id()
    dids = list(range(max(1, hi - n + 1), hi + 1))

    n_graphs = n_actions = n_bad_actions = 0
    graphs_with_bad = 0
    pairs = collections.Counter()
    for did in dids:
        rec = st.read_decision(did)
        bad, n_act = violations(rec)
        if not n_act:
            continue
        n_graphs += 1
        n_actions += n_act
        if bad:
            graphs_with_bad += 1
        for _c, os_ in bad:
            n_bad_actions += len(os_)
            types = tuple(sorted({str(o.get("action_type")) for o in os_}))
            pairs[types] += 1
            if verbose:
                print("  class of %d: %s" % (len(os_), [
                    (o.get("action_type"), o.get("key")) for o in os_][:4]))
    st.close()

    print("graphs                : %d" % n_graphs)
    print("action nodes          : %d" % n_actions)
    print("in a violating class  : %d (%.1f%%)"
          % (n_bad_actions, 100.0 * n_bad_actions / max(n_actions, 1)))
    print("graphs with any       : %d of %d" % (graphs_with_bad, n_graphs))
    if pairs:
        print("\nby action type:")
        for t, c in pairs.most_common(15):
            print("   %-46s %d class(es)" % ("+".join(t), c))
    print("\n%s" % ("WL identity OK -- every distinguishable action is distinguishable"
                    if not n_bad_actions else
                    "%d ACTION NODE(S) A MESSAGE-PASSING NET CANNOT SEPARATE"
                    % n_bad_actions))
    return 1 if n_bad_actions else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
