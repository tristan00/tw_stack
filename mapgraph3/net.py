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

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
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


_IDX_FIELDS = ("node_type", "race_idx", "agent_idx", "stance_idx", "subtype_idx",
               "atype_idx", "term_idx", "cat_idx")


def to_data(g, y=None, taken=None):
    """Split the flat edge list into the three channels the encoder needs.

    The split is three boolean masks over the edge arrays, not a python loop over every
    edge. That loop ran ~61.6M iterations across a corpus walk. Boolean indexing keeps
    order, so each channel is element-for-element what the loop emitted -- including the
    [[0],[0]] rel-0 placeholder an empty channel gets.
    """
    act = S.ACTION_TYPE_INDEX
    ne, nn_ = len(g.src), len(g.x)
    src = np.fromiter(g.src, dtype=np.int64, count=ne)
    dst = np.fromiter(g.dst, dtype=np.int64, count=ne)
    rel = np.fromiter(g.rel, dtype=np.int64, count=ne)
    ntype = np.fromiter(g.node_type, dtype=np.int64, count=nn_)

    is_act = ntype == act
    s_act, d_act = is_act[src], is_act[dst]
    chan = []
    #        map                a2e: entities <- actions   e2a: actions <- entities
    # action<->action edges are not built; s_act & d_act is dropped on purpose
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
    """Message conditioned on both endpoints and the relation.

    GINE builds a message from the source node and the edge only, which cannot express
    "is this attacker stronger than this defender" -- that needs x_i and x_j together.
    Relation identity enters as an embedding rather than a weight matrix per relation:
    76 relations would otherwise mean 76 weight matrices.

    A cheaper form was tried and rejected: hoisting the source projection out of the edge
    loop and running the MLP per node on cat([x_i, aggregated]), with the relation added
    per edge. It is not worth it and it is not equivalent.
      - The saving is 1.50x on conv MACs, not the 4.4x it claimed -- that number assumed
        all 8 conv applications traverse all 8.5k edges, but each sees only its own
        channel (21.8k edge-visits total, not 68k). Arithmetic is ~1% of this model's
        wall clock, so 1.5x of it buys nothing.
      - sum_j (W_s x_j + W_r r_ij) = W_s sum_j x_j + W_r sum_j r_ij. The two sums
        decouple exactly, so which relation attached to which neighbour is unrecoverable
        at any depth: "besieging settlement S while garrisoned in T" becomes
        indistinguishable from the swap. With 46 relation types carrying the semantics
        this schema was restructured around, that is most of what the model is for.
      - Returning upd(cat([x, m])) rather than a pure message also breaks the zero-init
        a2e gate below: a node with no incoming action edge would get a nonzero function
        of its own state instead of 0.
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


_NT = len(S.NODE_TYPES)


class TypeEncoders(nn.Module):
    """Per-node-type input projection, as ONE block-diagonal matmul.

    Semantically identical to a Linear per node type: each node's own fields are
    scattered into its type's slot of a [N, n_types * MAX_FIELDS] row and projected by a
    single weight whose blocks are exactly those per-type weights. Written as a Python
    loop over 19 types it cost more in kernel launches than the entire message passing --
    most types hold single-digit node counts, so it was 19 tiny gather/scatter pairs per
    call to do a few thousand FLOPs.
    """

    def __init__(self, hidden):
        super().__init__()
        self.hidden = hidden
        self.lin = nn.Linear(_NT * S.MAX_FIELDS, hidden, bias=False)
        self.bias = nn.Parameter(torch.zeros(_NT, hidden))
        self.register_buffer("_span", torch.arange(S.MAX_FIELDS), persistent=False)

    def forward(self, x, node_type):
        cols = node_type.unsqueeze(1) * S.MAX_FIELDS + self._span
        big = x.new_zeros(x.size(0), _NT * S.MAX_FIELDS).scatter_(1, cols, x)
        # F.embedding, not self.bias[node_type]: same lookup, but the backward of plain
        # advanced indexing is an atomic scatter from every node row into only 19 rows,
        # and that contention was 90% of total training time. F.embedding's dense backward
        # sorts the indices and segment-reduces instead. Identical maths, ~50x cheaper.
        return self.lin(big) + F.embedding(node_type, self.bias)


class TypeNorm(nn.Module):
    """LayerNorm with per-node-type affine parameters, fused.

    nn.LayerNorm takes its statistics over the feature dimension of each row
    independently, so a separate LayerNorm per node type differs from a shared one ONLY
    in the affine parameters -- the normalisation itself is per-row either way. So this
    is mathematically the same thing the 19-way loop computed, in two kernels instead of
    roughly seventy-six, and without the full-width h.clone() it did on every call.
    """

    def __init__(self, hidden, eps=1e-5):
        super().__init__()
        self.eps = eps
        self.hidden = hidden
        # weight and bias live in ONE table so a node costs one lookup, not two.
        self.affine = nn.Parameter(torch.cat(
            [torch.ones(_NT, hidden), torch.zeros(_NT, hidden)], dim=1))

    def forward(self, h, node_type):
        mu = h.mean(dim=-1, keepdim=True)
        var = h.var(dim=-1, unbiased=False, keepdim=True)
        x = (h - mu) * torch.rsqrt(var + self.eps)
        # F.embedding rather than self.affine[node_type]. Plain advanced indexing
        # backpropagates as an atomic scatter from every node row into only 19 rows;
        # profiling showed those scatters were 90% of ALL training gpu time. F.embedding
        # sorts indices and segment-reduces instead -- same values, ~50x cheaper.
        w, b = F.embedding(node_type, self.affine).split(self.hidden, dim=1)
        return x * w + b


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
