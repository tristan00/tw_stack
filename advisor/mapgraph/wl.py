from __future__ import annotations

"""Weisfeiler-Leman identity: can the network tell two candidate actions apart at all?

A message-passing network cannot distinguish two nodes that WL refinement gives the same
colour -- not at this depth, not at any depth, not with more parameters. So if the action
the agent took and an action it did not take end up the same colour, the loss is asking
for something the architecture cannot express, and every gradient spent on that pair is
noise. Measured on the previous corpus: 543 of 544 graphs contained at least one violating
class, 24.8% of action nodes were in one, and 47.4% of taken actions were indistinguishable
from a candidate that was not taken.

The tolerance is a DERIVED PREDICATE, never a per-type whitelist. A class of two or more
action nodes is acceptable only if every offer in it has identical
(action_type, action_key, params) -- i.e. they really are the same action offered twice,
and picking either is the same decision. Anything else is a defect, whoever it belongs to.
That rule is why this is a check and not a metric: nobody gets to add their action type to
an exemption list.

    python -m advisor.mapgraph.wl [run_dir] [--n 40] [--verbose]

Exit code 1 means at least one pair of genuinely different actions is invisible to the
network.
"""

import collections
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import common  # noqa: E402
from advisor.mapgraph import build as B      # noqa: E402
from advisor.mapgraph import schema as S     # noqa: E402

MAX_ROUNDS = 40


def colours(g, max_rounds=MAX_ROUNDS):
    """WL refinement to convergence. Returns the final colour per node.

    The initial colour is everything the encoder can see about a node on its own: its
    type, its categorical indices and its scalars. Refinement then folds in the multiset
    of (relation, neighbour colour), which is exactly what one message-passing layer can
    condition on -- the invariants file checks the network really does condition on
    (x_i, x_j, rel) jointly, so this is a faithful upper bound on its resolving power.
    """
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
            return nxt                    # the partition stopped refining
        cur = nxt
    return cur


def _offers_in_order(record):
    """The offers in the order build_graph adds their action nodes."""
    out = []
    for e in record.get("entities") or []:
        for o in e.get("offers") or []:
            out.append(o)
    return out


def violations(record, g=None):
    """[(colour, [offer, ...])] for every class of action nodes that a message-passing
    network cannot separate but that are not the same action."""
    g = g or B.build_graph(record)
    if g is None:
        return [], 0
    col = colours(g)
    offers = _offers_in_order(record)
    if len(offers) != len(g.action_nodes):
        # build_graph and this walk disagree about order, which would make every verdict
        # below meaningless. Say so rather than report a number.
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
    """One province with two slots and one lord, offering the action types that dominate
    the remaining violations. Parameterised so the same record can be built the way the
    old collector emitted it and the way the fixed one does."""
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
    """The checker has to be seen failing before a green run means anything."""
    broken = _synthetic(item_keys=["nil", "nil"],
                        building_keys=[None, None])      # the old collector
    fixed = _synthetic(item_keys=["anc_horse_a", "anc_horse_b"],
                       building_keys=["bldg_a", "bldg_b"])   # the fixed one
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
    target = next((a for a in argv if not a.startswith("--")), None)
    if not target:
        import common
        target = os.path.join(common.RUNS_ROOT.replace("/", os.sep), "run")
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
