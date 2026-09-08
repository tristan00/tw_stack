import json

import numpy as np
import pytest

from advisor.mapgraph import build as B, graph_config as GC, schema as S
from advisor.mapgraph.edge_selection import select_edges
from bench.gnn_edge_profile import percentile
from tests.test_gnn_edges import add_node

pytestmark = pytest.mark.skip("not part of the suite")


def test_zero_remains_empty_through_tensor_conversion_and_model_backward():
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
        assert tuple(data[name + "_index"].shape) == (2, 0)
    model = GN.from_cfg(dict(T.CFG, hidden=8, entity_layers=1, action_rounds=1))
    q = model(data)["q"]
    assert torch.isfinite(q).all()
    q.sum().backward()
    assert any(p.grad is not None for p in model.parameters())


def test_defaults_match_observed_p95_and_unobserved_policy():
    assert percentile({1: 95, 100: 5}, .95) == 1
    document = json.loads(GC.DEFAULTS_PATH.read_text())
    for name, evidence in document["evidence"].items():
        percentiles = []
        for side in ("forward", "reverse"):
            values = np.repeat([int(v) for v in evidence[side]], list(evidence[side].values()))
            percentiles.append(int(np.quantile(values, .95, method="inverted_cdf")) if len(values) else 0)
        assert document["limits"][name] == max(1, *percentiles), name
