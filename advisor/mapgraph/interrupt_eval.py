from __future__ import annotations


import copy
import json
import math
import os
import random
import sys

from advisor.mapgraph import schema as S
from advisor.mapgraph import interrupt_build as IB
from advisor.mapgraph import interrupt_train as IT

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

DECISION_WORLD_KEYS = ("relations", "citizenry", "war_graph")


def _rows():
    from store import DecisionStore
    out = []
    for db in common.run_dbs():
        st = DecisionStore(os.path.dirname(db), readonly=True)
        try:
            rows = st.interrupt_rows()
            ctx = IT.context_for(st, rows)
        finally:
            st.close()
        for r in rows:
            c = ctx.get(r["interrupt_id"])
            if c is not None:
                out.append((r, c))
    return out


def build():
    checked = skipped = 0
    fails = []
    screens = {}
    opt_keys = {}
    thin_context = []
    for r, c in _rows():
        opts = sorted(r.get("options") or {})
        if len(opts) < 2:
            skipped += 1
            continue
        rec = c["record"]
        missing = [k for k in DECISION_WORLD_KEYS if k not in (rec.get("world") or {})]
        if missing:
            thin_context.append((r["screen"], missing))
        g = IB.build_screen_graph(rec, r["screen"], opts, meta=r.get("options"),
                                  panel=r.get("panel"))
        checked += 1
        screens[r["screen"]] = screens.get(r["screen"], 0) + 1
        if g is None:
            fails.append("%s: no graph" % r["screen"])
            continue
        if len(g.action_nodes) != len(opts):
            fails.append("%s: %d action nodes for %d options"
                         % (r["screen"], len(g.action_nodes), len(opts)))
        want = set(S.SCREEN_ACTION_TYPES)
        leaked = sorted({S.ACTION_TYPES[g.atype_idx[i] - 1] if g.atype_idx[i] else "?"
                         for i in g.action_nodes} - want)
        if leaked:
            fails.append("%s: map actions leaked into the candidate set: %s"
                         % (r["screen"], leaked))
        if r.get("chosen") and IB.taken_mask(g, opts, r["chosen"]) is None:
            fails.append("%s: chosen %r not among its own options" % (r["screen"], r["chosen"]))
        for o in opts:
            k = IB._option_key(r["screen"], o, r.get("options"))
            opt_keys.setdefault(S.cat_global("screen_option", k), set()).add(k)

    collide = {i: sorted(v) for i, v in opt_keys.items() if len(v) > 1}
    if collide:
        fails.append("screen_option ids collide: %s" % list(collide.values())[:3])
    out = {"checked": checked, "single_option_skipped": skipped, "screens": screens,
           "distinct_option_keys": len(opt_keys), "id_collisions": len(collide),
           "context_missing_decision_world": len(thin_context), "failures": fails[:10]}
    print(json.dumps(out, indent=2))
    if thin_context:
        raise SystemExit(
            "build FAILED: %d screens were given a context with no %s -- that is the "
            "interrupt's own world blob, not a decision snapshot. Garrisons become field "
            "armies and diplomacy disappears. First: %s"
            % (len(thin_context), "/".join(DECISION_WORLD_KEYS), thin_context[:3]))
    if fails:
        raise SystemExit("build FAILED: %s" % "; ".join(fails[:5]))
    if not checked:
        raise SystemExit("build FAILED: no multi-option screens in the corpus to check")
    print("build OK: %d screens, options are the candidate set, context is a decision "
          "snapshot" % checked)


def _held_out():
    from base_model import stable_split
    from advisor.mapgraph import interrupt_rank as IR
    r = IR.Ranker()
    if not r.ready:
        raise SystemExit("no interrupt graph model on disk (or its schema is stale) -- "
                         "train one first: python -m advisor.mapgraph.interrupt_train train")
    rows = [(row, c) for row, c in _rows() if len(row.get("options") or {}) >= 2
            and row.get("chosen")]
    groups = [row.get("campaign_id") for row, _c in rows]
    val_idx, _t = stable_split(len(rows), groups)
    return r, [rows[i] for i in val_idx]


def discrim():
    import torch
    from advisor.mapgraph import net as N
    r, val = _held_out()
    per = {}
    for row, c in val:
        opts = sorted(row["options"])
        g = IB.build_screen_graph(c["record"], row["screen"], opts,
                                  meta=row.get("options"), panel=row.get("panel"))
        if g is None:
            continue
        with torch.no_grad():
            q = r.net(N.to_data(g))["q"].tolist()
        ti = opts.index(str(row["chosen"])) if str(row["chosen"]) in opts else None
        if ti is None:
            continue
        rank = 1 + sum(1 for v in q if v > q[ti])
        d = per.setdefault(row["screen"], {"n": 0, "hits": 0.0, "mrr": 0.0, "uni": 0.0})
        d["n"] += 1
        d["hits"] += 1.0 if rank == 1 else 0.0
        d["mrr"] += 1.0 / rank
        d["uni"] += 1.0 / len(opts)
    if not per:
        raise SystemExit("discrim: no held-out screens to score")
    out = {}
    tot = {"n": 0, "hits": 0.0, "mrr": 0.0, "uni": 0.0}
    for k, d in sorted(per.items()):
        out[k] = {"n": d["n"], "acc@1": round(d["hits"] / d["n"], 4),
                  "mrr": round(d["mrr"] / d["n"], 4),
                  "uniform_acc@1": round(d["uni"] / d["n"], 4)}
        for f in tot:
            tot[f] += d[f]
    out["ALL"] = {"n": tot["n"], "acc@1": round(tot["hits"] / tot["n"], 4),
                  "mrr": round(tot["mrr"] / tot["n"], 4),
                  "uniform_acc@1": round(tot["uni"] / tot["n"], 4)}
    out["beats_uniform"] = out["ALL"]["acc@1"] > out["ALL"]["uniform_acc@1"]
    print(json.dumps(out, indent=2))
    print("discrim OK (reported, not gated -- %d held-out screens)" % out["ALL"]["n"])


def _rewire(g, group, rng):
    act = S.ACTION_TYPE_INDEX
    scr = S.NODE_TYPES.index("screen")
    gg = copy.copy(g)
    gg.src, gg.dst, gg.rel = list(g.src), list(g.dst), list(g.rel)
    nrel = len(S.RELATIONS)
    idx = []
    for i, (s, d, rl) in enumerate(zip(gg.src, gg.dst, gg.rel)):
        name = S.RELATIONS[rl % nrel]
        kinds = {g.node_type[s], g.node_type[d]}
        if group == "map" and not (kinds & {act, scr}):
            idx.append(i)
        elif group == "panel" and name in ("screen_fact", "demanded", "offered", "in_treaty"):
            idx.append(i)
        elif group == "option" and name == "act_on" and act in kinds:
            idx.append(i)
    if len(idx) > 1:
        dsts = [gg.dst[i] for i in idx]
        rng.shuffle(dsts)
        for i, d in zip(idx, dsts):
            gg.dst[i] = d
    return gg, len(idx)


def ablate(seed=0):
    import torch
    from advisor.mapgraph import net as N
    r, val = _held_out()
    rng = random.Random(seed)
    res = {}
    for group in ("map", "panel", "option"):
        moved, n, edges = 0.0, 0, 0
        for row, c in val:
            opts = sorted(row["options"])
            g = IB.build_screen_graph(c["record"], row["screen"], opts,
                                      meta=row.get("options"), panel=row.get("panel"))
            if g is None:
                continue
            gg, k = _rewire(g, group, rng)
            with torch.no_grad():
                q0 = torch.tensor(r.net(N.to_data(g))["q"].tolist())
                q1 = torch.tensor(r.net(N.to_data(gg))["q"].tolist())
            moved += float((q0 - q1).abs().mean() / (q0.std() + 1e-6))
            edges += k
            n += 1
        res[group] = {"score_shift_sd": round(moved / max(n, 1), 4),
                      "edges_rewired": edges, "screens": n}
    print(json.dumps(res, indent=2))
    m, p = res["map"]["score_shift_sd"], res["panel"]["score_shift_sd"]
    if p > 0 and m < p * 0.25:
        print("NOTE: the panel moves the ranking %.1fx more than the world does. If that "
              "holds as the corpus grows, this is a panel-fact ranker with a graph "
              "attached." % (p / max(m, 1e-9)))
    print("ablate OK (reported, not gated)")


if __name__ == "__main__":
    common.require_venv()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        build()
    elif cmd == "discrim":
        discrim()
    elif cmd == "ablate":
        ablate()
    else:
        raise SystemExit("usage: interrupt_eval.py build|discrim|ablate")
