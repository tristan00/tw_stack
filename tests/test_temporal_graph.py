from dataclasses import replace
import ast
from pathlib import Path

import numpy as np
import pytest

from advisor.temporal_graph import ActionQueries, EdgeKind, GraphBuildState, GraphConfig, GraphLimits, History, LearnedMetric, NodeKind, build_temporal_graph
from advisor.temporal_graph.config import KERNELS, LAYOUTS, NODE_VIEWS
from advisor.temporal_graph.examples import campaign


@pytest.fixture
def data():
    return campaign(12, 9)


def edges(graph):
    return set(map(tuple, graph.edge_index.T.tolist()))


def check_graph(graph):
    nodes = graph.nodes
    src, dst = graph.edge_index
    assert np.all(nodes.tick[src] <= nodes.tick[dst])
    assert np.all(nodes.kind[src] != NodeKind.QUERY)
    assert np.all(src != dst)
    assert len(edges(graph)) == len(src)
    assert np.all(np.isfinite(graph.edge_score))
    assert np.all(graph.decision_gap >= 0)
    assert np.all(graph.turn_gap >= 0)
    assert nodes.member_offsets[0] == 0
    assert nodes.member_offsets[-1] == len(nodes.members)
    assert np.all(np.diff(nodes.member_offsets) >= 0)


@pytest.mark.parametrize("view", NODE_VIEWS)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_layout_and_node_invariants(data, view, layout):
    history, queries = data
    graph = build_temporal_graph(history, queries, GraphConfig(node_view=view, layout=layout, degree=3, arity=4))
    check_graph(graph)
    assert len(graph.query_index) == len(queries.ids)
    assert set(graph.query_ids) == set(queries.ids)
    src, dst = graph.edge_index
    ordinary = graph.nodes.kind[dst] != NodeKind.QUERY
    degree = np.bincount(dst[ordinary], minlength=len(graph.nodes.tick))
    if layout == "forest":
        assert degree.max(initial=0) <= 1
        assert np.all(src[ordinary] < dst[ordinary])
    if layout == "knn":
        assert degree.max(initial=0) <= 3
    if layout == "empty":
        assert not ordinary.any()
    if layout == "reciprocal":
        pairs = edges(graph)
        for a, b in graph.edge_index[:, ordinary].T:
            if graph.nodes.tick[a] == graph.nodes.tick[b]:
                assert (b, a) in pairs
    if layout == "incidence":
        groups = np.flatnonzero(graph.nodes.kind == NodeKind.GROUP)
        assert len(groups) > 0
        membership = np.bincount(dst[(graph.edge_kind & int(EdgeKind.GROUP_IN)) != 0], minlength=len(graph.nodes.tick))
        assert np.all((membership[groups] >= 2) & (membership[groups] <= 4))


@pytest.mark.parametrize("kernel", KERNELS)
def test_kernels(data, kernel):
    history, queries = data
    metric = LearnedMetric(np.eye(6, dtype=np.float32), -np.eye(6, dtype=np.float32))
    graph = build_temporal_graph(history, queries, GraphConfig(node_view="observation", kernel=kernel), learned=metric)
    check_graph(graph)
    assert len(graph.edge_score) > 0


def test_two_temporal_clocks_and_deduplication():
    history = History.from_arrays(np.arange(12, dtype=np.float32)[:, None], np.zeros(12, dtype=np.int64),
                                  np.arange(12), np.arange(12) // 4, names=("income",))
    config = GraphConfig(node_view="observation", kernel="identity", degree=16)
    graph = build_temporal_graph(history, config=config, cutoff=10, receivers="current")
    src, dst = graph.edge_index
    action = (graph.edge_kind & int(EdgeKind.PREVIOUS_ACTION)) != 0
    turn = (graph.edge_kind & int(EdgeKind.PREVIOUS_TURN)) != 0
    assert graph.nodes.step[src[action]].tolist() == [9]
    assert graph.nodes.step[src[turn]].tolist() == [7]
    assert graph.nodes.step[dst[action]].tolist() == [10]
    boundary = build_temporal_graph(history, config=config, cutoff=8, receivers="current")
    both = int(EdgeKind.PREVIOUS_ACTION | EdgeKind.PREVIOUS_TURN)
    assert np.count_nonzero((boundary.edge_kind & both) == both) == 1
    assert len(edges(boundary)) == len(boundary.edge_score)


def test_missing_observations_are_not_previous_action_or_turn():
    history = History.from_arrays([[1], [2]], [0, 0], [0, 10], [0, 3], names=("income",))
    graph = build_temporal_graph(history, config=GraphConfig(node_view="observation", kernel="identity"))
    assert not np.any(graph.edge_kind & int(EdgeKind.PREVIOUS_ACTION | EdgeKind.PREVIOUS_TURN))


@pytest.mark.parametrize("view", NODE_VIEWS)
def test_future_and_current_chosen_action_cannot_enter_graph(data, view):
    history, queries = data
    values = history.values.copy()
    values[history.tick > 12] = 1_000_000
    altered = History.from_arrays(values, history.stream, history.step, history.turn, names=history.names,
                                  entity=history.entity, reference=history.reference, kind=history.kind, xy=history.xy)
    config = GraphConfig(node_view=view)
    first = build_temporal_graph(history, queries, config, cutoff=6)
    second = build_temporal_graph(altered, queries, config, cutoff=6)
    assert first.fingerprint() == second.fingerprint()
    np.testing.assert_array_equal(first.nodes.values, second.nodes.values)
    np.testing.assert_array_equal(first.edge_score, second.edge_score)
    assert np.all(first.nodes.tick <= 12)
    assert not np.any((first.nodes.kind == NodeKind.ACTION) & (first.nodes.step == 6))


def query_sources(graph):
    result = {}
    for index, identity in zip(graph.query_index, graph.query_ids):
        selected = graph.edge_index[1] == index
        result[int(identity)] = (graph.edge_index[0, selected], graph.edge_score[selected])
    return result


def test_query_order_and_block_size_do_not_change_graph(data):
    history, queries = data
    order = np.arange(len(queries.ids))[::-1]
    reordered = ActionQueries.from_arrays(queries.values[order], ids=queries.ids[order], entity=queries.entity[order],
                                         reference=queries.reference[order], xy=queries.xy[order])
    first = build_temporal_graph(history, queries, limits=GraphLimits(block_size=7))
    second = build_temporal_graph(history, reordered, limits=GraphLimits(block_size=64))
    for key, value in query_sources(first).items():
        other = query_sources(second)[key]
        np.testing.assert_array_equal(value[0], other[0])
        np.testing.assert_array_equal(value[1], other[1])


def test_state_reuse_and_invalidation(data):
    history, queries = data
    state = GraphBuildState()
    first = build_temporal_graph(history, queries, state=state)
    second = build_temporal_graph(history, queries, state=state)
    assert not first.metrics["cache_hit"] and second.metrics["cache_hit"]
    assert first.fingerprint() == second.fingerprint()
    np.testing.assert_array_equal(first.edge_score, second.edge_score)
    changed = build_temporal_graph(history, queries, GraphConfig(resolution=8), state=state)
    assert not changed.metrics["cache_hit"]
    with pytest.raises(ValueError):
        first.nodes.values[0, 0] = 4


def test_exact_knn_matches_independent_scalar_reference(data):
    history, _ = data
    config = GraphConfig(node_view="observation", kernel="similarity", degree=3, time_bias=0)
    graph = build_temporal_graph(history, config=config, exhaustive=True)
    expected = set()
    x = graph.nodes.values.astype(np.float64)
    for destination in range(len(x)):
        candidates = []
        for source in range(len(x)):
            if source == destination or graph.nodes.tick[source] > graph.nodes.tick[destination]:
                continue
            denominator = np.linalg.norm(x[source]) * np.linalg.norm(x[destination])
            score = float(np.dot(x[source], x[destination]) / denominator) if denominator else 0.0
            candidates.append((-round(score, 6), source))
        expected.update((source, destination) for _, source in sorted(candidates)[:config.degree])
    assert edges(graph) == expected


def test_node_views_and_layouts_produce_distinct_structures(data):
    history, queries = data
    views = [build_temporal_graph(history, queries, GraphConfig(node_view=view)) for view in NODE_VIEWS]
    assert len({g.fingerprint() for g in views}) == len(NODE_VIEWS)
    layouts = [build_temporal_graph(history, queries, GraphConfig(node_view="observation", layout=layout, degree=3, arity=4)) for layout in LAYOUTS]
    assert len({g.fingerprint() for g in layouts}) == len(LAYOUTS)


def test_history_zero_has_no_past_or_change_profile(data):
    history, queries = data
    graph = build_temporal_graph(history, queries, GraphConfig(history=0))
    assert np.all(graph.nodes.step == history.step[-1])
    assert not graph.nodes.delta.any()
    assert not graph.nodes.profile.any()


def test_raw_game_rank_is_a_supported_numeric_feature():
    history = History.from_arrays([[4]], [0], [0], [0], names=("rank",))
    assert history.values.tolist() == [[4]]


def test_limits_fail_explicitly(data):
    history, queries = data
    with pytest.raises(ValueError, match="max_nodes"):
        build_temporal_graph(history, queries, limits=GraphLimits(max_nodes=2))
    with pytest.raises(ValueError, match="max_edges"):
        build_temporal_graph(history, queries, limits=GraphLimits(max_edges=2))
    with pytest.raises(ValueError, match="dense_pair_limit"):
        build_temporal_graph(history, config=GraphConfig(layout="dense"), limits=GraphLimits(dense_pair_limit=2))
    with pytest.raises(ValueError, match="requires explicit"):
        build_temporal_graph(history, config=GraphConfig(kernel="learned"))


def test_no_mapgraph_or_model_dependency():
    root = Path(__file__).resolve().parents[1] / "advisor" / "temporal_graph"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "mapgraph" not in (node.module or "")
                assert not (node.module or "").startswith(("torch", "catboost", "decisions"))
            if isinstance(node, ast.Import):
                assert not any("mapgraph" in item.name or item.name.startswith(("torch", "catboost", "decisions")) for item in node.names)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_blocking_preserves_complete_graph(data, layout):
    history, queries = data
    config = GraphConfig(layout=layout, arity=4, degree=3)
    first = build_temporal_graph(history, queries, config, limits=GraphLimits(block_size=5))
    second = build_temporal_graph(history, queries, config, limits=GraphLimits(block_size=256))
    assert first.fingerprint() == second.fingerprint()
    np.testing.assert_array_equal(first.edge_score, second.edge_score)


def test_current_block_matches_full_graph_current_edges(data):
    history, queries = data
    config = GraphConfig(node_view="observation")
    full = build_temporal_graph(history, queries, config)
    current = build_temporal_graph(history, queries, config, receivers="current")
    selected = full.nodes.step[full.edge_index[1]] == history.step[-1]
    np.testing.assert_array_equal(full.edge_index[:, selected], current.edge_index)
    np.testing.assert_array_equal(full.edge_score[selected], current.edge_score)
    assert current.metrics["candidate_slots"] == len(current.receiver_index) * 64


def test_query_chunking_and_empty_queries(data):
    history, queries = data
    full = query_sources(build_temporal_graph(history, queries))
    for order in (np.arange(0, len(queries.ids), 2), np.arange(1, len(queries.ids), 2)):
        chunk = ActionQueries.from_arrays(queries.values[order], ids=queries.ids[order], entity=queries.entity[order],
                                         reference=queries.reference[order], xy=queries.xy[order])
        for key, values in query_sources(build_temporal_graph(history, chunk)).items():
            np.testing.assert_array_equal(values[0], full[key][0])
            np.testing.assert_array_equal(values[1], full[key][1])
    none = ActionQueries.from_arrays(np.empty((0, 6)), ids=np.empty(0, dtype=np.int64))
    assert build_temporal_graph(history, none).query_index.size == 0


@pytest.mark.parametrize("view", NODE_VIEWS)
def test_current_state_anchors_and_selected_actions_survive_packing(data, view):
    history, _ = data
    graph = build_temporal_graph(history, config=GraphConfig(node_view=view, resolution=3))
    source = np.flatnonzero((history.step == history.step[-1]) & (history.kind == NodeKind.OBSERVATION))
    current = np.flatnonzero((graph.nodes.step == history.step[-1]) & (graph.nodes.kind == NodeKind.OBSERVATION))
    np.testing.assert_array_equal(graph.nodes.stream[current], history.stream[source])
    np.testing.assert_array_equal(graph.nodes.values[current], history.values[source])
    assert np.count_nonzero(graph.nodes.kind == NodeKind.ACTION) == 11
    packed = np.isin(graph.nodes.kind, (NodeKind.TRACE, NodeKind.BUNDLE))
    assert np.all(graph.nodes.mass[packed] <= 3)


def test_missing_value_does_not_become_a_change_from_zero():
    history = History.from_arrays([[np.nan], [10], [10]], [0, 0, 0], [0, 1, 2], [0, 0, 0],
                                  names=("income",), present=[[False], [True], [True]])
    assert history.delta[1, 0] == 0
    graph = build_temporal_graph(history, config=GraphConfig(node_view="change"))
    assert graph.nodes.step.tolist() == [0, 1, 2]
    assert not graph.nodes.present[0, 0]
    assert graph.nodes.values[-1, 0] == 10


def test_dense_ignores_degree_and_empty_does_not_score_history(data):
    history, _ = data
    dense = build_temporal_graph(history, config=GraphConfig(node_view="observation", layout="dense", degree=0))
    ticks = dense.nodes.tick
    expected = sum(int(np.count_nonzero(ticks <= tick)) - 1 for tick in ticks)
    assert len(dense.edge_score) == expected
    empty = build_temporal_graph(history, config=GraphConfig(layout="empty"))
    assert empty.metrics["candidate_slots"] == 0
    assert empty.edge_index.shape == (2, 0)


def test_episode_and_metric_changes_cannot_reuse_the_wrong_state(data):
    history, queries = data
    state = GraphBuildState()
    identity = np.eye(6, dtype=np.float32)
    first = build_temporal_graph(history, queries, GraphConfig(kernel="learned"), learned=LearnedMetric(identity, identity), state=state)
    second = build_temporal_graph(history, queries, GraphConfig(kernel="learned"), learned=LearnedMetric(identity, -identity), state=state)
    assert second.metrics["cache_hit"]
    assert first.fingerprint() != second.fingerprint()
    other = replace(history, episode="another-campaign")
    graph = build_temporal_graph(other, queries, state=state)
    assert not graph.metrics["cache_hit"]
    assert graph.metrics["episode"] == "another-campaign"
    with pytest.raises(ValueError):
        graph.query_ids[0] = -100
