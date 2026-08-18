from __future__ import annotations


import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.data import Data
from torch_geometric.nn import MessagePassing
from torch_geometric.utils import scatter, softmax

from advisor.mapgraph import schema as S

NET_KEYS = ("hidden", "entity_layers", "action_rounds", "map_aggr", "act_aggr", "attn",
            "conv", "conv_map", "conv_a2e", "conv_e2a", "dst_dim", "update",
            "self_transform")


class DecisionGraph(Data):

    def __cat_dim__(self, key, value, *args, **kwargs):
        if key == "g_ctx":
            return 0
        return super().__cat_dim__(key, value, *args, **kwargs)


_IDX_FIELDS = ("node_type", "race_idx", "agent_idx", "stance_idx", "subtype_idx",
               "atype_idx", "term_idx", "cat_idx")


def to_data(g, y=None, taken=None):
    act = S.ACTION_TYPE_INDEX
    ne, nn_ = len(g.src), len(g.x)
    src = np.fromiter(g.src, dtype=np.int64, count=ne)
    dst = np.fromiter(g.dst, dtype=np.int64, count=ne)
    rel = np.fromiter(g.rel, dtype=np.int64, count=ne)
    ntype = np.fromiter(g.node_type, dtype=np.int64, count=nn_)

    is_act = ntype == act
    s_act, d_act = is_act[src], is_act[dst]
    chan = []
    for mk in (~(s_act | d_act), s_act & ~d_act, d_act & ~s_act):
        s2, d2, r2 = src[mk], dst[mk], rel[mk]
        if s2.size == 0:
            chan.append((torch.zeros((2, 1), dtype=torch.long),
                         torch.zeros(1, dtype=torch.long)))
        else:
            chan.append((torch.from_numpy(np.stack((s2, d2))), torch.from_numpy(r2)))

    d = DecisionGraph(x=torch.from_numpy(np.asarray(g.x, dtype=np.float32)),
                      edge_index=chan[0][0])
    d.edge_rel = chan[0][1]
    d.a2e_index, d.a2e_rel = chan[1]
    d.e2a_index, d.e2a_rel = chan[2]
    d.node_type = torch.from_numpy(ntype)
    for name in _IDX_FIELDS[1:]:
        d[name] = torch.from_numpy(
            np.fromiter(getattr(g, name), dtype=np.int64, count=nn_))
    d.g_ctx = torch.from_numpy(np.asarray([g.g_ctx], dtype=np.float32))
    na = len(g.action_nodes)
    d.action_index = torch.from_numpy(
        np.fromiter(g.action_nodes or [0], dtype=np.int64, count=na or 1))
    d.n_actions = torch.tensor([na], dtype=torch.long)
    if taken is not None:
        d.is_taken = torch.from_numpy(np.asarray(taken, dtype=np.float32))
    if y is not None:
        d.y = torch.tensor([float(y)], dtype=torch.float32)
    return d


def _mlp(dims, dropout=0.0):
    layers = []
    for i in range(len(dims) - 1):
        layers.append(nn.Linear(dims[i], dims[i + 1]))
        if i < len(dims) - 2:
            layers.append(nn.ReLU())
            if dropout:
                layers.append(nn.Dropout(dropout))
    return nn.Sequential(*layers)


class RelConv(MessagePassing):

    def __init__(self, hidden, rel_dim, aggr, attn, conv, dst_dim):
        aggrs = [a for a in str(aggr).split("+") if a]
        super().__init__(aggr="add" if attn else (aggrs if len(aggrs) > 1 else aggrs[0]))
        self.conv_kind = conv
        d = 0 if conv == "sage" else (hidden if dst_dim is None else int(dst_dim))
        self.dst = nn.Linear(hidden, d) if (d and d != hidden) else None
        dim = d + hidden + rel_dim
        self.msg = _mlp([dim, hidden * 2, hidden])
        self.att = nn.Linear(dim, 1) if attn else None
        self.merge = (nn.Linear(hidden * len(aggrs), hidden)
                      if len(aggrs) > 1 and not attn else None)

    def forward(self, x, edge_index, rel_emb):
        if edge_index.numel() == 0:
            return x.new_zeros(x.size())
        out = self.propagate(edge_index, x=x, rel=rel_emb)
        return out if self.merge is None else self.merge(out)

    def message(self, x_i, x_j, rel, index, size_i):
        parts = [x_j, rel]
        if self.conv_kind != "sage":
            parts.insert(0, x_i if self.dst is None else self.dst(x_i))
        z = torch.cat(parts, dim=-1)
        m = self.msg(z)
        if self.att is None:
            return m
        return m * softmax(self.att(z), index, num_nodes=size_i)


_NT = len(S.NODE_TYPES)
_NORM_SAMPLE = 2_000_000


class TypeEncoders(nn.Module):

    def __init__(self, hidden):
        super().__init__()
        self.hidden = hidden
        self.lin = nn.Linear(_NT * S.MAX_FIELDS, hidden, bias=False)
        self.bias = nn.Parameter(torch.zeros(_NT, hidden))
        self.register_buffer("_span", torch.arange(S.MAX_FIELDS), persistent=False)
        self.register_buffer("center", torch.zeros(_NT, S.MAX_FIELDS))
        self.register_buffer("spread", torch.ones(_NT, S.MAX_FIELDS))

    @torch.no_grad()
    def fit_norm(self, datas, log=None):
        cols = [[[] for _ in range(S.MAX_FIELDS)] for _ in range(_NT)]
        for d in datas:
            x, nt = d.x, d.node_type
            for t in nt.unique().tolist():
                rows = x[nt == t]
                for c in range(S.MAX_FIELDS):
                    cols[t][c].append(rows[:, c])
        fitted = 0
        for t in range(_NT):
            for c in range(S.MAX_FIELDS):
                if not cols[t][c]:
                    continue
                v = torch.cat(cols[t][c])
                if v.numel() < 8:
                    continue
                if v.numel() > _NORM_SAMPLE:
                    gen = torch.Generator().manual_seed(t * S.MAX_FIELDS + c)
                    v = v[torch.randint(0, v.numel(), (_NORM_SAMPLE,), generator=gen)]
                q = torch.quantile(v, torch.tensor([0.25, 0.5, 0.75], dtype=v.dtype))
                iqr = float(q[2] - q[0])
                sd = float(v.std(unbiased=False))
                s = iqr / 1.349 if iqr > 0 else sd
                if s <= 0:
                    continue
                self.center[t, c] = float(q[1])
                self.spread[t, c] = s
                fitted += 1
        if log:
            log("mapgraph.net: input norm fitted on %d (node type, field) pairs" % fitted)
        return fitted

    def forward(self, x, node_type):
        x = (x - F.embedding(node_type, self.center)) / F.embedding(node_type, self.spread)
        cols = node_type.unsqueeze(1) * S.MAX_FIELDS + self._span
        big = x.new_zeros(x.size(0), _NT * S.MAX_FIELDS).scatter_(1, cols, x)
        return self.lin(big) + F.embedding(node_type, self.bias)


class TypeNorm(nn.Module):

    def __init__(self, hidden, eps=1e-5):
        super().__init__()
        self.eps = eps
        self.hidden = hidden
        self.affine = nn.Parameter(torch.cat(
            [torch.ones(_NT, hidden), torch.zeros(_NT, hidden)], dim=1))

    def forward(self, h, node_type):
        mu = h.mean(dim=-1, keepdim=True)
        var = h.var(dim=-1, unbiased=False, keepdim=True)
        x = (h - mu) * torch.rsqrt(var + self.eps)
        w, b = F.embedding(node_type, self.affine).split(self.hidden, dim=1)
        return x * w + b


class Encoder(nn.Module):

    def __init__(self, hidden, entity_layers, action_rounds, map_aggr, act_aggr,
                 attn, conv, conv_map, conv_a2e, conv_e2a, dst_dim, update,
                 self_transform):
        super().__init__()
        map_attn = attn in ("map", "all")
        act_attn = attn in ("act", "all")
        c_map = conv_map or conv
        c_a2e = conv_a2e or conv
        c_e2a = conv_e2a or conv
        self.entity_layers, self.action_rounds = entity_layers, action_rounds
        self.type_enc = TypeEncoders(hidden)
        self.rel_emb = nn.Embedding(S.N_RELATIONS, S.REL_DIM)
        self.race = nn.Embedding(S.RACE_VOCAB, S.RACE_DIM, max_norm=2.0)
        self.agent = nn.Embedding(S.AGENT_VOCAB, S.AGENT_DIM, max_norm=2.0)
        self.stance = nn.Embedding(S.STANCE_BUCKETS, S.STANCE_DIM, max_norm=2.0)
        self.subtype = nn.Embedding(S.SUBTYPE_BUCKETS, S.SUBTYPE_DIM, max_norm=2.0)
        self.atype = nn.Embedding(S.ATYPE_VOCAB, S.ATYPE_DIM, max_norm=2.0)
        self.term = nn.Embedding(S.TERM_VOCAB, S.TERM_DIM, max_norm=2.0)
        self.cat = nn.Embedding(S.CAT_VOCAB, S.CAT_DIM, max_norm=2.0)
        ident = (S.RACE_DIM + S.AGENT_DIM + S.STANCE_DIM + S.SUBTYPE_DIM
                 + S.ATYPE_DIM + S.TERM_DIM + S.CAT_DIM)
        self.ident = nn.Linear(ident, hidden)
        self.in_norm = TypeNorm(hidden)

        n_map = entity_layers + action_rounds
        self.map_conv = nn.ModuleList([RelConv(hidden, S.REL_DIM, map_aggr, map_attn, c_map, dst_dim)
                                       for _ in range(n_map)])
        self.map_norm = nn.ModuleList([TypeNorm(hidden) for _ in range(n_map)])
        self.a2e_conv = nn.ModuleList([RelConv(hidden, S.REL_DIM, act_aggr, act_attn,
                                               c_a2e, dst_dim)
                                       for _ in range(action_rounds)])
        self.a2e_norm = nn.ModuleList([TypeNorm(hidden) for _ in range(action_rounds)])
        self.a2e_gate = nn.Parameter(torch.zeros(action_rounds))
        self.e2a_conv = nn.ModuleList([RelConv(hidden, S.REL_DIM, act_aggr, act_attn,
                                               c_e2a, dst_dim)
                                       for _ in range(action_rounds)])
        self.e2a_norm = nn.ModuleList([TypeNorm(hidden) for _ in range(action_rounds)])
        self.drop = nn.Dropout(0.15)
        n_upd = n_map + 2 * action_rounds
        if update == "linear":
            self.upd = nn.ModuleList([nn.Linear(hidden * 2, hidden) for _ in range(n_upd)])
        elif update == "mlp":
            self.upd = nn.ModuleList([_mlp([hidden * 2, hidden * 2, hidden])
                                      for _ in range(n_upd)])
        else:
            self.upd = None
        self.self_lin = nn.Linear(hidden, hidden) if self_transform else None
        self.jk = nn.Linear(hidden * (1 + entity_layers + 2 * action_rounds), hidden)

    def _combine(self, h, m, nt, norm, ui, gate=None):
        m = norm(m, nt)
        if self.upd is not None:
            m = self.upd[ui](torch.cat([h, m], dim=-1))
        m = self.drop(torch.relu(m))
        if gate is not None:
            m = gate * m
        return (h if self.self_lin is None else self.self_lin(h)) + m

    def forward(self, data):
        nt = data.node_type
        h = torch.relu(self.type_enc(data.x, nt)) + self.ident(torch.cat([
            self.race(data.race_idx), self.agent(data.agent_idx),
            self.stance(data.stance_idx), self.subtype(data.subtype_idx),
            self.atype(data.atype_idx), self.term(data.term_idx),
            self.cat(data.cat_idx)], dim=1))
        h = self.in_norm(h, nt)
        states = [h]
        map_rel = self.rel_emb(data.edge_rel)
        a2e_rel = self.rel_emb(data.a2e_rel)
        e2a_rel = self.rel_emb(data.e2a_rel)

        n_map = self.entity_layers + self.action_rounds
        li = 0
        for _ in range(self.entity_layers):
            m = self.map_conv[li](h, data.edge_index, map_rel)
            h = self._combine(h, m, nt, self.map_norm[li], li)
            states.append(h)
            li += 1

        for r in range(self.action_rounds):
            m = self.a2e_conv[r](h, data.a2e_index, a2e_rel)
            h = self._combine(h, m, nt, self.a2e_norm[r], n_map + r,
                              gate=self.a2e_gate[r])
            m = self.map_conv[li](h, data.edge_index, map_rel)
            h = self._combine(h, m, nt, self.map_norm[li], li)
            states.append(h)
            li += 1
            m = self.e2a_conv[r](h, data.e2a_index, e2a_rel)
            h = self._combine(h, m, nt, self.e2a_norm[r],
                              n_map + self.action_rounds + r)
            states.append(h)

        h = self.jk(torch.cat(states, dim=1))
        batch = getattr(data, "batch", None)
        if batch is None:
            batch = data.x.new_zeros(data.x.size(0), dtype=torch.long)
        ent = (nt != S.ACTION_TYPE_INDEX).float().unsqueeze(1)
        n = max(1, int(data.n_actions.numel()))
        num = scatter(h * ent, batch, dim=0, dim_size=n, reduce="sum")
        den = scatter(ent, batch, dim=0, dim_size=n, reduce="sum").clamp(min=1.0)
        return h, num / den, batch


class Head(nn.Module):

    def __init__(self, hidden):
        super().__init__()
        self.q = _mlp([hidden, 128, 64, 1], dropout=0.1)
        self.v = _mlp([hidden + S.G_CTX_DIM, 64, 1], dropout=0.1)

    def forward(self, h, pool, data):
        q = self.q(h[data.action_index]).squeeze(-1)
        v = self.v(torch.cat([pool, data.g_ctx], dim=1)).squeeze(-1)
        return q, v


class Net(nn.Module):

    def __init__(self, hidden, entity_layers, action_rounds, map_aggr, act_aggr,
                 attn, conv, conv_map, conv_a2e, conv_e2a, dst_dim, update,
                 self_transform):
        super().__init__()
        self.encoder = Encoder(hidden, entity_layers, action_rounds,
                               map_aggr, act_aggr, attn, conv, conv_map, conv_a2e,
                               conv_e2a, dst_dim, update, self_transform)
        self.head = Head(hidden)

    def forward(self, data):
        h, pool, batch = self.encoder(data)
        q, v = self.head(h, pool, data)
        return {"h": h, "pool": pool, "q": q, "v": v,
                "action_graph": batch[data.action_index]}


def listwise_nll(q, a_graph, is_taken, n_graphs):
    p = softmax(q, a_graph, num_nodes=n_graphs)
    hit = scatter(p * is_taken, a_graph, dim=0, dim_size=n_graphs, reduce="sum")
    return -torch.log(hit.clamp(min=1e-9))


def from_cfg(cfg):
    cfg = cfg or {}
    missing = [k for k in NET_KEYS if k not in cfg]
    if missing:
        raise KeyError(
            "mapgraph.net.from_cfg: %s absent from cfg. Every net parameter comes from "
            "train.CFG and is stored in the model's meta.json, so a saved model carries "
            "the shape it was trained with. There is no default to fall back to -- a "
            "default here would silently build a different net than the weights expect."
            % ", ".join(missing))
    return Net(cfg["hidden"], cfg["entity_layers"], cfg["action_rounds"],
               map_aggr=cfg["map_aggr"], act_aggr=cfg["act_aggr"], attn=cfg["attn"],
               conv=cfg["conv"], conv_map=cfg["conv_map"], conv_a2e=cfg["conv_a2e"],
               conv_e2a=cfg["conv_e2a"], dst_dim=cfg["dst_dim"], update=cfg["update"],
               self_transform=cfg["self_transform"])
