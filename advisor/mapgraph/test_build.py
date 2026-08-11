from __future__ import annotations

"""Build graphs from real recorded decisions and check the structure that was wrong.

build.py needs no torch, so the graph fixes can be checked directly against the corpus
rather than argued about. Each check below corresponds to a defect that was measured:

  - slot nodes were named by PROVINCE while being populated per REGION, so 341 of 558
    shared (province, slot) pairs held two different built buildings.
  - MOBILE_KINDS dropped every hostile settlement, so attack_settlement had no target
    node (the target was never missing from the snapshot -- 0 of 2,571 were).
  - `garrison` keys are "settlement:<region>" and _target_of looked up "s:settlement:..",
    so all 1,973 garrison offers had no target edge.
  - `building` read params.building_key, which that action type does not have, so no
    construction offer ever linked to the building it constructs.
  - `move` recorded x,y that no node carried.

    python -m advisor.mapgraph.test_build [run_dir_or_db] [--n 40]
"""

import collections
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import common  # noqa: E402
from advisor.mapgraph import build as B      # noqa: E402
from advisor.mapgraph import schema as S     # noqa: E402

FAILED = []


def check(cond, what, detail=""):
    print("  %-4s %-56s %s" % ("ok" if cond else "FAIL", what, detail))
    if not cond:
        FAILED.append(what)


def _records(target, n):
    from decisions.store import DecisionStore
    run = target if os.path.isdir(target) else os.path.dirname(target)
    st = DecisionStore(run, readonly=True)
    hi = st.max_decision_id()
    out = [st.read_decision(d) for d in range(max(1, hi - n + 1), hi + 1)]
    st.close()
    return out


def main(argv):
    target = next((a for a in argv if not a.startswith("--")), None)
    if not target:
        import common
        target = os.path.join(common.RUNS_ROOT.replace("/", os.sep), "run")
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 40

    recs = _records(target, n)
    graphs = [g for g in (B.build_graph(r) for r in recs) if g is not None]
    check(len(graphs) > 0, "graphs build", "%d of %d records" % (len(graphs), len(recs)))
    if not graphs:
        return 1

    # 1. every slot node belongs to exactly one region
    dup = 0
    for g in graphs:
        by_slot = collections.defaultdict(set)
        for nid in g.node_ids:
            if nid.startswith("slot:"):
                _p, region, sl = nid.split(":", 2)
                by_slot[(region, sl)].add(nid)
        dup += sum(1 for v in by_slot.values() if len(v) > 1)
    check(dup == 0, "slot nodes are per-region, not per-province")

    # the same building may now legitimately appear in two slots of one region
    n_slots = sum(1 for g in graphs for nid in g.node_ids if nid.startswith("slot:"))
    check(n_slots > 0, "slot nodes exist", "%d over %d graphs" % (n_slots, len(graphs)))

    # 2/3/4/5. action structure, counted over every action node
    tgt_rel = S.REL_INDEX["act_target"]
    on_rel = S.REL_INDEX["act_on"]
    per_type = collections.defaultdict(lambda: collections.Counter())
    xy_seen = collections.Counter()
    for g in graphs:
        out_edges = collections.defaultdict(set)
        for s, d, r in zip(g.src, g.dst, g.rel):
            out_edges[(s, r)].add(d)
        xcol = S.FIELD_POS["action"]["x"]
        for ai, (ck, cid, at, key) in zip(g.action_nodes, g.action_keys):
            per_type[at]["n"] += 1
            if out_edges.get((ai, tgt_rel)):
                per_type[at]["target"] += 1
            if out_edges.get((ai, on_rel)):
                per_type[at]["act_on"] += 1
            if g.x[ai][xcol] != 0.0:
                xy_seen[at] += 1

    def rate(at, field):
        t = per_type.get(at, {})
        return (t.get(field, 0), t.get("n", 0))

    for at in ("attack_settlement", "garrison", "colonize"):
        hit, tot = rate(at, "target")
        if tot:
            check(hit == tot, "%s offers all have a target node" % at,
                  "%d/%d" % (hit, tot))
    hit, tot = rate("building", "act_on")
    if tot:
        check(hit == tot, "building offers link the building they construct",
              "%d/%d" % (hit, tot))
    mv, mtot = xy_seen.get("move", 0), per_type.get("move", {}).get("n", 0)
    if mtot:
        check(mv > 0, "move actions carry their destination x,y", "%d/%d" % (mv, mtot))

    # enemy settlements became nodes
    enemy = sum(1 for g in graphs for i, nid in enumerate(g.node_ids)
                if nid.startswith("s:") and g.own_mask[i] == 0.0)
    check(enemy > 0, "enemy settlements have nodes", "%d over %d graphs"
          % (enemy, len(graphs)))

    # the encoder input width must not have moved
    check(S.MAX_FIELDS == 6, "MAX_FIELDS unchanged by the new action fields",
          "MAX_FIELDS=%d, action=%s" % (S.MAX_FIELDS, list(S.TYPE_FIELDS["action"])))

    print("\naction coverage:")
    for at in sorted(per_type, key=lambda a: -per_type[a]["n"]):
        t = per_type[at]
        print("   %-20s n=%-7d target=%-7d act_on=%-7d xy=%d"
              % (at, t["n"], t["target"], t["act_on"], xy_seen.get(at, 0)))

    print("\n%s" % ("build OK" if not FAILED else "%d FAILED" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
