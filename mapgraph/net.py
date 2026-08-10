from __future__ import annotations

import torch
import torch.nn as nn
from torch_geometric.data import Data
from torch_geometric.nn import GINEConv, GraphNorm
from torch_geometric.utils import scatter

try:
    from mapgraph import schema as S
except ImportError:
    import schema as S

HIDDEN = 64


class DecisionGraph(Data):

    def __inc__(self, key, value, *args, **kwargs):
        if key in ("offer_ego", "offer_target"):
            return self.num_nodes
        return super().__inc__(key, value, *args, **kwargs)

    def __cat_dim__(self, key, value, *args, **kwargs):
        if key in ("g_ctx",):
            return 0
        return super().__cat_dim__(key, value, *args, **kwargs)


def to_data(g, offers, y=None, node_y=None, node_y_mask=None):
    d = DecisionGraph(
        x=torch.tensor(g.x, dtype=torch.float32),
        edge_index=torch.tensor([g.edge_src, g.edge_dst], dtype=torch.long),
        edge_attr=torch.tensor(g.edge_attr, dtype=torch.float32),
    )
    d.race_idx = torch.tensor(g.race_idx, dtype=torch.long)
    d.stance_idx = torch.tensor(g.stance_idx, dtype=torch.long)
    d.own_mask = torch.tensor(g.own_mask, dtype=torch.float32)
    d.g_ctx = torch.tensor([g.g_ctx], dtype=torch.float32)
    d.offer_ego = torch.tensor([o["ego"] for o in offers], dtype=torch.long)
    d.offer_target = torch.tensor([o["target"] for o in offers], dtype=torch.long)
    d.offer_has_target = torch.tensor([o["has_target"] for o in offers], dtype=torch.float32)
    d.offer_atype = torch.tensor([o["atype"] for o in offers], dtype=torch.long)
    d.offer_key = torch.tensor([o["key_hash"] for o in offers], dtype=torch.long)
    d.offer_feats = torch.tensor([o["feats"] for o in offers], dtype=torch.float32)
    d.n_offers_t = torch.tensor([len(offers)], dtype=torch.long)
    if y is not None:
        d.y = torch.tensor([float(y)], dtype=torch.float32)
    if node_y is not None:
        d.node_y = torch.tensor(node_y, dtype=torch.float32)
        d.node_y_mask = torch.tensor(node_y_mask, dtype=torch.float32)
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


class Encoder(nn.Module):

    def __init__(self, hidden=HIDDEN):
        super().__init__()
        self.race_emb = nn.Embedding(S.RACE_VOCAB, S.RACE_DIM)
        self.stance_emb = nn.Embedding(S.STANCE_BUCKETS, S.STANCE_DIM)
        in_dim = S.NODE_DIM + S.RACE_DIM + S.STANCE_DIM
        self.inp = nn.Linear(in_dim, hidden)
        self.conv1 = GINEConv(_mlp([hidden, hidden * 2, hidden]), edge_dim=S.EDGE_DIM)
        self.norm1 = GraphNorm(hidden)
        self.conv2 = GINEConv(_mlp([hidden, hidden * 2, hidden]), edge_dim=S.EDGE_DIM)
        self.norm2 = GraphNorm(hidden)
        self.conv3 = GINEConv(_mlp([hidden, hidden * 2, hidden]), edge_dim=S.EDGE_DIM)
        self.norm3 = GraphNorm(hidden)
        self.drop = nn.Dropout(0.2)

    def forward(self, data):
        batch = getattr(data, "batch", None)
        if batch is None:
            batch = data.x.new_zeros(data.x.size(0), dtype=torch.long)
        x = torch.cat([data.x, self.race_emb(data.race_idx),
                       self.stance_emb(data.stance_idx)], dim=1)
        h = torch.relu(self.inp(x))
        d = self.drop(torch.relu(self.norm1(
            self.conv1(h, data.edge_index, data.edge_attr), batch)))
        h = h + d
        d = self.drop(torch.relu(self.norm2(
            self.conv2(h, data.edge_index, data.edge_attr), batch)))
        h = h + d
        d = self.drop(torch.relu(self.norm3(
            self.conv3(h, data.edge_index, data.edge_attr), batch)))
        h = h + d
        w = data.own_mask.unsqueeze(1)
        # graph count from a CPU-side int: batch.max().item() would sync the GPU every forward
        n_graphs = max(1, int(data.n_offers_t.numel()))
        pool_num = scatter(h * w, batch, dim=0, dim_size=n_graphs, reduce="sum")
        pool_den = scatter(w, batch, dim=0, dim_size=n_graphs, reduce="sum").clamp(min=1.0)
        return h, pool_num / pool_den, batch


class Head(nn.Module):

    def __init__(self, hidden=HIDDEN):
        super().__init__()
        self.atype_emb = nn.Embedding(S.ATYPE_VOCAB, S.ATYPE_DIM)
        self.key_emb = nn.Embedding(S.KEY_BUCKETS, S.KEY_DIM)
        q_in = hidden * 3 + S.G_CTX_DIM + S.ATYPE_DIM + S.KEY_DIM + S.OFFER_DIM
        self.q = _mlp([q_in, 128, 64, 1], dropout=0.3)
        self.v = _mlp([hidden * 2 + S.G_CTX_DIM, 64, 1], dropout=0.3)
        self.node_aux = _mlp([hidden, 32, 1])

    def forward(self, h, pool, batch, data):
        offer_graph = torch.repeat_interleave(
            torch.arange(data.n_offers_t.numel(), device=h.device), data.n_offers_t)
        h_ego = h[data.offer_ego]
        h_tgt = h[data.offer_target] * data.offer_has_target.unsqueeze(1)
        g_pool = pool[offer_graph]
        g_ctx = data.g_ctx[offer_graph]
        q = self.q(torch.cat([h_ego, h_tgt, g_pool, g_ctx,
                              self.atype_emb(data.offer_atype),
                              self.key_emb(data.offer_key),
                              data.offer_feats], dim=1)).squeeze(-1)
        v = self.v(torch.cat([h_ego, g_pool, g_ctx], dim=1)).squeeze(-1)
        return q, v, offer_graph

    def node_scores(self, h):
        return torch.sigmoid(self.node_aux(h)).squeeze(-1)


class Net(nn.Module):

    def __init__(self, hidden=HIDDEN):
        super().__init__()
        self.encoder = Encoder(hidden)
        self.head = Head(hidden)

    def forward(self, data):
        h, pool, batch = self.encoder(data)
        q, v, offer_graph = self.head(h, pool, batch, data)
        return {"h": h, "pool": pool, "q": q, "v": v, "offer_graph": offer_graph}
