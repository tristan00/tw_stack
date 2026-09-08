import numpy as np

from .data import NodeKind, Nodes


def materialize(history, config, cutoff):
    lo = np.searchsorted(history.step, max(0, cutoff - config.history), side="left")
    hi = np.searchsorted(history.tick, cutoff * 2, side="right")
    raw = np.arange(lo, hi, dtype=np.int64)
    if not len(raw):
        raise ValueError("no observations in the requested prefix")
    observation = history.kind[raw] == NodeKind.OBSERVATION
    previous = history.previous[raw]
    changed = np.any(history.delta[raw] != 0, axis=1)
    changed |= np.any(history.present[raw] != history.present[np.maximum(previous, 0)], axis=1)
    changed |= previous < lo
    order = np.lexsort((history.tick[raw], history.stream[raw]))
    latest = np.zeros(len(raw), dtype=bool)
    ends = np.r_[history.stream[raw[order[1:]]] != history.stream[raw[order[:-1]]], True]
    latest[order[ends]] = True
    anchors = observation & latest
    age = cutoff - history.step[raw]
    keep = np.ones(len(raw), dtype=bool)
    kinds = history.kind[raw].copy()
    if config.node_view in ("change", "bundle", "mixed"):
        keep = ~observation | anchors | changed
        if config.node_view == "mixed":
            keep |= age > config.resolution
        kinds[observation & ~anchors] = NodeKind.CHANGE
    raw, anchors, age, kinds = raw[keep], anchors[keep], age[keep], kinds[keep]
    keys = np.column_stack((np.zeros(len(raw), dtype=np.int64), raw, np.zeros(len(raw), dtype=np.int64)))
    eligible = (history.kind[raw] == NodeKind.OBSERVATION) & ~anchors
    if config.node_view == "bundle":
        ix = np.flatnonzero(eligible)
        steps = history.step[raw[ix]]
        starts = np.maximum.accumulate(np.where(np.r_[True, np.diff(steps) != 0], np.arange(len(ix)), 0)) if len(ix) else ix
        keys[ix] = np.column_stack((np.ones(len(ix), dtype=np.int64), steps, (np.arange(len(ix)) - starts) // config.resolution))
        kinds[ix] = NodeKind.BUNDLE
    elif config.node_view in ("trace", "mixed"):
        if config.node_view == "mixed":
            eligible &= age > config.resolution
        ix = np.flatnonzero(eligible)
        order = np.lexsort((history.tick[raw[ix]], history.stream[raw[ix]]))
        ix = ix[order]
        streams = history.stream[raw[ix]]
        buckets = np.zeros(len(ix), dtype=np.int64)
        if config.node_view == "mixed":
            buckets = np.floor(np.log2(np.maximum(age[ix], 1))).astype(np.int64)
        boundary = np.r_[True, (np.diff(streams) != 0) | (np.diff(buckets) != 0)] if len(ix) else np.empty(0, bool)
        starts = np.maximum.accumulate(np.where(boundary, np.arange(len(ix)), 0)) if len(ix) else ix
        segment = np.cumsum(boundary)
        keys[ix] = np.column_stack((np.full(len(ix), 2), segment, (np.arange(len(ix)) - starts) // config.resolution))
        kinds[ix] = NodeKind.TRACE
    _, labels = np.unique(keys, axis=0, return_inverse=True)
    order = np.lexsort((history.tick[raw], labels))
    raw, labels, kinds = raw[order], labels[order], kinds[order]
    starts = np.r_[0, np.flatnonzero(np.diff(labels)) + 1]
    ends = np.r_[starts[1:], len(raw)] - 1
    group_order = np.argsort(history.tick[raw[ends]], kind="stable")
    remap = np.empty(len(starts), dtype=np.int64)
    remap[group_order] = np.arange(len(starts))
    order = np.argsort(remap[labels], kind="stable")
    raw, labels, kinds = raw[order], remap[labels[order]], kinds[order]
    starts = np.r_[0, np.flatnonzero(np.diff(labels)) + 1]
    ends = np.r_[starts[1:], len(raw)] - 1
    counts = np.diff(np.r_[starts, len(raw)])
    present_counts = np.add.reduceat(history.present[raw].astype(np.int64), starts, axis=0)
    values = np.add.reduceat(history.values[raw], starts, axis=0) / np.maximum(present_counts, 1)
    delta = np.add.reduceat(np.where((history.previous[raw] >= lo)[:, None], history.delta[raw], 0), starts, axis=0)
    profile = history.profile[raw[ends]].copy()
    if config.history == 0:
        delta[:] = 0
        profile[:] = 0
    identifiers = []
    for column in (history.stream, history.entity, history.reference):
        low = np.minimum.reduceat(column[raw], starts)
        high = np.maximum.reduceat(column[raw], starts)
        identifiers.append(np.where(low == high, low, -1))
    positions = history.xy[raw]
    position_present = np.isfinite(positions).all(axis=1)
    xy_count = np.add.reduceat(position_present.astype(np.int64), starts)
    xy_sum = np.add.reduceat(np.where(position_present[:, None], positions, 0), starts, axis=0)
    xy = np.full_like(xy_sum, np.nan)
    np.divide(xy_sum, xy_count[:, None], out=xy, where=xy_count[:, None] > 0)
    return Nodes(values.astype(np.float32), present_counts > 0, delta.astype(np.float32), profile,
                 *identifiers, kinds[ends], history.step[raw[ends]], history.turn[raw[ends]],
                 history.tick[raw[ends]], history.step[raw[starts]], xy, counts,
                 np.r_[starts, len(raw)], raw)


def append_queries(nodes, queries, cutoff, turn):
    if queries is None:
        return nodes, np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    if queries.values.shape[1] != nodes.values.shape[1]:
        raise ValueError("queries must use the same declared feature columns as history")
    n = len(queries.values)
    index = np.arange(len(nodes.tick), len(nodes.tick) + n)
    tail = dict(values=queries.values, present=np.ones_like(queries.values, dtype=bool),
                delta=np.zeros_like(queries.values), profile=np.zeros((n, 8), dtype=np.float32),
                stream=np.full(n, -1), entity=queries.entity, reference=queries.reference,
                kind=np.full(n, NodeKind.QUERY), step=np.full(n, cutoff), turn=np.full(n, turn),
                tick=np.full(n, cutoff * 2), start=np.full(n, cutoff), xy=queries.xy,
                mass=np.ones(n, dtype=np.int64))
    result = {name: np.concatenate((getattr(nodes, name), value), axis=0) for name, value in tail.items()}
    return Nodes(**result, member_offsets=np.r_[nodes.member_offsets, np.full(n, nodes.member_offsets[-1])],
                 members=nodes.members), index, queries.ids.copy()
