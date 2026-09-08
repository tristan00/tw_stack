import numpy as np
import copy

from .data import NodeKind


KEY_DTYPE = np.dtype([("group", np.int64), ("time", np.int64)])


def keys(group, times):
    out = np.empty(np.shape(group), dtype=KEY_DTYPE)
    out["group"], out["time"] = group, times
    return out


class GroupIndex:

    def __init__(self, groups, times, sources):
        valid = groups[sources] >= 0
        sources = sources[valid]
        order = np.lexsort((times[sources], groups[sources]))
        self.sources = sources[order]
        self.keys = keys(groups[self.sources], times[self.sources])

    def find(self, groups, times, count=1):
        if not len(self.sources):
            return np.full((len(groups), count), -1, dtype=np.int64)
        end = np.searchsorted(self.keys, keys(groups, times), side="right")
        start = np.searchsorted(self.keys, keys(groups, np.full(len(groups), -1)), side="left")
        length = end - start
        offsets = np.arange(count)[None, :]
        if count > 1:
            offsets = np.rint(offsets * np.maximum(length[:, None] - 1, 0) / (count - 1)).astype(np.int64)
        ix = end[:, None] - 1 - offsets
        valid = (ix >= start[:, None]) & (ix < end[:, None]) & (groups[:, None] >= 0)
        return np.where(valid, self.sources[np.clip(ix, 0, len(self.sources) - 1)], -1)


def normalized(values):
    length = np.linalg.norm(values, axis=1, keepdims=True)
    return np.divide(values, length, out=np.zeros_like(values), where=length > 1e-12)


def spatial_keys(xy):
    available = np.isfinite(xy).all(axis=1)
    cells = np.floor(np.where(available[:, None], xy, 0) / 32).astype(np.int64)
    hashed = (cells[:, 0].astype(np.uint64) * np.uint64(0x9E3779B185EBCA87)) ^ (cells[:, 1].astype(np.uint64) * np.uint64(0xC2B2AE3D27D4EB4F))
    return np.where(available, (hashed & np.uint64(2**63 - 1)).astype(np.int64), -1)


class CandidateIndex:

    def __init__(self, nodes, sources, seed):
        self.nodes, self.sources, self.seed = nodes, sources, seed
        self.stream = GroupIndex(nodes.stream, nodes.tick, sources)
        self.entity = GroupIndex(nodes.entity, nodes.tick, sources)
        self.reference = GroupIndex(nodes.reference, nodes.tick, sources)
        observations = sources[nodes.kind[sources] != NodeKind.ACTION]
        self.turn = GroupIndex(nodes.stream, nodes.turn, observations)
        width = nodes.values.shape[1]
        self.projection = np.sin((np.arange(width)[:, None] + 1) * (np.arange(8)[None, :] + 1) * 1.61803398875).astype(np.float32)
        self.signature = ((np.einsum("nd,dk->nk", nodes.values, self.projection, optimize=False) > 0) * (1 << np.arange(8))).sum(axis=1)
        self.content = GroupIndex(self.signature, nodes.tick, sources)
        self.spatial_keys = spatial_keys(nodes.xy)
        self.spatial = GroupIndex(self.spatial_keys, nodes.tick, sources)
        self.unit = normalized(nodes.values)
        self.sample_identity = np.arange(len(nodes.tick), dtype=np.uint64)

    def with_queries(self, nodes, query_ids):
        result = copy.copy(self)
        result.nodes = nodes
        extra = nodes.values[len(self.signature):]
        signature = ((np.einsum("nd,dk->nk", extra, self.projection, optimize=False) > 0) * (1 << np.arange(8))).sum(axis=1)
        result.signature = np.r_[self.signature, signature]
        result.spatial_keys = np.r_[self.spatial_keys, spatial_keys(nodes.xy[len(self.signature):])]
        result.unit = np.concatenate((self.unit, normalized(extra)), axis=0)
        result.sample_identity = np.r_[self.sample_identity, query_ids.astype(np.uint64) ^ np.uint64(0xD6E8FEB86659FD93)]
        return result

    def propose(self, receivers, count):
        nodes = self.nodes
        per = max(1, (count - 3) // 8)
        first = self.stream.find(nodes.stream[receivers], (nodes.step[receivers] - 1) * 2)
        safe = np.maximum(first[:, 0], 0)
        first[:, 0] = np.where((first[:, 0] >= 0) & (nodes.step[safe] == nodes.step[receivers] - 1)
                               & (nodes.tick[safe] % 2 == 0), first[:, 0], -1)
        previous_turn = self.turn.find(nodes.stream[receivers], nodes.turn[receivers] - 1)
        safe = np.maximum(previous_turn[:, 0], 0)
        previous_turn[:, 0] = np.where((previous_turn[:, 0] >= 0) & (nodes.turn[safe] == nodes.turn[receivers] - 1),
                                        previous_turn[:, 0], -1)
        available = np.searchsorted(nodes.tick[self.sources], nodes.tick[receivers], side="right")
        lag = np.rint(np.exp(np.linspace(0, 1, per)[None, :] * np.log(np.maximum(available[:, None], 1)))).astype(np.int64)
        ix = available[:, None] - lag
        temporal = np.where(ix >= 0, self.sources[np.maximum(ix, 0)], -1)
        columns = [first, previous_turn, self.entity.find(nodes.entity[receivers], nodes.tick[receivers], per),
                   self.reference.find(nodes.reference[receivers], nodes.tick[receivers], per),
                   self.entity.find(nodes.reference[receivers], nodes.tick[receivers], per),
                   self.content.find(self.signature[receivers], nodes.tick[receivers], per),
                   self.content.find(255 - self.signature[receivers], nodes.tick[receivers], per), temporal,
                   self.spatial.find(self.spatial_keys[receivers], nodes.tick[receivers], per)]
        remaining = count - sum(a.shape[1] for a in columns)
        counters = (self.sample_identity[receivers, None] + np.uint64(1)) * np.uint64(0x9E3779B185EBCA87)
        counters = counters ^ ((np.arange(remaining, dtype=np.uint64)[None, :] + self.seed + 1) * np.uint64(0xC2B2AE3D27D4EB4F))
        counters ^= counters >> np.uint64(29)
        sample = (counters % np.maximum(available[:, None], 1).astype(np.uint64)).astype(np.int64)
        columns.append(np.where(available[:, None] > 0, self.sources[sample], -1))
        candidates = np.concatenate(columns, axis=1)
        candidates.sort(axis=1)
        duplicate = np.c_[np.zeros(len(receivers), dtype=bool), candidates[:, 1:] == candidates[:, :-1]]
        safe = np.maximum(candidates, 0)
        valid = (candidates >= 0) & ~duplicate & (candidates != receivers[:, None])
        valid &= nodes.tick[safe] <= nodes.tick[receivers, None]
        return np.where(valid, candidates, -1)
