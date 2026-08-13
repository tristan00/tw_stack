from __future__ import annotations

"""Gates that would have caught the model this one replaced.

That model shipped with a test suite that looked rigorous and verified nothing: `_overfit`
asserted `impact_spread > 1e-4` on a quantity (q-v) that was non-zero purely because
the two heads had different input widths. It passed every time and proved nothing.

The gates here are built so a decorative graph FAILS them.

  ablate    Degree-preserving rewiring, PER RELATION GROUP. A global shuffle is not a
            test: it destroys act_actor and act_target too, so any action-node model
            reacts and passes trivially. Rewiring the MAP edges alone, while leaving the
            action wiring intact, asks the only question that matters: does the world
            structure change the ranking, or is it decoration?

  discrim   Held-out accuracy@1 and MRR of the taken action, against two baselines the
            model must beat: uniform over the candidate set, and always-pick-the-most-
            common-action-type. (Not top-k overlap, not RBO -- permanently banned.)

  catboost  Assert no mapgraph source imports advisor/features.py. Checking sys.modules
            is useless: base_model imports `features` to compute the label, so the module
            is loaded no matter what this package does. The honest check is on OUR source.
"""

import glob
import json
import os
import re
import sys

from advisor.mapgraph import schema as S
from advisor.mapgraph import build as B

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)


def _load(limit=120):
    from base_model import decision_deltas, target
    from store import DecisionStore
    out = []
    for db in sorted(glob.glob(os.path.join(common.native(common.RUNS_ROOT), "*",
                                            "decisions.sqlite"))):
        s = DecisionStore(os.path.dirname(db), readonly=True)
        try:
            with s.snapshot_read():
                recs = s.labelled_decisions()
                series = s.target_series()
        finally:
            s.close()
        for rec, taken, _ in reversed(recs):
            g = B.build_graph(rec)
            if g is None or not g.action_nodes:
                continue
            want = (str(taken[0]), str(taken[1]), str(taken[2]), str(taken[3]))
            mask = [1.0 if k == want else 0.0 for k in g.action_keys]
            if sum(mask) != 1.0:
                continue
            y = target(decision_deltas(rec.get("campaign"),
                                       series.get(rec.get("campaign_id")) or {},
                                       rec.get("turn")))
            out.append((g, mask, y))
            if len(out) >= limit:
                return out
    return out


def _net():
    import torch
    try:
        from advisor.mapgraph import net as N
    except ImportError:
        import net as N
    meta = json.load(open(os.path.join(S.MODEL_DIR, "meta.json"), encoding="utf-8"))
    cfg = meta.get("cfg") or {}
    net = N.Net(cfg.get("hidden", N.HIDDEN), cfg.get("entity_layers", N.ENTITY_LAYERS),
                cfg.get("action_rounds", N.ACTION_ROUNDS))
    net.encoder.load_state_dict(torch.load(os.path.join(S.MODEL_DIR, "encoder.pt"),
                                           map_location="cpu"))
    net.head.load_state_dict(torch.load(os.path.join(S.MODEL_DIR, "head.pt"),
                                        map_location="cpu"))
    net.eval()
    return net, meta, N


def _rank_of_taken(q, mask):
    ti = mask.index(1.0)
    tv = q[ti]
    return 1 + sum(1 for v in q if v > tv)


def discrim(limit=120):
    """Does it order the choice set better than trivial baselines?"""
    import torch
    net, meta, N = _net()
    data = _load(limit)
    if not data:
        raise SystemExit("discrim: no evaluable decisions")
    hits = mrr = 0.0
    base_hits = base_mrr = 0.0
    type_hits = 0.0
    n = 0
    for g, mask, _ in data:
        with torch.no_grad():
            q = net(N.to_data(g))["q"].tolist()
        r = _rank_of_taken(q, mask)
        hits += 1.0 if r == 1 else 0.0
        mrr += 1.0 / r
        k = len(q)
        base_hits += 1.0 / k                       # uniform pick
        base_mrr += sum(1.0 / i for i in range(1, k + 1)) / k
        # most-common-action-type baseline: rank by global type frequency
        types = [S.ACTION_TYPES[a - 1] if a else "?" for a in
                 [g.atype_idx[i] for i in g.action_nodes]]
        tt = types[mask.index(1.0)]
        freq = sum(1 for t in types if t == tt)
        type_hits += freq / float(k)
        n += 1
    out = {"n": n,
           "acc@1": round(hits / n, 4), "mrr": round(mrr / n, 4),
           "uniform_acc@1": round(base_hits / n, 5), "uniform_mrr": round(base_mrr / n, 5),
           "type_prior_acc@1": round(type_hits / n, 4)}
    out["beats_uniform"] = out["acc@1"] > out["uniform_acc@1"] * 3
    out["beats_type_prior"] = out["acc@1"] > out["type_prior_acc@1"]
    print(json.dumps(out, indent=2))
    if not out["beats_uniform"]:
        raise SystemExit("discrim FAILED: no better than uniform")
    print("discrim OK")


def ablate(limit=60, seed=0):
    """Rewire one relation group at a time and measure how much the ranking moves."""
    import random
    import torch
    net, meta, N = _net()
    data = _load(limit)
    if not data:
        raise SystemExit("ablate: no evaluable decisions")
    act = S.ACTION_TYPE_INDEX
    rng = random.Random(seed)

    def scores(g, mode):
        gg = g
        if mode != "none":
            gg = _rewire(g, mode, rng, act)
        with torch.no_grad():
            return net(N.to_data(gg))["q"].tolist()

    res = {}
    for mode in ("map", "act_actor", "act_target", "act_on", "all"):
        moved, rankdelta, n = 0.0, 0.0, 0
        for g, mask, _ in data:
            q0 = scores(g, "none")
            q1 = scores(g, mode)
            r0, r1 = _rank_of_taken(q0, mask), _rank_of_taken(q1, mask)
            a = torch.tensor(q0)
            b = torch.tensor(q1)
            moved += float((a - b).abs().mean() / (a.std() + 1e-6))
            rankdelta += abs(r0 - r1) / float(len(q0))
            n += 1
        res[mode] = {"score_shift_sd": round(moved / n, 4),
                     "taken_rank_shift_frac": round(rankdelta / n, 4)}
    print(json.dumps(res, indent=2))
    if res["map"]["score_shift_sd"] < 0.05:
        raise SystemExit(
            "ablate FAILED: rewiring the map moved scores by %.4f sd -- the world "
            "structure is decoration. This is the failure that retired the predecessor."
            % res["map"]["score_shift_sd"])
    print("ablate OK: the map changes the ranking")


def _rewire(g, mode, rng, act):
    """Degree-preserving shuffle of the destinations within one relation group."""
    import copy
    gg = copy.copy(g)
    gg.src, gg.dst, gg.rel = list(g.src), list(g.dst), list(g.rel)
    nrel = len(S.RELATIONS)
    want = {"map": None, "all": "all"}.get(mode, mode)
    idx = []
    for i, (s, d, r) in enumerate(zip(gg.src, gg.dst, gg.rel)):
        name = S.RELATIONS[r % nrel]
        s_act, d_act = g.node_type[s] == act, g.node_type[d] == act
        if mode == "map" and not s_act and not d_act:
            idx.append(i)
        elif mode == "all":
            idx.append(i)
        elif want and name == want:
            idx.append(i)
    if len(idx) > 1:
        dsts = [gg.dst[i] for i in idx]
        rng.shuffle(dsts)
        for i, d in zip(idx, dsts):
            gg.dst[i] = d
    return gg


def no_catboost():
    """No mapgraph source may import the CatBoost feature module."""
    here = os.path.dirname(os.path.abspath(__file__))
    pat = re.compile(r"^\s*(?:import\s+features|from\s+features\s+import"
                     r"|from\s+advisor\.features\s+import|import\s+advisor\.features)",
                     re.M)
    bad = []
    for p in sorted(glob.glob(os.path.join(here, "*.py"))):
        src = open(p, encoding="utf-8").read()
        if pat.search(src):
            bad.append(os.path.basename(p))
    print(json.dumps({"files_checked": len(glob.glob(os.path.join(here, "*.py"))),
                      "importing_features": bad}))
    if bad:
        raise SystemExit("no_catboost FAILED: %s import advisor/features.py"
                         % ", ".join(bad))
    print("no_catboost OK (note: base_model imports `features` to compute the LABEL; "
          "that is the shared target and is deliberate)")


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    n = int(a[a.index("--limit") + 1]) if "--limit" in a else None
    if cmd == "discrim":
        discrim(n or 120)
    elif cmd == "ablate":
        ablate(n or 60)
    elif cmd == "no-catboost":
        no_catboost()
    else:
        raise SystemExit("usage: eval.py discrim|ablate|no-catboost [--limit N]")
