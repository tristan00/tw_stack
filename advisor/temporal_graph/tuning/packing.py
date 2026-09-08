import logging
from pathlib import Path
import time

import numpy as np


LOG = logging.getLogger(__name__)


def graph_arrays(graph, cutoff, time_features):
    if time_features not in ("action", "turn", "both"):
        raise ValueError("unknown time feature choice")
    nodes = graph.nodes
    if len(graph.query_index) != 1:
        raise ValueError("training graphs require exactly one chosen action query")
    decision = np.log1p(np.maximum(cutoff - nodes.step, 0)).astype(np.float32)
    turn = np.log1p(np.maximum(nodes.turn[graph.query_index[0]] - nodes.turn, 0)).astype(np.float32)
    gaps = np.log1p(np.maximum(np.column_stack((graph.decision_gap, graph.turn_gap)), 0)).astype(np.float32)
    if time_features == "turn":
        decision[:] = 0
        gaps[:, 0] = 0
    if time_features == "action":
        turn[:] = 0
        gaps[:, 1] = 0
    x = np.column_stack((nodes.values, nodes.present, nodes.delta, nodes.profile,
                         decision, turn, np.log1p(nodes.mass))).astype(np.float32)
    bits = ((graph.edge_kind[:, None] >> np.arange(13)) & 1).astype(np.float32)
    score = np.sign(graph.edge_score) * np.log1p(np.abs(graph.edge_score))
    edge = np.column_stack((bits, score, gaps)).astype(np.float32)
    return dict(x=x, kind=nodes.kind, edge_index=graph.edge_index,
                edge=edge, query=graph.query_index)


class GraphCache:

    def __init__(self, path, max_bytes):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.rows = []
        self.size = 0
        self.file = self.path.open("xb")
        self.mapping = None

    def append(self, arrays):
        size = sum(a.nbytes for a in arrays.values())
        if self.size + size > self.max_bytes:
            raise MemoryError("trial graph cache exceeds the explicit byte limit")
        entry = {}
        for key, array in arrays.items():
            array = np.ascontiguousarray(array)
            entry[key] = (self.size, array.dtype.str, array.shape)
            self.file.write(array.tobytes())
            self.size += array.nbytes
        self.rows.append(entry)

    def finish(self):
        self.file.close()
        self.file = None
        self.mapping = np.memmap(self.path, dtype=np.uint8, mode="r")
        LOG.info("graph cache ready rows=%d bytes=%d", len(self.rows), self.size)

    def __getitem__(self, index):
        return {key: np.ndarray(shape, dtype=dtype, buffer=self.mapping, offset=offset)
                for key, (offset, dtype, shape) in self.rows[index].items()}

    def close(self):
        if self.file is not None:
            self.file.close()
        if self.mapping is not None:
            self.mapping._mmap.close()
            self.mapping = None


def batches(cache, indices, batch_size, max_nodes=40_000, max_edges=400_000):
    current, nodes, edges = [], 0, 0
    for index in indices:
        row = cache.rows[int(index)]
        n, e = row["x"][2][0], row["edge"][2][0]
        if n > max_nodes or e > max_edges:
            raise MemoryError("one graph exceeds the explicit batch limits")
        if current and (len(current) >= batch_size or nodes + n > max_nodes or edges + e > max_edges):
            yield current
            current, nodes, edges = [], 0, 0
        current.append(int(index))
        nodes, edges = nodes + n, edges + e
    if current:
        yield current


def collate(cache, indices):
    started = time.perf_counter()
    arrays = [cache[index] for index in indices]
    offsets = np.r_[0, np.cumsum([len(a["x"]) for a in arrays])[:-1]]
    out = {key: np.concatenate([a[key] for a in arrays]) for key in ("x", "kind", "edge")}
    out["edge_index"] = np.concatenate([a["edge_index"] + offset for a, offset in zip(arrays, offsets)], axis=1)
    out["query"] = np.concatenate([a["query"] + offset for a, offset in zip(arrays, offsets)])
    LOG.debug("collate exit seconds=%.6f graphs=%d", time.perf_counter() - started, len(indices))
    return out
