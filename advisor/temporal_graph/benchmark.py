import argparse
from dataclasses import asdict, replace
import json
import logging
import os
from pathlib import Path
import platform
import time
import tracemalloc

import numpy as np

from .config import KERNELS, LAYOUTS, NODE_VIEWS, GraphConfig, GraphLimits, LearnedMetric
from .data import NodeKind
from .examples import campaign
from .graph import EdgeKind, GraphBuildState, build_temporal_graph


def structural_metrics(graph):
    nodes = graph.nodes
    src, dst = graph.edge_index
    n = len(nodes.tick)
    assert np.all(nodes.tick[src] <= nodes.tick[dst])
    assert np.all(nodes.kind[src] != NodeKind.QUERY)
    assert np.all(src != dst)
    assert len(np.unique(src * n + dst)) == len(src)
    assert np.isfinite(graph.edge_score).all()
    ordinary = (nodes.kind[src] != NodeKind.QUERY) & (nodes.kind[dst] != NodeKind.QUERY)
    a, b = src[ordinary], dst[ordinary]
    degree = np.bincount(b, minlength=n)
    active = np.flatnonzero(nodes.kind != NodeKind.QUERY)
    labels = np.arange(n)
    for _ in range(n):
        updated = labels.copy()
        np.minimum.at(updated, a, labels[b])
        np.minimum.at(updated, b, labels[a])
        updated = updated[updated]
        if np.array_equal(labels, updated):
            break
        labels = updated
    same_time = nodes.tick[a] == nodes.tick[b]
    pair = a * n + b
    reciprocal = np.isin(pair, b * n + a)
    groups = np.flatnonzero(nodes.kind == NodeKind.GROUP)
    group_memberships = (graph.edge_kind & int(EdgeKind.GROUP_IN)) != 0
    group_degree = np.bincount(dst[group_memberships], minlength=n)[groups]
    return dict(fingerprint=graph.fingerprint(), nodes=n, edges=len(src), observation_edges=int(ordinary.sum()),
                node_kinds={kind.name.lower(): int(np.count_nonzero(nodes.kind == kind)) for kind in NodeKind},
                components=int(len(np.unique(labels[active]))), incoming_max=int(degree[active].max(initial=0)),
                incoming_mean=float(degree[active].mean()) if len(active) else 0.0,
                same_time_reciprocity=float(reciprocal[same_time].mean()) if same_time.any() else 0.0,
                temporal_edges=int(np.count_nonzero(graph.decision_gap > 0)),
                previous_action_edges=int(np.count_nonzero(graph.edge_kind & int(EdgeKind.PREVIOUS_ACTION))),
                previous_turn_edges=int(np.count_nonzero(graph.edge_kind & int(EdgeKind.PREVIOUS_TURN))),
                max_group_arity=int(group_degree.max(initial=0)),
                lag_quantiles=np.quantile(graph.decision_gap, [0.5, 0.95, 1]).tolist() if len(src) else [0, 0, 0],
                build_ms=graph.metrics["seconds"] * 1000)


def edge_distance(first, second):
    left = set(map(tuple, first.edge_index.T.tolist()))
    right = set(map(tuple, second.edge_index.T.tolist()))
    return 1 - len(left & right) / max(1, len(left | right))


def diversity():
    history, queries = campaign(20, 18)
    base = GraphConfig(node_view="observation", resolution=4, history=64, degree=4, arity=4)
    state = GraphBuildState()
    rows, layouts = [], {}
    for view in NODE_VIEWS:
        for layout in LAYOUTS:
            config = replace(base, node_view=view, layout=layout)
            graph = build_temporal_graph(history, queries, config, state=state)
            rows.append(dict(config=asdict(config), **structural_metrics(graph)))
            if view == "observation":
                layouts[layout] = graph
    metric = LearnedMetric(np.eye(6, dtype=np.float32), -np.eye(6, dtype=np.float32))
    for kernel in KERNELS:
        config = replace(base, kernel=kernel)
        graph = build_temporal_graph(history, queries, config, learned=metric if kernel == "learned" else None, state=state)
        rows.append(dict(config=asdict(config), **structural_metrics(graph)))
    pairs = (("node_view", base, replace(base, node_view="change")),
             ("resolution", replace(base, node_view="trace", resolution=2), replace(base, node_view="trace", resolution=8)),
             ("history", replace(base, history=4), replace(base, history=64)),
             ("kernel", replace(base, kernel="similarity"), replace(base, kernel="contrast")),
             ("layout", base, replace(base, layout="forest")),
             ("degree", replace(base, degree=1), replace(base, degree=8)),
             ("arity", replace(base, layout="incidence", arity=2), replace(base, layout="incidence", arity=8)),
             ("time_bias", replace(base, time_bias=-1), replace(base, time_bias=1)))
    sensitivity = []
    for knob, first, second in pairs:
        left = build_temporal_graph(history, queries, first, state=state)
        right = build_temporal_graph(history, queries, second, state=state)
        changed = left.fingerprint() != right.fingerprint()
        assert changed, "%s did not change graph structure" % knob
        sensitivity.append(dict(knob=knob, changed=changed, first_nodes=len(left.nodes.tick), second_nodes=len(right.nodes.tick),
                                first_edges=left.metrics["edges"], second_edges=right.metrics["edges"],
                                edge_jaccard_distance=edge_distance(left, right) if np.array_equal(left.nodes.members, right.nodes.members)
                                and np.array_equal(left.nodes.member_offsets, right.nodes.member_offsets) else None))
    unique = len({r["fingerprint"] for r in rows})
    assert len({g.fingerprint() for g in layouts.values()}) == len(LAYOUTS)
    assert unique >= 36
    return dict(recipes=len(rows), unique_topologies=unique, same_input_rows=len(history.tick),
                layouts_distinct=len({g.fingerprint() for g in layouts.values()}), rows=rows, sensitivity=sensitivity)


def timings(history, queries, config, mode, repeats):
    state = GraphBuildState()
    started = time.perf_counter()
    cold = build_temporal_graph(history, queries, config, receivers=mode, state=state)
    cold_ms = (time.perf_counter() - started) * 1000
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        graph = build_temporal_graph(history, queries, config, receivers=mode, state=state)
        samples.append((time.perf_counter() - started) * 1000)
        assert graph.fingerprint() == cold.fingerprint()
    tracemalloc.start()
    measured = build_temporal_graph(history, queries, config, receivers=mode)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert measured.fingerprint() == cold.fingerprint()
    graph_bytes = cold.metrics["array_bytes"]
    return dict(mode=mode, input_rows=len(history.tick), nodes=cold.metrics["nodes"], receivers=cold.metrics["receivers"],
                edges=cold.metrics["edges"], cold_ms=cold_ms,
                warm_median_ms=float(np.median(samples)), warm_p95_ms=float(np.quantile(samples, .95)),
                candidate_slots=cold.metrics["candidate_slots"], valid_candidates=cold.metrics["valid_candidates"],
                graph_mib=graph_bytes / 2**20, cold_peak_traced_mib=peak / 2**20,
                phase_ms={k: v * 1000 for k, v in cold.metrics.items() if k.endswith("_seconds")})


def scaling(repeats):
    rows = []
    for steps, streams in ((8, 64), (32, 64), (128, 64), (512, 64), (1024, 128)):
        started = time.perf_counter()
        history, queries = campaign(steps, streams)
        preparation_ms = (time.perf_counter() - started) * 1000
        config = GraphConfig(node_view="observation", history=steps, kernel="mixed", degree=8)
        for mode in ("current", "all"):
            rows.append(dict(prepare_fixture_and_history_ms=preparation_ms, **timings(history, queries, config, mode, repeats)))
    return rows


def scalar_reference(nodes, receivers, proposals, unit):
    pairs = []
    for row, destination in enumerate(receivers):
        scores = []
        for source in proposals[row]:
            if source >= 0:
                score = float(np.einsum("d,d->", unit[source], unit[destination]))
                scores.append((-score, int(source)))
        pairs.extend((source, int(destination)) for _, source in sorted(scores)[:8])
    return np.array(sorted(pairs, key=lambda p: (p[1], p[0])), dtype=np.int64).T


def reference_speed(repeats):
    history, _ = campaign(32, 32)
    config = GraphConfig(node_view="observation", kernel="similarity", time_bias=0, degree=8)
    state = GraphBuildState()
    graph = build_temporal_graph(history, config=config, state=state)
    index = state.index
    receivers = np.arange(len(state.nodes.tick))
    proposals = index.propose(receivers, 64)
    scalar_samples, vector_samples = [], []
    for _ in range(repeats):
        started = time.perf_counter()
        reference = scalar_reference(state.nodes, receivers, proposals, index.unit)
        scalar_samples.append((time.perf_counter() - started) * 1000)
        np.testing.assert_array_equal(reference, graph.edge_index)
        started = time.perf_counter()
        vector = build_temporal_graph(history, config=config, state=state)
        vector_samples.append((time.perf_counter() - started) * 1000)
        np.testing.assert_array_equal(vector.edge_index, reference)
    return dict(rows=len(history.tick), candidate_slots=proposals.size, reference_edges=reference.shape[1],
                equality="exact endpoint equality", scalar_scoring_selection_median_ms=float(np.median(scalar_samples)),
                vector_complete_warm_build_median_ms=float(np.median(vector_samples)),
                speedup=float(np.median(scalar_samples) / np.median(vector_samples)))


def retrieval_quality():
    history, _ = campaign(20, 18)
    rows = []
    for kernel in ("similarity", "contrast", "mixed", "geometric"):
        config = GraphConfig(node_view="observation", kernel=kernel, degree=8)
        exact = build_temporal_graph(history, config=config, exhaustive=True)
        exact_edges = set(map(tuple, exact.edge_index.T.tolist()))
        for candidates in (32, 64, 128):
            sparse = build_temporal_graph(history, config=config, limits=GraphLimits(candidates=candidates))
            sparse_edges = set(map(tuple, sparse.edge_index.T.tolist()))
            rows.append(dict(kernel=kernel, candidates=candidates, exact_topk_edge_recall=len(exact_edges & sparse_edges) / max(1, len(exact_edges)),
                             candidate_slots=sparse.metrics["candidate_slots"], exact_slots=exact.metrics["candidate_slots"]))
    return rows


def run(repeats=5):
    if repeats < 1:
        raise ValueError("repeats must be positive")
    started = time.perf_counter()
    result = dict(platform=platform.platform(), processor=platform.processor(), logical_cpus=os.cpu_count(),
                  python=platform.python_version(), numpy=np.__version__, repeats=repeats,
                  data="deterministic synthetic state/selected-action histories; no live database or GPU",
                  diversity=diversity())
    print("structural checks complete", flush=True)
    result["scaling"] = scaling(repeats)
    print("scaling measurements complete", flush=True)
    result["reference"] = reference_speed(repeats)
    result["retrieval_quality"] = retrieval_quality()
    result["seconds"] = time.perf_counter() - started
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--log-timings", action="store_true")
    args = parser.parse_args()
    if args.log_timings:
        logging.basicConfig(level=logging.INFO)
    result = run(args.repeats)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(dict(output=str(args.output), seconds=result["seconds"],
                              recipes=result["diversity"]["recipes"], unique_topologies=result["diversity"]["unique_topologies"],
                              reference_speedup=result["reference"]["speedup"]), indent=2))
    else:
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
