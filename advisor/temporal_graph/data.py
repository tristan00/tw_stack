from dataclasses import dataclass
from enum import IntEnum
import logging
import time

import numpy as np


LOG = logging.getLogger(__name__)


class NodeKind(IntEnum):
    OBSERVATION = 0
    CHANGE = 1
    BUNDLE = 2
    TRACE = 3
    ACTION = 4
    QUERY = 5
    GROUP = 6


def integers(value, size, name):
    array = np.asarray(value)
    if array.shape != (size,) or array.dtype.kind not in "iu":
        raise ValueError("%s must be a one-dimensional integer array of length %d" % (name, size))
    return array.astype(np.int64, copy=True)


def immutable(array):
    array.flags.writeable = False
    return array


def feature_names(names, width):
    names = tuple(names)
    if len(names) != width or len(set(names)) != width or not all(isinstance(n, str) and n for n in names):
        raise ValueError("feature_names must uniquely name every value column")
    return names


def predecessor(stream, ticks):
    order = np.lexsort((ticks, stream))
    previous = np.full(len(stream), -1, dtype=np.int64)
    same = stream[order[1:]] == stream[order[:-1]]
    previous[order[1:][same]] = order[:-1][same]
    return previous


@dataclass(frozen=True)
class History:
    values: np.ndarray
    present: np.ndarray
    stream: np.ndarray
    step: np.ndarray
    turn: np.ndarray
    entity: np.ndarray
    reference: np.ndarray
    kind: np.ndarray
    xy: np.ndarray
    names: tuple[str, ...]
    tick: np.ndarray
    previous: np.ndarray
    delta: np.ndarray
    profile: np.ndarray
    episode: str

    @classmethod
    def from_arrays(cls, values, stream, step, turn, *, names, present=None, entity=None,
                    reference=None, kind=None, xy=None, episode="campaign"):
        started = time.perf_counter()
        values = np.array(values, dtype=np.float32, copy=True)
        if values.ndim != 2 or not values.shape[0] or not values.shape[1]:
            raise ValueError("values must be a nonempty observation-by-feature matrix")
        n, width = values.shape
        names = feature_names(names, width)
        present = np.ones_like(values, dtype=bool) if present is None else np.array(present, dtype=bool, copy=True)
        if present.shape != values.shape or not np.isfinite(values[present]).all():
            raise ValueError("present values must be finite, with a matching presence mask")
        values[~present] = 0
        stream, step, turn = (integers(v, n, k) for v, k in ((stream, "stream"), (step, "step"), (turn, "turn")))
        if np.any(stream < 0) or np.any(step < 0) or np.any(turn < 0) or np.any(step > (2**62 - 1)):
            raise ValueError("stream, step and turn must be nonnegative; step must fit doubled int64")
        entity = stream.copy() if entity is None else integers(entity, n, "entity")
        reference = np.full(n, -1, dtype=np.int64) if reference is None else integers(reference, n, "reference")
        kind = np.zeros(n, dtype=np.int64) if kind is None else integers(kind, n, "kind")
        if not np.isin(kind, (NodeKind.OBSERVATION, NodeKind.ACTION)).all():
            raise ValueError("history rows must be observations or selected action events")
        xy = np.full((n, 2), np.nan, dtype=np.float32) if xy is None else np.array(xy, dtype=np.float32, copy=True)
        if xy.shape != (n, 2) or np.isinf(xy).any():
            raise ValueError("xy must have shape (observations, 2), with NaN for unavailable positions")
        if not isinstance(episode, str) or not episode:
            raise ValueError("one explicit campaign/branch episode is required")
        tick = step * 2 + (kind == NodeKind.ACTION)
        order = np.lexsort((stream, tick))
        values, present, stream, step, turn, entity, reference, kind, xy, tick = (
            a[order] for a in (values, present, stream, step, turn, entity, reference, kind, xy, tick))
        if np.any(np.diff(turn) < 0) or np.any((np.diff(step) == 0) & (np.diff(turn) != 0)):
            raise ValueError("turns must be monotonic and identical within each decision step")
        previous = predecessor(stream, tick)
        safe = np.maximum(previous, 0)
        if np.any((previous >= 0) & ((tick == tick[safe]) | (kind != kind[safe]))):
            raise ValueError("each stream has one row per cutoff and a stable observation/action kind")
        delta = np.where((previous >= 0)[:, None] & present & present[safe], values - values[safe], 0)
        profile = np.zeros((n, 8), dtype=np.float32)
        ix = np.arange(n)
        for lag in range(4):
            valid = (ix >= 0) & (previous[np.maximum(ix, 0)] >= 0)
            profile[:, lag] = np.where(valid, delta[np.maximum(ix, 0)].mean(axis=1), 0)
            profile[:, lag + 4] = valid
            ix = np.where(ix >= 0, previous[np.maximum(ix, 0)], -1)
        result = cls(*(immutable(a) for a in (values, present, stream, step, turn, entity, reference, kind, xy)),
                     names, *(immutable(a) for a in (tick, previous, delta, profile)), episode)
        LOG.info("prepare_history exit seconds=%.6f rows=%d", time.perf_counter() - started, n)
        return result


@dataclass(frozen=True)
class ActionQueries:
    values: np.ndarray
    ids: np.ndarray
    entity: np.ndarray
    reference: np.ndarray
    xy: np.ndarray

    @classmethod
    def from_arrays(cls, values, *, ids, entity=None, reference=None, xy=None):
        values = np.array(values, dtype=np.float32, copy=True)
        if values.ndim != 2 or not np.isfinite(values).all():
            raise ValueError("query values must be a finite matrix")
        n = len(values)
        ids = integers(ids, n, "query ids")
        if len(np.unique(ids)) != n:
            raise ValueError("query ids must be unique")
        entity = np.full(n, -1, dtype=np.int64) if entity is None else integers(entity, n, "entity")
        reference = np.full(n, -1, dtype=np.int64) if reference is None else integers(reference, n, "reference")
        xy = np.full((n, 2), np.nan, dtype=np.float32) if xy is None else np.array(xy, dtype=np.float32, copy=True)
        if xy.shape != (n, 2) or np.isinf(xy).any():
            raise ValueError("query xy must have shape (queries, 2)")
        return cls(*(immutable(a) for a in (values, ids, entity, reference, xy)))


@dataclass(frozen=True)
class Nodes:
    values: np.ndarray
    present: np.ndarray
    delta: np.ndarray
    profile: np.ndarray
    stream: np.ndarray
    entity: np.ndarray
    reference: np.ndarray
    kind: np.ndarray
    step: np.ndarray
    turn: np.ndarray
    tick: np.ndarray
    start: np.ndarray
    xy: np.ndarray
    mass: np.ndarray
    member_offsets: np.ndarray
    members: np.ndarray
