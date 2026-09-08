from __future__ import annotations

import time


def _pack(datas):
    import numpy as np
    block = {}
    for key in datas[0].keys():
        values = [data_array(data, key) for data in datas]
        dim = 1 if key in ("edge_index", "a2e_index", "e2a_index") else 0
        offsets = np.concatenate(([0], np.cumsum([value.shape[dim] for value in values])))
        block[key] = np.concatenate(values, axis=dim), offsets, dim
    return block


class GraphView:

    __slots__ = ("block", "index", "y_z")

    def __init__(self, block, index):
        self.block, self.index, self.y_z = block, index, None

    def array(self, key):
        if key == "y_z":
            return self.y_z.numpy()
        value, offsets, dim = self.block[key]
        start, end = offsets[self.index:self.index + 2]
        return value[:, start:end] if dim else value[start:end]

    def __getitem__(self, key):
        import torch
        value = self.array(key)
        return value if torch.is_tensor(value) else torch.from_numpy(value)

    def keys(self):
        return (*self.block, "y_z") if self.y_z is not None else tuple(self.block)

    @property
    def num_nodes(self):
        offsets = self.block["x"][1]
        return int(offsets[self.index + 1] - offsets[self.index])

    @property
    def nbytes(self):
        return sum(self.array(key).nbytes for key in self.keys())

    def to_dict(self):
        return {key: self[key] for key in self.keys()}


def data_array(data, key):
    if isinstance(data, dict):
        return data[key]
    return data.array(key) if isinstance(data, GraphView) else data[key].numpy()


def _graphs(job):
    from advisor.mapgraph import train
    run_dir, heads, graph_config, deadline = job
    source = {"records": DecisionSource([(run_dir, heads)], lambda s: None),
              "runs": 1, "population_decisions": len(heads)}
    walked = train.walk_source(source, graph_config, log=lambda s: None, arrays=True, deadline=deadline)
    walked["block"] = _pack([example["data"] for example in walked["examples"]]) if walked["examples"] else {}
    for example in walked["examples"]:
        example["data"] = None
    return walked


class DecisionSource:

    def __init__(self, jobs, log):
        self.jobs = jobs
        self.log = log
        self.input_seconds = 0.0
        self.query_seconds = 0.0
        self.query_rows = 0

    def __len__(self):
        return sum(len(heads) for _, heads in self.jobs)

    def select(self, indices):
        wanted = set(indices)
        jobs, offset = [], 0
        for run_dir, heads in self.jobs:
            jobs.append((run_dir, [h for i, h in enumerate(heads, offset) if i in wanted]))
            offset += len(heads)
        return DecisionSource(jobs, self.log)

    def graphs(self, graph_config, workers, deadline=None):
        import concurrent.futures
        from collections import deque
        from advisor.mapgraph.trial_budget import remaining
        jobs = iter((run_dir, heads[i:i + 1024], graph_config.as_dict(), deadline)
                    for run_dir, heads in self.jobs for i in range(0, len(heads), 1024))
        started = time.perf_counter()
        self.log("mapgraph.source: graph workers enter workers=%d" % workers)
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            pending = deque()
            for job in (next(jobs, None) for _ in range(workers)):
                if job is None:
                    break
                pending.append(pool.submit(_graphs, job))
            while pending:
                walked = pending.popleft().result(timeout=remaining(deadline) if deadline is not None else None)
                block = walked.pop("block")
                for index, example in enumerate(walked["examples"]):
                    example["data"] = GraphView(block, index)
                yield walked
                job = next(jobs, None)
                if job is not None:
                    pending.append(pool.submit(_graphs, job))
        self.log("mapgraph.source: graph workers exit %.1fs"
                 % (time.perf_counter() - started))

    def __iter__(self):
        from base_model import TARGET_WEIGHTS, decision_deltas, target
        from store import DecisionStore
        from advisor import memory
        from advisor.mapgraph.projection import Projection
        started = time.perf_counter()
        count, read_s, setup_s = 0, 0.0, 0.0
        self.query_seconds, self.query_rows = 0.0, 0
        try:
            for run_dir, heads in self.jobs:
                if not heads:
                    continue
                st = DecisionStore(run_dir, readonly=True)
                projection = None
                try:
                    setup_started = time.perf_counter()
                    series = st.target_series({h[10] for h in heads})
                    stamps = memory.replay_stamps(st, [h[0] for h in heads])
                    projection = Projection(st.con)
                    setup_s += time.perf_counter() - setup_started
                    for offset in range(0, len(heads), 1024):
                        chunk = heads[offset:offset + 1024]
                        records, seconds = projection.read(chunk)
                        read_s += seconds
                        for h in chunk:
                            rec = records.pop(h[0])
                            rec["campaign"].update(stamps.pop(h[0], {}))
                            deltas = decision_deltas(rec["campaign"], series.get(h[10]) or {}, rec["turn"])
                            y = target(deltas)
                            gain = None if y is None else sum(
                                TARGET_WEIGHTS.get(k, 1.0) * v for k, v in deltas.items()
                                if k != "survival" and v is not None)
                            count += 1
                            yield rec, st._identity(*h[1:7]), y, gain
                        if offset % 4096 == 0:
                            self.log("mapgraph.source: %d decisions, SQL projection %.1fs, elapsed %.1fs"
                                     % (count, read_s, time.perf_counter() - started))
                finally:
                    if projection is not None:
                        self.query_seconds += projection.query_seconds
                        self.query_rows += projection.query_rows
                    st.close()
        finally:
            self.input_seconds = setup_s + read_s
            self.log("mapgraph.source: exit %d decisions, SQL projection %.1fs, elapsed %.1fs"
                     % (count, read_s, time.perf_counter() - started))
