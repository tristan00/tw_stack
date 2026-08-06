from __future__ import annotations

import math

ALLEGIANCES = ("own", "enemy", "neutral")
KINDS = ("army", "character", "settlement")
PREFIX = "mg"


def center(graph, ego_id):
    nodes = graph["nodes"]
    ego = None
    for n in nodes:
        if n["id"] == ego_id:
            ego = n
            break
    if ego is None:
        raise KeyError("ego %r is not a node in this graph" % (ego_id,))
    if not ego["positioned"]:
        raise ValueError("ego %r has no position -- cannot center the graph on it" % (ego_id,))

    out = []
    for n in nodes:
        if n["id"] == ego_id:
            continue
        if not n["positioned"]:
            continue
        dx = n["x"] - ego["x"]
        dy = n["y"] - ego["y"]
        dist = math.sqrt(dx * dx + dy * dy)
        r = dict(n)
        r["dx"] = dx
        r["dy"] = dy
        r["dist"] = dist
        r["bearing_sin"] = dy / dist if dist else 0.0
        r["bearing_cos"] = dx / dist if dist else 0.0
        out.append(r)
    return ego, out


def pool(graph, ego_id):
    ego, rows = center(graph, ego_id)
    groups = {(a, k): [] for a in ALLEGIANCES for k in KINDS}
    for r in rows:
        groups[(r["allegiance"], r["kind"])].append(r)

    cols = {}
    for a in ALLEGIANCES:
        for k in KINDS:
            g = groups[(a, k)]
            base = "%s_%s_%s" % (PREFIX, a, k)
            strengths = [r["strength"] for r in g if r["strength"] is not None]
            dists = [r["dist"] for r in g]
            cols[base + "_n"] = len(g)
            cols[base + "_strength_sum"] = float(sum(strengths)) if strengths else 0.0
            cols[base + "_strength_max"] = float(max(strengths)) if strengths else None
            cols[base + "_dist_min"] = float(min(dists)) if dists else None
            cols[base + "_dist_mean"] = float(sum(dists) / len(dists)) if dists else None
            cols[base + "_dx_mean"] = float(sum(r["dx"] for r in g) / len(g)) if g else None
            cols[base + "_dy_mean"] = float(sum(r["dy"] for r in g) / len(g)) if g else None

    cols["%s_ego_strength" % PREFIX] = ego["strength"]
    cols["%s_ego_is_leader" % PREFIX] = 1 if ego["is_leader"] else 0
    cols["%s_n_total" % PREFIX] = len(rows)
    return cols


def columns():
    names = []
    for a in ALLEGIANCES:
        for k in KINDS:
            base = "%s_%s_%s" % (PREFIX, a, k)
            names.extend([base + s for s in ("_n", "_strength_sum", "_strength_max",
                                             "_dist_min", "_dist_mean", "_dx_mean", "_dy_mean")])
    names.extend(["%s_ego_strength" % PREFIX, "%s_ego_is_leader" % PREFIX,
                  "%s_n_total" % PREFIX])
    return names
