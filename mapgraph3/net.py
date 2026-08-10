from __future__ import annotations

"""v3 network.

* Candidate actions are NODES, and `q = MLP(h_action)` reads that node alone after
  message passing. Nothing else is concatenated back in. If that is not enough to rank,
  message passing failed and I want to see it rather than hide it behind a pooled
  context vector the way v2 did.

* Layers 0..1 run on the ENTITY subgraph only. Action nodes join from layer 2. Without
  that gap the map embeddings depend on which offers the generator emitted, and the
  model can learn the offer generator instead of the world.

* Then alternating rounds, ASNets-style (Toyer et al. 2018): entities <- actions, then
  actions <- entities, ending on `actions <-`.

* No action<->action edges. Candidates meet through the entities and the candidate-group
  node they share.

* action -> entity is mean-aggregated and gated by a zero-initialised scalar. One
  campaign ego can carry 600+ candidates; summed, they bury its own state. Zero-init
  starts training at "actions do not perturb the map" and makes the channel earn itself.

* Per-node-type input encoders and per-node-type norm: ~75% of the node population is
  action nodes, so one shared norm would compute its statistics almost entirely off them.
"""

import torch
import torch.nn as nn
from torch_geometric.data import Data
from torch_geometric.nn import MessagePassing
from torch_geometric.utils import scatter, softmax

try:
    from mapgraph3 import schema as S
except ImportError:
    import schema as S

HIDDEN = 192
ENTITY_LAYERS = 2
ACTION_ROUNDS = 2


class DecisionGraph(Data):

    def __cat_dim__(self, key, value, *args, **kwargs):
        if key == "g_ctx":
            return 0
        return super().__cat_dim__(key, value, *args, **kwargs)


def to_data(g, y=None, taken=None):
    """Split the flat edge list into the three channels the encoder needs."""
    act = S.ACTION_TYPE_INDEX
    nt = g.node_type
    m_s, m_d, m_r = [], [], []
    a_s, a_d, a_r = [], [], []
    e_s, e_d, e_r = [], [], []
    for s, d, r in zip(g.src, g.dst, g.rel):
        s_act, d_act = nt[s] == act, nt[d] == act
        if not s_act and not d_act:
            m_s.append(s); m_d.append(d); m_r.append(r)
        elif s_act and not d_act:
            a_s.append(s); a_d.append(d); a_r.append(r)      # entities <- actions
        elif d_act and not s_act:
            e_s.append(s); e_d.append(d); e_r.append(r)      # actions  <- entities
        # action<->action edges are not built; if one appears it is dropped on purpose

    d = DecisionGraph(
        x=torch.tensor(g.x, dtype=torch.float32),
        edge_index=torch.tensor([m_s or [0], m_d or [0]], dtype=torch.long),
    )
    d.edge_rel = torch.tensor(m_r or [0], dtype=torch.long)
    d.a2e_index = torch.tensor([a_s or [0], a_d or [0]], dtype=torch.long)
    d.a2e_rel = torch.tensor(a_r or [0], dtype=torch.long)
    d.e2a_index = torch.tensor([e_s or [0], e_d or [0]], dtype=torch.long)
    d.e2a_rel = torch.tensor(e_r or [0], dtype=torch.long)
    d.node_type = torch.tensor(g.node_type, dtype=torch.long)
    d.race_idx = torch.tensor(g.race_idx, dtype=torch.long)
    d.agent_idx = torch.tensor(g.agent_idx, dtype=torch.long)
    d.stance_idx = torch.tensor(g.stance_idx, dtype=torch.long)
    d.subtype_idx = torch.tensor(g.subtype_idx, dtype=torch.long)
    d.atype_idx = torch.tensor(g.atype_idx, dtype=torch.long)
    d.term_idx = torch.tensor(g.term_idx, dtype=torch.long)
    d.cat_idx = torch.tensor(g.cat_idx, dtype=torch.long)
    d.g_ctx = torch.tensor([g.g_ctx], dtype=torch.float32)
    d.action_index = torch.tensor(g.action_nodes or [0], dtype=torch.long)
    d.n_actions = torch.tensor([len(g.action_nodes)], dtype=torch.long)
    if taken is not None:
        d.is_taken = torch.tensor(taken, dtype=torch.float32)
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
    """Message conditioned on both endpoints and the relation.

    GINE builds a message from the source node and the edge only, which cannot express
    "is this attacker stronger than this defender" -- that needs x_i and x_j together.
    Relation identity enters as an embedding rather than a weight matrix per relation:
    76 relations would otherwise mean 76 weight matrices.
    """

    def __init__(self, hidden, rel_dim, aggr):
        super().__init__(aggr=aggr)
        self.msg = _mlp([hidden * 2 + rel_dim, hidden * 2, hidden])

    def forward(self, x, edge_index, rel_emb):
        if edge_index.numel() == 0:
            return x.new_zeros(x.size())
        return self.propagate(edge_index, x=x, rel=rel_emb)

    def message(self, x_i, x_j, rel):
        return self.msg(torch.cat([x_i, x_j, rel], dim=-1))


class TypeEncoders(nn.Module):

    def __init__(self, hidden):
        super().__init__()
        self.hidden = hidden
        self.enc = nn.ModuleList(
            [nn.Linear(max(len(S.TYPE_FIELDS[t]), 1), hidden) for t in S.NODE_TYPES])

    def forward(self, x, node_type):
        h = x.new_zeros(x.size(0), self.hidden)
        for ti, t in enumerate(S.NODE_TYPES):
            sel = node_type == ti
            if not bool(sel.any()):
                continue
            h[sel] = self.enc[ti](x[sel, :max(len(S.TYPE_FIELDS[t]), 1)])
        return h


class TypeNorm(nn.Module):

    def __init__(self, hidden):
        super().__init__()
        self.norm = nn.ModuleList([nn.LayerNorm(hidden) for _ in S.NODE_TYPES])

    def forward(self, h, node_type):
        out = h.clone()
        for ti in range(len(S.NODE_TYPES)):
            sel = node_type == ti
            if bool(sel.any()):
                out[sel] = self.norm[ti](h[sel])
        return out


class Encoder(nn.Module):

    def __init__(self, hidden=HIDDEN, entity_layers=ENTITY_LAYERS,
                 action_rounds=ACTION_ROUNDS):
        super().__init__()
        self.entity_layers, self.action_rounds = entity_layers, action_rounds
        self.type_enc = TypeEncoders(hidden)
        self.rel_emb = nn.Embedding(S.N_RELATIONS, S.REL_DIM)
        # max_norm caps identity magnitude. Without it the catalogue table ran away to a
        # norm of ~1036 against ~20 for the conv weights, so node state was almost purely
        # "which item is this" and message passing -- normalised, therefore O(1) --
        # could not move the score. The map ablation caught it.
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
        # put identity and message passing on the same scale before the first round
        self.in_norm = TypeNorm(hidden)

        n_map = entity_layers + action_rounds
        self.map_conv = nn.ModuleList([RelConv(hidden, S.REL_DIM, "add")
                                       for _ in range(n_map)])
        self.map_norm = nn.ModuleList([TypeNorm(hidden) for _ in range(n_map)])
        self.a2e_conv = nn.ModuleList([RelConv(hidden, S.REL_DIM, "mean")
                                       for _ in range(action_rounds)])
        self.a2e_norm = nn.ModuleList([TypeNorm(hidden) for _ in range(action_rounds)])
        self.a2e_gate = nn.Parameter(torch.zeros(action_rounds))
        self.e2a_conv = nn.ModuleList([RelConv(hidden, S.REL_DIM, "mean")
                                       for _ in range(action_rounds)])
        self.e2a_norm = nn.ModuleList([TypeNorm(hidden) for _ in range(action_rounds)])
        self.drop = nn.Dropout(0.15)
        self.jk = nn.Linear(hidden * (1 + entity_layers + 2 * action_rounds), hidden)

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

        li = 0
        for _ in range(self.entity_layers):
            m = self.map_conv[li](h, data.edge_index, map_rel)
            h = h + self.drop(torch.relu(self.map_norm[li](m, nt)))
            states.append(h)
            li += 1

        for r in range(self.action_rounds):
            m = self.a2e_conv[r](h, data.a2e_index, a2e_rel)
            h = h + self.a2e_gate[r] * self.drop(torch.relu(self.a2e_norm[r](m, nt)))
            m = self.map_conv[li](h, data.edge_index, map_rel)
            h = h + self.drop(torch.relu(self.map_norm[li](m, nt)))
            states.append(h)
            li += 1
            m = self.e2a_conv[r](h, data.e2a_index, e2a_rel)
            h = h + self.drop(torch.relu(self.e2a_norm[r](m, nt)))
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

    def __init__(self, hidden=HIDDEN):
        super().__init__()
        self.q = _mlp([hidden, 128, 64, 1], dropout=0.1)
        self.v = _mlp([hidden + S.G_CTX_DIM, 64, 1], dropout=0.1)

    def forward(self, h, pool, data):
        q = self.q(h[data.action_index]).squeeze(-1)
        v = self.v(torch.cat([pool, data.g_ctx], dim=1)).squeeze(-1)
        return q, v


class Net(nn.Module):

    def __init__(self, hidden=HIDDEN, entity_layers=ENTITY_LAYERS,
                 action_rounds=ACTION_ROUNDS):
        super().__init__()
        self.encoder = Encoder(hidden, entity_layers, action_rounds)
        self.head = Head(hidden)

    def forward(self, data):
        h, pool, batch = self.encoder(data)
        q, v = self.head(h, pool, data)
        return {"h": h, "pool": pool, "q": q, "v": v,
                "action_graph": batch[data.action_index]}


def listwise_nll(q, a_graph, is_taken, n_graphs):
    """-log p(taken) under a per-decision softmax over that decision's candidate set."""
    p = softmax(q, a_graph, num_nodes=n_graphs)
    hit = scatter(p * is_taken, a_graph, dim=0, dim_size=n_graphs, reduce="sum")
    return -torch.log(hit.clamp(min=1e-9))
