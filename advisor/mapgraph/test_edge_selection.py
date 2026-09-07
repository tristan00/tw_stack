import unittest

import numpy as np

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


class EdgeSelectionTests(unittest.TestCase):
    def test_every_relation_uses_same_order_and_independent_reverse_cap(self):
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
            self.assertEqual(chosen, ["a", "z"])
            self.assertEqual(sum(r == relation + S.N_FORWARD_RELATIONS for r in g.rel), 4)
        self.assertEqual(len(edge_names(g)), len(g.src))

    def test_spatial_matches_exhaustive_order_with_ties_and_missing_locations(self):
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
            self.assertEqual(actual, expected)
            if reference is not None:
                self.assertEqual(actual, reference)
            reference = actual

    def test_zero_remains_empty_through_tensor_conversion_and_model_backward(self):
        import torch
        from advisor.mapgraph import net as N
        from advisor.mapgraph import greedy_net as GN
        from advisor.mapgraph import train as T
        torch.set_num_threads(1)
        g = B.Graph(GC.GraphBuildConfig({r: 0 for r in S.RELATIONS}))
        actor = add_node(g, "actor", (0, 0), "lord")
        action = add_node(g, "action", (1, 0), "action")
        for relation in S.RELATIONS:
            g.edge(action, actor, relation)
        g._context_targets.append((action, None, None))
        g.g_ctx = [0.0] * S.G_CTX_DIM
        g.finalize()
        select_edges(g)
        data = N.to_data(g, y=1, taken=[1])
        for name in ("edge", "a2e", "e2a"):
            self.assertEqual(tuple(data[name + "_index"].shape), (2, 0))
        model = GN.from_cfg(dict(T.CFG, hidden=8, entity_layers=1, action_rounds=1))
        q = model(data)["q"]
        self.assertTrue(torch.isfinite(q).all())
        q.sum().backward()
        self.assertTrue(any(p.grad is not None for p in model.parameters()))

    def test_configuration_is_complete_strict_and_serializable(self):
        self.assertEqual(set(GC.default_limits()), set(S.RELATIONS))
        self.assertTrue(all(v >= 1 for v in GC.default_limits().values()))
        c = GC.from_dict({"edge_limits": {"near": 0}})
        self.assertEqual(c.edge_limits["near"], 0)
        self.assertEqual(GC.from_dict(c.as_dict()).fingerprint(), c.fingerprint())
        for limits in ({"near": -1}, {"near": 1.5}, {"unknown": 1}):
            with self.assertRaises(ValueError):
                GC.from_dict({"edge_limits": limits})
        with self.assertRaises(TypeError):
            GC.from_dict({"spatial_max_distance": 50})

    def test_tuner_uses_only_measured_edge_caps(self):
        from unittest.mock import Mock
        from advisor.mapgraph.optimize_greedy import _graph_space
        trial = Mock()
        trial.suggest_int.side_effect = lambda name, low, high, step: low
        config = _graph_space(trial)
        self.assertEqual(trial.suggest_int.call_count, len(GC.TUNING_RANGES))
        for relation in GC.TUNING_RANGES:
            self.assertEqual(config.edge_limits[relation], 0)
        trial.suggest_categorical.assert_not_called()

    def test_defaults_match_observed_medians_and_unobserved_policy(self):
        import json
        from bench.gnn_edge_profile import median
        self.assertEqual(median({1: 1, 100: 1}), 51)
        document = json.loads(GC.DEFAULTS_PATH.read_text())
        for name, evidence in document["evidence"].items():
            medians = []
            for side in ("forward", "reverse"):
                values = np.repeat([int(v) for v in evidence[side]], list(evidence[side].values()))
                medians.append(int(np.ceil(np.median(values))) if len(values) else 0)
            self.assertEqual(document["limits"][name], max(1, *medians), name)


if __name__ == "__main__":
    unittest.main()
