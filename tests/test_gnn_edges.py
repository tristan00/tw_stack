from types import SimpleNamespace

import numpy as np
import pytest

from advisor.mapgraph import build as B
from advisor.mapgraph import graph_config as GC
from advisor.mapgraph import guard as G
from advisor.mapgraph import schema as S
from advisor.mapgraph.edge_selection import select_edges


def add_node(g, name, point, kind="region"):
    values = {} if point is None else {k: G.Raw(v, name, k) for k, v in zip(("x", "y"), point)}
    return g.add(name, kind, values)


def edge_names(g):
    return {(g.node_ids[a], g.node_ids[b], r) for a, b, r in zip(g.src, g.dst, g.rel)}


def test_every_relation_uses_same_order_and_independent_reverse_cap():
    g = B.Graph(GC.GraphBuildConfig({r: 2 for r in S.RELATIONS}))
    target = add_node(g, "target", (0, 0))
    sources = [add_node(g, name, point) for name, point in
               (("z", (1, 0)), ("a", (0, 1)), ("b", None), ("c", (2, 0)))]
    for relation in S.RELATIONS:
        for source in sources:
            g.edge(source, target, relation)
            g.edge(source, target, relation)
    g.finalize()
    select_edges(g)
    for relation in range(S.N_FORWARD_RELATIONS):
        chosen = [g.node_ids[a] for a, b, r in zip(g.src, g.dst, g.rel) if b == target and r == relation]
        assert chosen == ["a", "z"]
        assert sum(r == relation + S.N_FORWARD_RELATIONS for r in g.rel) == 4
    assert len(edge_names(g)) == len(g.src)


def test_spatial_matches_exhaustive_order_with_ties_and_missing_locations():
    rng = np.random.default_rng(91)
    coordinates = {"node:%03d" % i: tuple(rng.integers(0, 4, size=2)) for i in range(175)}
    coordinates["node:000"] = None
    coordinates["node:001"] = (0, 0)
    coordinates["action:0"] = (0, 0)
    coordinates["action:1"] = None
    coordinates["action:2"] = (400, 400)
    reference = None
    for seed in (2, 7):
        g = B.Graph(GC.GraphBuildConfig({r: 3 for r in S.RELATIONS}))
        names = list(coordinates)
        np.random.default_rng(seed).shuffle(names)
        ids = {name: add_node(g, name, coordinates[name], "action" if name.startswith("action") else "lord")
               for name in names}
        for name in sorted(n for n in names if n.startswith("action")):
            g._context_targets.append((ids[name], ids["node:002"], ids["node:003"]))
        g.finalize()
        select_edges(g)
        actual = edge_names(g)
        near, context = S.REL_INDEX["near"], S.REL_INDEX["near_target"]
        expected = set()
        physical = sorted(n for n in names if n.startswith("node"))
        actions = sorted(n for n in names if n.startswith("action"))

        def key(source, target):
            a, b = coordinates[source], coordinates[target]
            return (sum((x - y) ** 2 for x, y in zip(a, b)) if a is not None and b is not None else float("inf"), source)

        for target in physical:
            for relation, candidates in ((near, [n for n in physical if n < target]),
                                         (near + S.N_FORWARD_RELATIONS, [n for n in physical if n > target]),
                                         (context, actions if target not in ("node:002", "node:003") else [])):
                expected.update((source, target, relation) for source in sorted(candidates, key=lambda n: key(n, target))[:3])
        for target in actions:
            candidates = [n for n in physical if n not in ("node:002", "node:003")]
            expected.update((source, target, context + S.N_FORWARD_RELATIONS)
                            for source in sorted(candidates, key=lambda n: key(n, target))[:3])
        assert actual == expected
        if reference is not None:
            assert actual == reference
        reference = actual


def test_configuration_is_complete_strict_and_serializable():
    assert set(GC.default_limits()) == set(S.RELATIONS)
    assert all(v >= 1 for v in GC.default_limits().values())
    c = GC.from_dict({"edge_limits": {"near": 0}})
    assert c.edge_limits["near"] == 0
    assert GC.from_dict(c.as_dict()).fingerprint() == c.fingerprint()
    for limits in ({"near": -1}, {"near": 1.5}, {"unknown": 1}):
        with pytest.raises(ValueError):
            GC.from_dict({"edge_limits": limits})
    with pytest.raises(TypeError):
        GC.from_dict({"spatial_max_distance": 50})


def test_tuner_uses_only_measured_edge_caps():
    from advisor.mapgraph.optimize_greedy import _graph_space
    calls = []
    trial = SimpleNamespace(suggest_int=lambda name, low, high, step: calls.append(name) or low,
                            suggest_categorical=None)
    config = _graph_space(trial)
    assert len(calls) == len(GC.TUNING_RANGES)
    for relation in GC.TUNING_RANGES:
        assert config.edge_limits[relation] == 0


def test_gpu_budget_uses_free_memory_and_optional_lower_ceiling():
    from advisor.mapgraph.optimize_greedy import _graph_gate
    torch = SimpleNamespace(cuda=SimpleNamespace(mem_get_info=lambda: (8 * 2**30, 32 * 2**30)))
    assert _graph_gate(torch, None, .7)["allowed_graph_gib"] == 5.6
    assert _graph_gate(torch, 2, .7)["allowed_graph_gib"] == 2
    assert _graph_gate(torch, 20, .7)["allowed_graph_gib"] == 5.6
