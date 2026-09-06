from __future__ import annotations


import os
import sys
import time

from advisor.mapgraph import schema as S
from advisor.mapgraph import build as B
from advisor.mapgraph import graph_config as GC

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

THREADS = min(4, max(1, os.cpu_count() or 1))

CFG = {"hidden": 92, "entity_layers": 1, "action_rounds": 5,
       "map_aggr": "add+mean", "act_aggr": "mean", "attn": "all",
       "conv": "sage", "conv_map": None, "conv_a2e": None, "conv_e2a": "rel",
       "dst_dim": 4, "update": "linear", "self_transform": True,
       "dropout": 0.14751160117262713,
       "lr": 0.0002048320544808509, "weight_decay": 7.44846604643036e-05,
       "batch": 512, "patience": 10,
       "grad_clip": 0.2681914954781695, "bf16": True,
       "seed": 0, "time_budget_s": 600, "device": "cuda"}

MIN_FIT_S = 30


def walk(runs_root=None, limit=None, log=print, window=None, graph_config=None, workers=2):
    source = load_walk_source(runs_root, limit=limit, log=log, window=window)
    return walk_source(source, graph_config=graph_config, limit=limit, log=log,
                       workers=workers)


def _percentile(values, pct):
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * pct + 0.5))]


def _data_bytes(data):
    from advisor.mapgraph.source import GraphView
    if isinstance(data, dict):
        return sum(value.nbytes for value in data.values())
    if isinstance(data, GraphView):
        return data.nbytes
    import torch
    return sum(v.numel() * v.element_size() for v in data.to_dict().values()
               if torch.is_tensor(v))


def graph_metrics(examples, built=0, reused=0, source_seconds=0.0,
                  graph_seconds=0.0, total_seconds=0.0, population=None):
    nodes = [int(e["counts"]["nodes"]) for e in examples]
    edges = [int(e["counts"]["edges"]) for e in examples]
    actions = [int(e["counts"]["actions"]) for e in examples]
    tensor_bytes = sum(_data_bytes(e["data"]) for e in examples)
    n = len(examples)
    processed = int(built) + int(reused)
    population = int(population if population is not None else n)
    valid_rate = n / max(1, processed)
    projected = int(round(population * valid_rate))
    return {
        "graphs": n, "processed": processed, "built": int(built), "reused": int(reused),
        "source_seconds": round(float(source_seconds), 3),
        "graph_seconds": round(float(graph_seconds), 3),
        "total_seconds": round(float(total_seconds), 3),
        "graphs_per_second": (round(processed / graph_seconds, 2)
                              if graph_seconds else 0.0),
        "nodes_total": sum(nodes), "nodes_mean": round(sum(nodes) / n, 2) if n else 0.0,
        "nodes_p95": _percentile(nodes, 0.95), "nodes_max": max(nodes, default=0),
        "edges_total": sum(edges), "edges_mean": round(sum(edges) / n, 2) if n else 0.0,
        "edges_p95": _percentile(edges, 0.95), "edges_max": max(edges, default=0),
        "actions_total": sum(actions),
        "tensor_bytes": tensor_bytes,
        "tensor_gib": round(tensor_bytes / (1024 ** 3), 4),
        "population_decisions": population,
        "projected_graphs": projected,
        "projected_nodes_total": int(round(sum(nodes) / max(1, n) * projected)),
        "projected_edges_total": int(round(sum(edges) / max(1, n) * projected)),
        "projected_tensor_gib": round(
            tensor_bytes / max(1, n) * projected / (1024 ** 3), 4),
    }


def format_graph_metrics(metrics):
    return ("%d graphs %.1f/s, nodes mean/p95/max %.1f/%d/%d, "
            "edges mean/p95/max %.1f/%d/%d, tensors %.3f GiB, "
            "source/graph %.1fs/%.1fs"
            % (metrics["graphs"], metrics["graphs_per_second"],
               metrics["nodes_mean"], metrics["nodes_p95"], metrics["nodes_max"],
               metrics["edges_mean"], metrics["edges_p95"], metrics["edges_max"],
               metrics["tensor_gib"], metrics["source_seconds"],
               metrics["graph_seconds"]))


def load_walk_source(runs_root=None, limit=None, log=print, window=None):
    from base_model import RUNS_ROOT, TRAIN_WINDOW_CAMPAIGNS
    from store import DecisionStore, IncompatibleStore
    from advisor.mapgraph.source import DecisionSource
    started = time.perf_counter()
    log("mapgraph.train: source selection enter")
    runs_root = runs_root or RUNS_ROOT
    window = TRAIN_WINDOW_CAMPAIGNS if window is None else window
    dbs = common.run_dbs(runs_root)
    jobs, skipped, population = [], [], 0
    for db in dbs:
        run_dir = os.path.dirname(db)
        try:
            st = DecisionStore(run_dir, readonly=True)
        except IncompatibleStore as e:
            skipped.append(run_dir)
            log("mapgraph.train: skipping %s -> %s" % (run_dir, str(e)[:100]))
            continue
        try:
            floor = st.window_floor(window)
            population += st.labelled_count(min_decision=floor)
            hydrate_limit = max(limit * 2, limit + 50) if limit else None
            heads = st.labelled_heads(
                after=(floor - 1) if floor is not None else None,
                limit=hydrate_limit, spread=bool(limit))
            jobs.append((run_dir, heads))
        finally:
            st.close()
    seconds = time.perf_counter() - started
    records = DecisionSource(jobs, log)
    log("mapgraph.train: source selection exit %.1fs -- %d decisions"
        % (seconds, len(records)))
    return {"records": records, "runs": len(dbs) - len(skipped),
            "source_seconds": seconds, "window": window,
            "population_decisions": population}


def walk_source(source, graph_config=None, limit=None, log=print, workers=1, arrays=False):
    if arrays:
        from advisor.mapgraph.arrays import to_arrays as convert
    else:
        from advisor.mapgraph.net import to_data as convert
    graph_config = GC.from_dict(graph_config)
    workers = max(1, min(workers, (len(source["records"]) + 1023) // 1024))
    started = time.time()
    log("mapgraph.train: graph walk enter graph=%s" % graph_config.fingerprint())
    examples = []
    tally = {"no_graph": 0, "no_label": 0, "taken_missing": 0, "no_actions": 0}
    input_seconds = 0.0
    query_seconds, query_rows, build_seconds, tensor_seconds = 0.0, 0, 0.0, 0.0
    if workers > 1:
        for walked in source["records"].graphs(graph_config, workers):
            examples.extend(walked["examples"])
            input_seconds += walked["metrics"]["input_seconds"]
            query_seconds += walked["metrics"]["query_seconds"]
            query_rows += walked["metrics"]["query_rows"]
            build_seconds += walked["metrics"]["build_seconds"]
            tensor_seconds += walked["metrics"]["tensor_seconds"]
            for key, value in walked["tally"].items():
                tally[key] += value
            log("mapgraph.train: %d graphs built in %.1fs"
                % (len(examples), time.time() - started))
            if limit and len(examples) >= limit:
                examples = examples[:limit]
                break
        records = ()
    else:
        records = source["records"]
    for rec, taken, counted, y, gain in records:
        if y is None:
            tally["no_label"] += 1
            continue
        stage = time.perf_counter()
        g = B.build_graph(rec, graph_config)
        build_seconds += time.perf_counter() - stage
        if g is None:
            tally["no_graph"] += 1
            continue
        if not g.action_nodes:
            tally["no_actions"] += 1
            continue
        want = tuple(str(v) for v in taken)
        mask = [1.0 if k == want else 0.0 for k in g.action_keys]
        if sum(mask) != 1.0:
            tally["taken_missing"] += 1
            continue
        stage = time.perf_counter()
        data = convert(g, y=y, taken=mask)
        tensor_seconds += time.perf_counter() - stage
        examples.append({"data": data, "y": float(y), "gain": float(gain),
                         "campaign_id": rec.get("campaign_id"), "counts": g.counts})
        if limit and len(examples) >= limit:
            break
    seconds = time.time() - started
    if workers == 1:
        input_seconds = source["records"].input_seconds
        query_seconds = source["records"].query_seconds
        query_rows = source["records"].query_rows
    metrics = graph_metrics(examples, built=len(examples), reused=0,
                            source_seconds=source.get("source_seconds", 0.0),
                            graph_seconds=seconds,
                            total_seconds=source.get("source_seconds", 0.0) + seconds,
                            population=source.get("population_decisions"))
    metrics["input_seconds"] = round(input_seconds, 3)
    metrics["workers"] = workers
    metrics.update(query_seconds=round(query_seconds, 3), query_rows=query_rows,
                   build_seconds=round(build_seconds, 3), tensor_seconds=round(tensor_seconds, 3))
    log("mapgraph.train: accumulated worker input/query/build/tensor %.1fs/%.1fs/%.1fs/%.1fs, %d SQL rows"
        % (input_seconds, query_seconds, build_seconds, tensor_seconds, query_rows))
    log("mapgraph.train: graph walk exit %.1fs -- %s"
        % (seconds, format_graph_metrics(metrics)))
    return {"examples": examples, "tally": tally, "runs": source["runs"],
            "n_decisions": len(source["records"]),
            "campaigns": sorted({e["campaign_id"] for e in examples}),
            "graph_config": graph_config.as_dict(), "metrics": metrics}


def _tensorize(examples):
    return [ex["data"] for ex in examples]


def _device(cfg, log):
    import torch
    want = str(cfg.get("device") or "")
    if want == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("mapgraph.train: device=cuda requested, CUDA unavailable")
        log("mapgraph.train: device cuda (%s)" % torch.cuda.get_device_name(0))
        return torch.device("cuda")
    if want == "cpu":
        log("mapgraph.train: device cpu")
        return torch.device("cpu")
    raise RuntimeError("mapgraph.train: device must be cuda or cpu, got %r" % want)


def _corpus_bytes(batches):
    import torch
    return sum(v.numel() * v.element_size() for b in batches
               for v in b.to_dict().values() if torch.is_tensor(v))


def _batch_field(items, key, offsets):
    import numpy as np
    import torch
    from advisor.mapgraph.source import data_array
    values = [data_array(d, key) for d in items]
    dim = 1 if key in ("edge_index", "a2e_index", "e2a_index") else 0
    sizes = np.asarray([v.shape[dim] for v in values], dtype=np.int64)
    value = np.concatenate(values, axis=dim)
    indexed = key in ("edge_index", "a2e_index", "e2a_index", "action_index")
    if indexed:
        value += np.repeat(offsets[:-1], sizes)
    return (torch.from_numpy(value),
            torch.from_numpy(np.concatenate(([0], np.cumsum(sizes)))),
            torch.from_numpy(offsets[:-1].copy() if indexed else np.zeros(len(items), dtype=np.int64)))


def _batch(items):
    import numpy as np
    import torch
    from torch_geometric.data import Batch
    from advisor.mapgraph.net import DecisionGraph
    nodes = np.asarray([d.num_nodes for d in items], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(nodes)))
    tensors, slices, increments = {}, {}, {}
    for key in items[0].keys():
        tensors[key], slices[key], increments[key] = _batch_field(items, key, offsets)
    tensors["batch"] = torch.from_numpy(np.repeat(np.arange(len(items)), nodes))
    tensors["ptr"] = torch.from_numpy(offsets)
    batch = Batch(_base_cls=DecisionGraph, **tensors)
    batch._num_graphs = len(items)
    batch._slice_dict = slices
    batch._inc_dict = increments
    return batch


def _collate_partitions(partitions, size, dev, log):
    import numpy as np
    import torch
    from torch_geometric.data import Batch
    from advisor.mapgraph.net import DecisionGraph
    from advisor.mapgraph.source import GraphView
    started = time.perf_counter()
    jobs, loaders, blocks = [], [], {}
    keys = next(items[0].keys() for items in partitions if items)
    for items in partitions:
        loader = []
        for k in range(0, len(items), size):
            group = items[k:k + size]
            nodes = np.asarray([d.num_nodes for d in group], dtype=np.int64)
            offsets = np.concatenate(([0], np.cumsum(nodes)))
            batch = Batch(_base_cls=DecisionGraph,
                          batch=torch.from_numpy(np.repeat(np.arange(len(group)), nodes)).to(dev),
                          ptr=torch.from_numpy(offsets).to(dev))
            batch._num_graphs = len(group)
            batch._slice_dict, batch._inc_dict = {}, {}
            loader.append(batch)
            jobs.append((group, offsets, batch))
            for data in group:
                if isinstance(data, GraphView):
                    blocks[id(data.block)] = data.block
        loaders.append(loader)
    for key in keys:
        field_started = time.perf_counter()
        for group, offsets, batch in jobs:
            value, slices, increments = _batch_field(group, key, offsets)
            batch[key] = value.to(dev)
            batch._slice_dict[key], batch._inc_dict[key] = slices, increments
            del value
        for block in blocks.values():
            block.pop(key, None)
        for items in partitions:
            for data in items:
                if isinstance(data, GraphView):
                    if key == "y_z":
                        data.y_z = None
                else:
                    del data[key]
        log("mapgraph.train: collate field %s transferred and CPU storage released %.2fs"
            % (key, time.perf_counter() - field_started))
    for items in partitions:
        items.clear()
    log("mapgraph.train: partition collation exit %.1fs, %d batches"
        % (time.perf_counter() - started, len(jobs)))
    return loaders


def _collate(items, size, dev, log, tag, consume=False):
    import torch
    started = time.perf_counter()
    batches = []
    build_s, transfer_s, release_s = 0.0, 0.0, 0.0
    for k in range(0, len(items), size):
        step = time.perf_counter()
        batch = _batch(items[k:k + size])
        build_s += time.perf_counter() - step
        step = time.perf_counter()
        batches.append(batch.to(dev, non_blocking=True))
        transfer_s += time.perf_counter() - step
        step = time.perf_counter()
        if consume:
            items[k:k + size] = [None] * len(items[k:k + size])
        release_s += time.perf_counter() - step
    if dev.type == "cuda" and batches:
        torch.cuda.synchronize()
        log("mapgraph.train: %s %d batches resident on %s (%.2fGB), %.1fs"
            % (tag, len(batches), dev.type, _corpus_bytes(batches) / 1e9,
               time.perf_counter() - started))
    log("mapgraph.train: %s collation build/transfer/release %.1fs/%.1fs/%.1fs"
        % (tag, build_s, transfer_s, release_s))
    return batches
