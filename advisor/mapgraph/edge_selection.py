import numpy as np

from advisor.mapgraph import schema as S


def _sparse(g, positions, ranks, observe):
    edges = np.asarray(g._edges, dtype=np.float64).reshape(-1, 6)
    if not len(edges):
        return edges
    src, dst, rel = edges[:, :3].astype(np.int64).T
    delta = positions[src] - positions[dst]
    distance = np.einsum("ij,ij->i", delta, delta)
    distance[~np.isfinite(distance)] = np.inf
    order = np.lexsort((edges[:, 5], edges[:, 4], edges[:, 3], ranks[src], distance, rel, dst))
    edges, src, dst, rel = edges[order], src[order], dst[order], rel[order]
    unique = np.r_[True, (src[1:] != src[:-1]) | (dst[1:] != dst[:-1]) | (rel[1:] != rel[:-1])]
    edges, dst, rel = edges[unique], dst[unique], rel[unique]
    starts = np.flatnonzero(np.r_[True, (dst[1:] != dst[:-1]) | (rel[1:] != rel[:-1])])
    counts = np.diff(np.r_[starts, len(edges)])
    if observe is not None:
        for r in np.unique(rel[starts]):
            observe(int(r), counts[rel[starts] == r])
    offsets = np.arange(len(edges)) - np.repeat(starts, counts)
    limits = np.asarray([g.config.edge_limits[name] for name in S.RELATIONS] * 2)
    return edges[offsets < limits[rel]]


def _spatial(g, sources, targets, relation, positions, ranks, excluded, direction, observe):
    cap = g.config.edge_limits[S.RELATIONS[relation % S.N_FORWARD_RELATIONS]]
    sources = np.asarray(sources, dtype=np.int64)
    targets = np.asarray(targets, dtype=np.int64)
    if not len(sources) or not len(targets) or (not cap and observe is None):
        return np.empty((0, 6))
    sources = sources[np.argsort(ranks[sources])]
    columns = np.full(len(positions), -1, dtype=np.int64)
    columns[sources] = np.arange(len(sources))
    blocks = []
    chunk_size = max(1, min(64, 8192 // len(sources)))
    for start in range(0, len(targets), chunk_size):
        dest = targets[start:start + chunk_size]
        eligible = np.ones((len(dest), len(sources)), dtype=bool)
        if direction:
            eligible &= (ranks[sources][None, :] < ranks[dest, None] if direction == 1
                         else ranks[sources][None, :] > ranks[dest, None])
        for row, target in enumerate(dest):
            banned = excluded.get(int(target), ())
            if banned:
                indices = columns[list(banned)]
                eligible[row, indices[indices >= 0]] = False
        if observe is not None:
            observe(relation, eligible.sum(axis=1))
        if not cap:
            continue
        delta = positions[dest, None, :] - positions[sources][None, :, :]
        distance = np.einsum("ijk,ijk->ij", delta, delta)
        distance[~np.isfinite(distance)] = np.inf
        order = np.lexsort((np.broadcast_to(ranks[sources], distance.shape), distance, ~eligible), axis=1)[:, :cap]
        rows = np.broadcast_to(np.arange(len(dest))[:, None], order.shape)
        keep = eligible[rows, order]
        src, dst = sources[order[keep]], dest[rows[keep]]
        delta = positions[dst] - positions[src]
        distance = np.sqrt(np.einsum("ij,ij->i", delta, delta))
        valid = np.isfinite(distance)
        unit = np.divide(delta, distance[:, None], out=np.zeros_like(delta),
                         where=valid[:, None] & (distance[:, None] > 0))
        blocks.append(np.column_stack((src, dst, np.full(len(src), relation),
                                       np.where(valid, distance, 0), unit)))
    return np.concatenate(blocks) if blocks else np.empty((0, 6))


def select_edges(g, observe=None):
    positions = np.asarray(g._positions, dtype=np.float64)
    ranks = np.empty(len(g.node_ids), dtype=np.int64)
    ranks[np.argsort(g.node_ids, kind="stable")] = np.arange(len(ranks))
    blocks = [_sparse(g, positions, ranks, observe)]
    spatial = np.flatnonzero(np.isin(g.node_type, [S.NODE_TYPES.index(t) for t in ("lord", "hero", "settlement")]))
    near = S.REL_INDEX["near"]
    for relation, direction in ((near, 1), (near + S.N_FORWARD_RELATIONS, -1)):
        blocks.append(_spatial(g, spatial, spatial, relation, positions, ranks, {}, direction, observe))
    actions, reverse_exclusions, forward_exclusions = [], {}, {}
    for action, actor, target in g._context_targets:
        actions.append(action)
        reverse_exclusions[action] = {i for i in (actor, target) if i is not None}
        for node in reverse_exclusions[action]:
            forward_exclusions.setdefault(node, set()).add(action)
    context = S.REL_INDEX["near_target"]
    blocks.append(_spatial(g, actions, spatial, context, positions, ranks, forward_exclusions, 0, observe))
    blocks.append(_spatial(g, spatial, actions, context + S.N_FORWARD_RELATIONS,
                           positions, ranks, reverse_exclusions, 0, observe))
    edges = np.concatenate(blocks)
    g.src, g.dst, g.rel = [v.astype(np.int64).tolist() for v in edges[:, :3].T]
    g.val, g.ux, g.uy = [v.tolist() for v in edges[:, 3:].T]
    g._edges.clear()
