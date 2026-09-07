import unittest

import numpy as np

from advisor.mapgraph import build as B, graph_config as GC, schema as S
from advisor.mapgraph.edge_selection import select_edges
from tests.unit.test_gnn_edges import add_node


class GraphModelChecks(unittest.TestCase):
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
