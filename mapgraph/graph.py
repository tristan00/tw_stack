from __future__ import annotations

import math

HOSTILE_KINDS = {"army": ("enemy", "army"), "neutral_army": ("neutral", "army"),
                 "hero": ("enemy", "character"), "neutral_hero": ("neutral", "character"),
                 "settlement": ("enemy", "settlement")}


def build(armies=(), settlements=(), hostiles=(), faction=None):
    nodes = []

    for c in armies or ():
        if c.get("cqi") is None:
            raise ValueError("character without cqi: %r" % (c,))
        nodes.append({"id": "own:c%s" % c["cqi"],
                      "kind": "army" if c.get("has_army") else "character",
                      "allegiance": "own", "faction": faction,
                      "x": c.get("x"), "y": c.get("y"),
                      "units": c.get("units"), "hp": c.get("hp"),
                      "stance": c.get("stance"), "subtype": c.get("subtype"),
                      "region": c.get("region"), "province": c.get("province"),
                      "is_leader": bool(c.get("is_leader")), "is_capital": None})

    for s in settlements or ():
        if not s.get("region"):
            raise ValueError("settlement without region: %r" % (s,))
        nodes.append({"id": "own:s:%s" % s["region"], "kind": "settlement",
                      "allegiance": "own", "faction": faction,
                      "x": s.get("x"), "y": s.get("y"),
                      "units": s.get("units"), "hp": None,
                      "stance": None, "subtype": None,
                      "region": s["region"], "province": s.get("province"),
                      "is_leader": False, "is_capital": bool(s.get("capital"))})

    for h in hostiles or ():
        spec = HOSTILE_KINDS.get(str(h.get("kind") or ""))
        if spec is None:
            raise ValueError("unknown hostile kind %r in %r" % (h.get("kind"), h))
        allegiance, kind = spec
        if kind == "settlement":
            if not h.get("region"):
                raise ValueError("hostile settlement without region: %r" % (h,))
            nid = "%s:s:%s" % (allegiance, h["region"])
        else:
            if h.get("cqi") is None:
                raise ValueError("hostile force without cqi: %r" % (h,))
            nid = "%s:c%s" % (allegiance, h["cqi"])
        nodes.append({"id": nid, "kind": kind,
                      "allegiance": allegiance, "faction": h.get("faction"),
                      "x": h.get("x"), "y": h.get("y"),
                      "units": h.get("units"), "hp": h.get("hp"),
                      "stance": h.get("stance"), "subtype": None,
                      "region": h.get("region"), "province": h.get("province"),
                      "is_leader": False, "is_capital": None})

    seen = {}
    for i, n in enumerate(nodes):
        if n["id"] in seen:
            raise ValueError("duplicate node id %r at %d and %d" % (n["id"], seen[n["id"]], i))
        seen[n["id"]] = i
        n["strength"] = n["hp"] if n["hp"] is not None else n["units"]
        n["positioned"] = n["x"] is not None and n["y"] is not None

    placed = [i for i, n in enumerate(nodes) if n["positioned"]]
    if nodes and not placed:
        raise ValueError("no node of %d has a position -- refusing to build an edgeless "
                         "graph from positional data" % len(nodes))

    edges = []
    for a, i in enumerate(placed):
        for j in placed[a + 1:]:
            dx = nodes[j]["x"] - nodes[i]["x"]
            dy = nodes[j]["y"] - nodes[i]["y"]
            edges.append({"i": i, "j": j, "dist": math.sqrt(dx * dx + dy * dy)})

    return {"nodes": nodes, "edges": edges}
