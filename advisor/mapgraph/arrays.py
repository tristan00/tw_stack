import numpy as np

from advisor.mapgraph import schema as S


IDX_FIELDS = ("node_type", "race_idx", "agent_idx", "stance_idx", "subtype_idx",
              "atype_idx", "term_idx", "cat_idx")


def to_arrays(g, y=None, taken=None):
    ne, nn = len(g.src), len(g.x)
    src = np.fromiter(g.src, dtype=np.int64, count=ne)
    dst = np.fromiter(g.dst, dtype=np.int64, count=ne)
    rel = np.fromiter(g.rel, dtype=np.int64, count=ne)
    ntype = np.fromiter(g.node_type, dtype=np.int64, count=nn)
    val = np.fromiter(g.val, dtype=np.float32, count=ne)
    dirs = np.stack((np.fromiter(g.ux, dtype=np.float32, count=ne),
                     np.fromiter(g.uy, dtype=np.float32, count=ne)), axis=1)
    is_action = ntype == S.ACTION_TYPE_INDEX
    src_action, dst_action = is_action[src], is_action[dst]
    masks = (~(src_action | dst_action), src_action & ~dst_action, dst_action & ~src_action)
    data = {"x": np.asarray(g.x, dtype=np.float32), "node_type": ntype}
    for name, mask in zip(("edge", "a2e", "e2a"), masks):
        indices = np.stack((src[mask], dst[mask]))
        values = (indices, rel[mask], val[mask], dirs[mask])
        data.update((name + "_" + key, value)
                    for key, value in zip(("index", "rel", "val", "dir"), values))
    for name in IDX_FIELDS[1:]:
        data[name] = np.fromiter(getattr(g, name), dtype=np.int64, count=nn)
    data["g_ctx"] = np.asarray([g.g_ctx], dtype=np.float32)
    na = len(g.action_nodes)
    data["action_index"] = np.fromiter(g.action_nodes or [0], dtype=np.int64, count=na or 1)
    data["n_actions"] = np.asarray([na], dtype=np.int64)
    if taken is not None:
        data["is_taken"] = np.asarray(taken, dtype=np.float32)
    if y is not None:
        data["y"] = np.asarray([float(y)], dtype=np.float32)
    return data
