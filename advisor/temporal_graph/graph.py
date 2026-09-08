from dataclasses import dataclass
from enum import IntFlag
import hashlib
import logging
import time

import numpy as np

from .config import GraphConfig, GraphLimits
from .data import History, NodeKind, Nodes, immutable
from .nodes import append_queries, materialize
from .retrieval import CandidateIndex


LOG = logging.getLogger(__name__)


class EdgeKind(IntFlag):
    PREVIOUS_ACTION = 1
    PREVIOUS_TURN = 2
    IDENTITY = 4
    REFERENCE = 8
    SIMILARITY = 16
    CONTRAST = 32
    COCHANGE = 64
    GEOMETRIC = 128
    LEARNED = 256
    GROUP_IN = 512
    GROUP_OUT = 1024
    QUERY = 2048
    TEMPORAL = 4096


@dataclass
class TemporalGraph:
    nodes: Nodes
    edge_index: np.ndarray
    edge_kind: np.ndarray
    edge_score: np.ndarray
    decision_gap: np.ndarray
    turn_gap: np.ndarray
    query_index: np.ndarray
    query_ids: np.ndarray
    receiver_index: np.ndarray
    metrics: dict

    def fingerprint(self):
        digest = hashlib.sha256()
        for array in (self.nodes.kind, self.nodes.stream, self.nodes.step, self.nodes.start,
                      self.nodes.member_offsets, self.nodes.members, self.edge_index):
            digest.update(str((array.shape, array.dtype)).encode())
            digest.update(np.ascontiguousarray(array).tobytes())
        return digest.hexdigest()[:20]


class GraphBuildState:

    def __init__(self):
        self.history = None
        self.key = None
        self.nodes = None
        self.index = None
        self.queries = None
        self.query_nodes = None
        self.query_index = None
        self.query_ids = None
        self.query_retrieval = None

    def prepare(self, history, config, cutoff, limits):
        key = (config.node_view, config.resolution, config.history, cutoff, limits.seed)
        if history is self.history and key == self.key:
            return self.nodes, self.index, True, 0.0, 0.0
        started = time.perf_counter()
        nodes = materialize(history, config, cutoff)
        if len(nodes.tick) > limits.max_nodes:
            raise ValueError("materialized nodes exceed max_nodes")
        for name in nodes.__dataclass_fields__:
            immutable(getattr(nodes, name))
        materialize_seconds = time.perf_counter() - started
        started = time.perf_counter()
        index = CandidateIndex(nodes, np.arange(len(nodes.tick)), limits.seed)
        index_seconds = time.perf_counter() - started
        self.history, self.key, self.nodes, self.index = history, key, nodes, index
        self.query_nodes = None
        return nodes, index, False, materialize_seconds, index_seconds

    def attach_queries(self, queries, cutoff):
        if self.query_nodes is not None and queries is self.queries:
            return self.query_nodes, self.query_index, self.query_ids, self.query_retrieval, True
        nodes, query_index, query_ids = append_queries(self.nodes, queries, cutoff, int(self.nodes.turn[-1]))
        index = self.index.with_queries(nodes, query_ids)
        self.queries, self.query_nodes = queries, nodes
        self.query_index, self.query_ids, self.query_retrieval = query_index, query_ids, index
        return nodes, query_index, query_ids, index, False


def relation_flags(nodes, source, destination):
    same_stream = (nodes.stream[source] >= 0) & (nodes.stream[source] == nodes.stream[destination])
    same_entity = (nodes.entity[source] >= 0) & (nodes.entity[source] == nodes.entity[destination])
    reference = ((nodes.reference[source] >= 0) & (nodes.reference[source] == nodes.reference[destination]))
    reference |= (nodes.entity[source] >= 0) & (nodes.entity[source] == nodes.reference[destination])
    reference |= (nodes.reference[source] >= 0) & (nodes.reference[source] == nodes.entity[destination])
    flags = ((same_stream | same_entity).astype(np.uint16) * int(EdgeKind.IDENTITY)
             | reference.astype(np.uint16) * int(EdgeKind.REFERENCE))
    flags |= ((nodes.step[source] < nodes.step[destination]).astype(np.uint16) * int(EdgeKind.TEMPORAL))
    flags |= ((nodes.kind[destination] == NodeKind.QUERY).astype(np.uint16) * int(EdgeKind.QUERY))
    return flags


def affinity(nodes, receivers, candidates, kernel, unit, learned):
    source = np.maximum(candidates, 0)
    destination = receivers[:, None]
    valid = candidates >= 0
    flags = relation_flags(nodes, source, destination)
    identity = (flags & int(EdgeKind.IDENTITY)) != 0
    reference = (flags & int(EdgeKind.REFERENCE)) != 0
    similarity = np.zeros(candidates.shape, dtype=np.float32)
    if kernel in ("similarity", "contrast", "mixed"):
        similarity = np.einsum("bld,bd->bl", unit[source], unit[receivers], optimize=False)
    cochange = np.zeros_like(similarity)
    supported = np.zeros_like(valid)
    if kernel in ("cochange", "mixed"):
        left, right = nodes.profile[source, :4], nodes.profile[receivers, None, :4]
        shared = (nodes.profile[source, 4:] > 0) & (nodes.profile[receivers, None, 4:] > 0)
        count = shared.sum(axis=2)
        left_mean = np.sum(left * shared, axis=2) / np.maximum(count, 1)
        right_mean = np.sum(right * shared, axis=2) / np.maximum(count, 1)
        left_center = (left - left_mean[:, :, None]) * shared
        right_center = (right - right_mean[:, :, None]) * shared
        denominator = np.sqrt(np.sum(left_center**2, axis=2) * np.sum(right_center**2, axis=2))
        supported = (count >= 2) & (denominator > 1e-12)
        np.divide(np.sum(left_center * right_center, axis=2), denominator, out=cochange, where=supported)
    position = np.isfinite(nodes.xy[source]).all(axis=2) & np.isfinite(nodes.xy[receivers]).all(axis=1)[:, None]
    distance = np.zeros_like(similarity)
    if kernel in ("geometric", "mixed"):
        difference = np.where(position[:, :, None], nodes.xy[source] - nodes.xy[receivers, None], 0)
        distance = np.log1p(np.linalg.norm(difference, axis=2))
    if kernel == "identity":
        score, valid = identity.astype(np.float32), valid & identity
    elif kernel == "reference":
        score, valid = reference.astype(np.float32), valid & reference
    elif kernel == "similarity":
        score = similarity
        flags |= int(EdgeKind.SIMILARITY)
    elif kernel == "contrast":
        score = -similarity
        flags |= int(EdgeKind.CONTRAST)
    elif kernel == "cochange":
        score, valid = cochange, valid & supported
        flags |= int(EdgeKind.COCHANGE)
    elif kernel == "geometric":
        score, valid = -distance, valid & position
        flags |= int(EdgeKind.GEOMETRIC)
    elif kernel == "learned":
        left, right = learned
        score = np.einsum("bld,bd->bl", left[source], right[receivers], optimize=False) / np.sqrt(left.shape[1])
        flags |= int(EdgeKind.LEARNED)
    else:
        score = 0.35 * similarity + 0.35 * cochange + 0.3 * identity + 0.3 * reference - 0.05 * distance
        flags |= int(EdgeKind.SIMILARITY)
        flags |= supported.astype(np.uint16) * int(EdgeKind.COCHANGE)
        flags |= position.astype(np.uint16) * int(EdgeKind.GEOMETRIC)
        if learned is not None:
            left, right = learned
            score += np.einsum("bld,bd->bl", left[source], right[receivers], optimize=False) / np.sqrt(left.shape[1])
            flags |= int(EdgeKind.LEARNED)
    if np.any(valid & ~np.isfinite(score)):
        raise FloatingPointError("nonfinite graph affinity; check feature scaling and metric weights")
    return np.where(valid, score, -np.inf).astype(np.float32), flags


def top_edges(candidates, score, flags, receivers, degree):
    count = min(degree, candidates.shape[1])
    order = np.argsort(-score, axis=1, kind="stable")[:, :count]
    selected = np.take_along_axis(candidates, order, axis=1)
    weights = np.take_along_axis(score, order, axis=1)
    kinds = np.take_along_axis(flags, order, axis=1)
    valid = np.isfinite(weights) & (selected >= 0)
    destination = np.broadcast_to(receivers[:, None], selected.shape)
    return selected[valid], destination[valid], weights[valid], kinds[valid]


def group_edges(nodes, batches, max_nodes, max_edges):
    members = np.concatenate([b[0] for b in batches], axis=0)
    receivers = np.concatenate([b[1] for b in batches])
    weights = np.concatenate([b[2] for b in batches])
    groups, inverse = np.unique(members, axis=0, return_inverse=True)
    n, count = len(nodes.tick), len(groups)
    if n + count > max_nodes:
        raise ValueError("group nodes exceed max_nodes")
    present = groups >= 0
    if int(present.sum()) + len(inverse) > max_edges:
        raise ValueError("group incidence exceeds max_edges")
    safe = np.maximum(groups, 0)
    mass = present.sum(axis=1)
    group_index = np.arange(n, n + count)
    last = np.max(np.where(present, nodes.tick[safe], -1), axis=1)
    last_member = safe[np.arange(count), np.argmax(np.where(present, nodes.tick[safe], -1), axis=1)]
    value_present = present[:, :, None] & nodes.present[safe]
    value_count = value_present.sum(axis=1)
    values = (nodes.values[safe] * value_present).sum(axis=1) / np.maximum(value_count, 1)
    tail = dict(values=values.astype(np.float32), present=value_count > 0,
                delta=(nodes.delta[safe] * present[:, :, None]).sum(axis=1),
                profile=np.zeros((count, 8), dtype=np.float32), stream=np.full(count, -1),
                entity=np.full(count, -1), reference=np.full(count, -1), kind=np.full(count, NodeKind.GROUP),
                step=nodes.step[last_member], turn=nodes.turn[last_member], tick=last,
                start=np.min(np.where(present, nodes.start[safe], np.iinfo(np.int64).max), axis=1),
                xy=np.full((count, 2), np.nan, dtype=np.float32), mass=mass)
    output = {key: np.concatenate((getattr(nodes, key), value), axis=0) for key, value in tail.items()}
    nodes = Nodes(**output, member_offsets=np.r_[nodes.member_offsets, np.full(count, nodes.member_offsets[-1])],
                  members=nodes.members)
    source = np.r_[groups[present], group_index[inverse]]
    destination = np.r_[np.broadcast_to(group_index[:, None], groups.shape)[present], receivers]
    score = np.r_[np.broadcast_to(1 / mass[:, None], groups.shape)[present], weights]
    flags = np.r_[np.full(present.sum(), int(EdgeKind.GROUP_IN), dtype=np.uint16),
                  np.full(len(inverse), int(EdgeKind.GROUP_OUT), dtype=np.uint16)]
    return nodes, (source, destination, score.astype(np.float32), flags)


def build_temporal_graph(history, candidates=None, config=None, *, limits=None, cutoff=None,
                         receivers="all", learned=None, exhaustive=False, state=None):
    started = time.perf_counter()
    LOG.info("build_temporal_graph enter")
    if not isinstance(history, History):
        raise TypeError("history must be a prepared temporal_graph.History")
    config = GraphConfig() if config is None else config
    limits = GraphLimits() if limits is None else limits
    if not isinstance(config, GraphConfig) or not isinstance(limits, GraphLimits):
        raise TypeError("config and limits must use their explicit temporal_graph types")
    if receivers not in ("all", "current"):
        raise ValueError("receivers must be all or current")
    if config.degree > limits.candidates and not exhaustive and config.layout != "dense":
        raise ValueError("degree exceeds the declared candidate budget")
    if limits.query_degree > limits.candidates and not exhaustive and config.layout != "dense":
        raise ValueError("query_degree exceeds the declared candidate budget")
    cutoff = int(history.step[-1]) if cutoff is None else cutoff
    if type(cutoff) is not int or cutoff < 0:
        raise ValueError("cutoff must be a nonnegative decision index")
    state = GraphBuildState() if state is None else state
    if not isinstance(state, GraphBuildState):
        raise TypeError("state must be a temporal_graph.GraphBuildState")
    nodes, prepared_index, cache_hit, materialize_seconds, index_seconds = state.prepare(history, config, cutoff, limits)
    source_count = len(nodes.tick)
    phase = time.perf_counter()
    nodes, query_index, query_ids, index, query_cache_hit = state.attach_queries(candidates, cutoff)
    if len(nodes.tick) > limits.max_nodes:
        raise ValueError("materialized nodes exceed max_nodes")
    timings = {"materialize_seconds": materialize_seconds, "index_seconds": index_seconds,
               "query_seconds": time.perf_counter() - phase}
    source = prepared_index.sources
    ordinary = source if receivers == "all" else source[np.searchsorted(nodes.step[:source_count], cutoff):]
    receiver_index = np.r_[ordinary, query_index]
    unit = index.unit
    metric = None
    if learned is not None:
        left, right = learned.matrices(nodes.values.shape[1])
        metric = np.einsum("nd,dk->nk", nodes.values, left, optimize=False), np.einsum("nd,dk->nk", nodes.values, right, optimize=False)
    if config.kernel == "learned" and metric is None:
        raise ValueError("learned kernel requires explicit LearnedMetric weights")
    dense = exhaustive or config.layout == "dense"
    if dense and len(receiver_index) * source_count > limits.dense_pair_limit:
        raise ValueError("exact pair work exceeds dense_pair_limit")
    hub = np.ones(source_count, dtype=bool)
    if config.layout == "hub":
        order = np.lexsort((source, index.signature[source], nodes.tick[source]))
        boundary = np.r_[True, np.diff(nodes.tick[order]) != 0]
        starts = np.maximum.accumulate(np.where(boundary, np.arange(source_count), 0))
        hub[:] = False
        hub[order[(np.arange(source_count) - starts) % config.arity == 0]] = True
    edges, groups = [], []
    slots, valid_pairs, temporary_bytes, edge_count, group_outputs = 0, 0, 0, 0, 0
    retrieve_seconds = score_seconds = select_seconds = 0.0
    scoring_receivers = query_index if config.layout == "empty" else receiver_index
    for offset in range(0, len(scoring_receivers), limits.block_size):
        edges_before = len(edges)
        dst = scoring_receivers[offset:offset + limits.block_size]
        phase = time.perf_counter()
        if dense:
            proposals = np.broadcast_to(source, (len(dst), source_count)).copy()
            valid = (nodes.tick[proposals] <= nodes.tick[dst, None]) & (proposals != dst[:, None])
            proposals[~valid] = -1
        else:
            proposals = index.propose(dst, limits.candidates)
        retrieve_seconds += time.perf_counter() - phase
        slots += proposals.size
        valid_pairs += int(np.count_nonzero(proposals >= 0))
        temporary_bytes = max(temporary_bytes, proposals.nbytes + proposals.size * (nodes.values.shape[1] * 4 + 32))
        phase = time.perf_counter()
        scores, flags = affinity(nodes, dst, proposals, config.kernel, unit, metric)
        safe = np.maximum(proposals, 0)
        gap = np.maximum(nodes.step[dst, None] - nodes.step[safe], 0)
        turn_gap = np.maximum(nodes.turn[dst, None] - nodes.turn[safe], 0)
        scores -= config.time_bias * (np.log1p(gap) + np.log1p(turn_gap))
        previous_action = index.stream.find(nodes.stream[dst], (nodes.step[dst] - 1) * 2)[:, 0]
        previous_turn = index.turn.find(nodes.stream[dst], nodes.turn[dst] - 1)[:, 0]
        flags |= ((proposals == previous_action[:, None]) & (gap == 1) & (nodes.tick[safe] % 2 == 0)).astype(np.uint16) * int(EdgeKind.PREVIOUS_ACTION)
        flags |= ((proposals == previous_turn[:, None]) & (turn_gap == 1)).astype(np.uint16) * int(EdgeKind.PREVIOUS_TURN)
        score_seconds += time.perf_counter() - phase
        phase = time.perf_counter()
        query = nodes.kind[dst] == NodeKind.QUERY
        if np.any(query):
            edges.append(top_edges(proposals[query], scores[query], flags[query], dst[query], limits.query_degree))
        proposals, scores, flags, dst = proposals[~query], scores[~query], flags[~query], dst[~query]
        if len(dst) and (config.degree > 0 or config.layout == "dense") and config.layout != "empty":
            if config.layout == "forest":
                scores = np.where(proposals < dst[:, None], scores, -np.inf)
                edges.append(top_edges(proposals, scores, flags, dst, 1))
            elif config.layout == "hub":
                scores = np.where(hub[np.maximum(proposals, 0)], scores, -np.inf)
                edges.append(top_edges(proposals, scores, flags, dst, config.degree))
            elif config.layout == "radius":
                finite = np.isfinite(scores)
                count = finite.sum(axis=1)
                total = np.where(finite, scores, 0).sum(axis=1)
                mean = total / np.maximum(count, 1)
                deviation = np.sqrt((np.where(finite, scores - mean[:, None], 0)**2).sum(axis=1) / np.maximum(count, 1))
                threshold = mean + deviation * np.log(np.maximum(count, 1) / (config.degree + 1))
                scores = np.where(scores >= threshold[:, None], scores, -np.inf)
                edges.append(top_edges(proposals, scores, flags, dst, proposals.shape[1]))
            elif config.layout == "incidence":
                count = min(proposals.shape[1], config.degree * config.arity)
                arity = min(config.arity, count)
                order = np.argsort(-scores, axis=1, kind="stable")[:, :count]
                members = np.take_along_axis(proposals, order, axis=1)
                weights = np.take_along_axis(scores, order, axis=1)
                members[~np.isfinite(weights)] = -1
                padding = (-count) % arity
                members = np.pad(members, ((0, 0), (0, padding)), constant_values=-1).reshape(-1, arity)
                weights = np.pad(weights, ((0, 0), (0, padding)), constant_values=-np.inf).reshape(-1, arity)
                targets = np.repeat(dst, (count + padding) // arity)
                members.sort(axis=1)
                counts = (members >= 0).sum(axis=1)
                means = np.where(np.isfinite(weights), weights, 0).sum(axis=1) / np.maximum(counts, 1)
                keep = counts >= 2
                if np.any(keep):
                    groups.append((members[keep], targets[keep], means[keep]))
                    group_outputs += int(keep.sum())
            else:
                degree = proposals.shape[1] if config.layout == "dense" else config.degree
                edges.append(top_edges(proposals, scores, flags, dst, degree))
        edge_count += sum(len(e[0]) for e in edges[edges_before:])
        if edge_count + group_outputs > limits.max_edges:
            raise ValueError("selected edges exceed max_edges")
        select_seconds += time.perf_counter() - phase
    phase = time.perf_counter()
    if groups:
        nodes, incidence = group_edges(nodes, groups, limits.max_nodes, limits.max_edges)
        edges.append(incidence)
    if edges:
        src, dst, score, flags = (np.concatenate([e[i] for e in edges]) for i in range(4))
    else:
        src, dst = np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
        score, flags = np.empty(0, dtype=np.float32), np.empty(0, dtype=np.uint16)
    if config.layout == "reciprocal" and len(src):
        n = len(nodes.tick)
        paired = np.isin(src * n + dst, dst * n + src)
        keep = paired | (nodes.tick[src] < nodes.tick[dst]) | (nodes.kind[dst] == NodeKind.QUERY)
        src, dst, score, flags = src[keep], dst[keep], score[keep], flags[keep]
    order = np.lexsort((src, dst))
    src, dst, score, flags = src[order], dst[order], score[order], flags[order]
    if len(src) > limits.max_edges:
        raise ValueError("selected edges exceed max_edges")
    timings.update(retrieve_seconds=retrieve_seconds, score_seconds=score_seconds,
                   select_seconds=select_seconds, assemble_seconds=time.perf_counter() - phase)
    metrics = dict(timings, seconds=time.perf_counter() - started, nodes=len(nodes.tick), edges=len(src),
                   sources=source_count, receivers=len(receiver_index), queries=len(query_index),
                   candidate_slots=slots, valid_candidates=valid_pairs, exact=dense,
                   pair_workspace_estimate_bytes=temporary_bytes, episode=history.episode,
                   cache_hit=cache_hit, query_cache_hit=query_cache_hit)
    graph = TemporalGraph(nodes, np.vstack((src, dst)), flags, score,
                          nodes.step[dst] - nodes.step[src], nodes.turn[dst] - nodes.turn[src],
                          query_index, query_ids, receiver_index, metrics)
    for name in nodes.__dataclass_fields__:
        immutable(getattr(nodes, name))
    for array in (graph.edge_index, graph.edge_kind, graph.edge_score, graph.decision_gap,
                  graph.turn_gap, graph.query_index, graph.query_ids, graph.receiver_index):
        immutable(array)
    metrics["array_bytes"] = sum(getattr(nodes, key).nbytes for key in nodes.__dataclass_fields__)
    metrics["array_bytes"] += sum(a.nbytes for a in (graph.edge_index, flags, score, graph.decision_gap, graph.turn_gap))
    metrics["seconds"] = time.perf_counter() - started
    LOG.info("build_temporal_graph exit seconds=%.6f nodes=%d edges=%d candidates=%d", metrics["seconds"], len(nodes.tick), len(src), slots)
    return graph
