from __future__ import annotations

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "reference"))
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

ALLEGIANCE = ("own", "enemy", "neutral")
KIND = ("army", "character", "settlement", "ruin")

HOSTILE_KIND = {"army": ("enemy", "army"),
                "neutral_army": ("neutral", "army"),
                "hero": ("enemy", "character"),
                "neutral_hero": ("neutral", "character"),
                "settlement": ("enemy", "settlement")}

NODE_FIELDS = (
    tuple("alleg_" + a for a in ALLEGIANCE)
    + tuple("kind_" + k for k in KIND)
    + ("units", "hp", "ap_pct", "is_leader", "is_capital", "is_ego",
       "dx", "dy", "dist", "dir_sin", "dir_cos")
)
EDGE_FIELDS = ("dist", "dir_sin", "dir_cos", "same_allegiance", "same_province")

DIST_SCALE = 100.0


def _f(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def nodes_of(world, faction=None):
    out = []
    for c in (world.get("armies") or ()):
        if c.get("cqi") is None:
            continue
        out.append({"id": "own:c%s" % c["cqi"],
                    "allegiance": "own",
                    "kind": "army" if c.get("has_army") else "character",
                    "x": c.get("x"), "y": c.get("y"),
                    "units": c.get("units"), "hp": c.get("hp"),
                    "ap_pct": c.get("ap_pct"),
                    "is_leader": bool(c.get("is_leader")), "is_capital": False,
                    "province": c.get("province"), "cqi": str(c.get("cqi"))})
    for s in (world.get("settlements") or ()):
        if not s.get("region"):
            continue
        out.append({"id": "own:s:%s" % s["region"], "allegiance": "own",
                    "kind": "settlement", "x": s.get("x"), "y": s.get("y"),
                    "units": s.get("units"), "hp": None, "ap_pct": None,
                    "is_leader": False, "is_capital": bool(s.get("capital")),
                    "province": s.get("province"), "region": s.get("region")})
    for h in (world.get("hostiles") or ()):
        spec = HOSTILE_KIND.get(str(h.get("kind") or ""))
        if spec is None:
            continue
        alleg, kind = spec
        if kind == "settlement":
            if not h.get("region"):
                continue
            nid = "%s:s:%s" % (alleg, h["region"])
        else:
            if h.get("cqi") is None:
                continue
            nid = "%s:c%s" % (alleg, h["cqi"])
        out.append({"id": nid, "allegiance": alleg, "kind": kind,
                    "x": h.get("x"), "y": h.get("y"),
                    "units": h.get("units"), "hp": h.get("hp"), "ap_pct": None,
                    "is_leader": False, "is_capital": False,
                    "province": h.get("province"), "region": h.get("region"),
                    "cqi": None if h.get("cqi") is None else str(h.get("cqi"))})
    for r in (world.get("ruins") or ()):
        if not r.get("region"):
            continue
        out.append({"id": "neutral:r:%s" % r["region"], "allegiance": "neutral",
                    "kind": "ruin", "x": r.get("x"), "y": r.get("y"),
                    "units": None, "hp": None, "ap_pct": None,
                    "is_leader": False, "is_capital": False,
                    "province": None, "region": r.get("region")})
    return [n for n in out if n.get("x") is not None and n.get("y") is not None]


def _node_vector(n, ego):
    v = [1.0 if n["allegiance"] == a else 0.0 for a in ALLEGIANCE]
    v += [1.0 if n["kind"] == k else 0.0 for k in KIND]
    v += [_f(n.get("units")), _f(n.get("hp")), _f(n.get("ap_pct")),
          1.0 if n.get("is_leader") else 0.0,
          1.0 if n.get("is_capital") else 0.0,
          1.0 if (ego is not None and n["id"] == ego["id"]) else 0.0]
    if ego is None:
        v += [0.0, 0.0, 0.0, 0.0, 1.0]
    else:
        dx = _f(n.get("x")) - _f(ego.get("x"))
        dy = _f(n.get("y")) - _f(ego.get("y"))
        d = math.hypot(dx, dy)
        v += [dx / DIST_SCALE, dy / DIST_SCALE, d / DIST_SCALE,
              (dy / d) if d else 0.0, (dx / d) if d else 1.0]
    return v


def _edges(nodes, mode="knn", k=8):
    n = len(nodes)
    idx = []
    if n < 2:
        return idx
    if mode == "full":
        return [(i, j) for i in range(n) for j in range(n) if i != j]
    for i in range(n):
        d = []
        for j in range(n):
            if i == j:
                continue
            dx = _f(nodes[j].get("x")) - _f(nodes[i].get("x"))
            dy = _f(nodes[j].get("y")) - _f(nodes[i].get("y"))
            d.append((math.hypot(dx, dy), j))
        d.sort()
        for _dist, j in d[:k]:
            idx.append((i, j))
    return idx


def _edge_vector(a, b):
    dx = _f(b.get("x")) - _f(a.get("x"))
    dy = _f(b.get("y")) - _f(a.get("y"))
    d = math.hypot(dx, dy)
    same_p = 1.0 if (a.get("province") and a.get("province") == b.get("province")) else 0.0
    return [d / DIST_SCALE, (dy / d) if d else 0.0, (dx / d) if d else 1.0,
            1.0 if a["allegiance"] == b["allegiance"] else 0.0, same_p]


def ego_id_for(entity):
    kind = entity.get("context_kind")
    cid = str(entity.get("context_id"))
    if kind in ("lord", "hero"):
        return "own:c%s" % cid
    if kind == "province":
        return "own:s:%s" % cid
    return None


def to_data(record, ego=None, mode="knn", k=8, y=None):
    import torch
    from torch_geometric.data import Data

    world = record.get("world") or {}
    camp = record.get("campaign") or {}
    ns = nodes_of(world, camp.get("faction"))
    if not ns:
        return None
    ego_node = None
    if ego:
        for n in ns:
            if n["id"] == ego:
                ego_node = n
                break
    x = torch.tensor([_node_vector(n, ego_node) for n in ns], dtype=torch.float)
    pairs = _edges(ns, mode=mode, k=k)
    if pairs:
        edge_index = torch.tensor([[a for a, _ in pairs], [b for _, b in pairs]],
                                  dtype=torch.long)
        edge_attr = torch.tensor([_edge_vector(ns[a], ns[b]) for a, b in pairs],
                                 dtype=torch.float)
    else:
        edge_index = torch.zeros((2, 0), dtype=torch.long)
        edge_attr = torch.zeros((0, len(EDGE_FIELDS)), dtype=torch.float)
    d = Data(x=x, edge_index=edge_index, edge_attr=edge_attr)
    d.node_ids = [n["id"] for n in ns]
    d.ego_index = ns.index(ego_node) if ego_node is not None else -1
    d.campaign_id = record.get("campaign_id")
    d.turn = record.get("turn")
    d.decision_id = record.get("decision_id")
    if y is not None:
        d.y = torch.tensor([float(y)], dtype=torch.float)
    return d


def dataset(runs_root=None, limit=None, mode="knn", k=8, log=print):
    from base_model import decision_deltas, target
    from model import RUNS_ROOT
    from store import DecisionStore

    root = runs_root or RUNS_ROOT
    import glob
    out = []
    for db in sorted(glob.glob(os.path.join(root, "*", "decisions.sqlite"))):
        run_dir = os.path.dirname(db)
        s = DecisionStore(run_dir, readonly=True)
        try:
            with s.snapshot_read():
                lab = s.labelled_decisions()
                series = s.target_series()
        finally:
            s.close()
        for rec, taken, _counted in lab:
            turns = series.get(rec.get("campaign_id")) or {}
            y = target(decision_deltas(rec.get("campaign"), turns, rec.get("turn")))
            if y is None:
                continue
            ent = {"context_kind": taken[0], "context_id": taken[1]}
            d = to_data(rec, ego=ego_id_for(ent), mode=mode, k=k, y=y)
            if d is not None:
                out.append(d)
            if limit and len(out) >= limit:
                return out
    return out
