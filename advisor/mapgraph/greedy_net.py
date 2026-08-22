from __future__ import annotations


import os
import sys

import torch
import torch.nn as nn

from advisor.mapgraph import net as N
from advisor.mapgraph import schema as S


class RewardHead(nn.Module):

    def __init__(self, hidden):
        super().__init__()
        self.q = N._mlp([hidden * 2 + S.G_CTX_DIM, 128, 64, 1], dropout=0.1)

    def forward(self, h, pool, data, action_graph):
        g = data.g_ctx
        ctx = torch.cat([pool, torch.sign(g) * torch.log1p(g.abs())], dim=1)[action_graph]
        return self.q(torch.cat([h[data.action_index], ctx], dim=-1)).squeeze(-1)


class GreedyNet(nn.Module):

    def __init__(self, hidden, entity_layers, action_rounds, map_aggr, act_aggr,
                 attn, conv, conv_map, conv_a2e, conv_e2a, dst_dim, update,
                 self_transform, dropout):
        super().__init__()
        self.encoder = N.Encoder(hidden, entity_layers, action_rounds,
                                 map_aggr, act_aggr, attn, conv, conv_map, conv_a2e,
                                 conv_e2a, dst_dim, update, self_transform,
                                 dropout=dropout)
        self.head = RewardHead(hidden)

    def forward(self, data):
        h, pool, batch = self.encoder(data)
        ag = batch[data.action_index]
        return {"q": self.head(h, pool, data, ag), "action_graph": ag}


def taken_q(q, action_graph, is_taken, n_graphs):
    from torch_geometric.utils import scatter
    return scatter(q * is_taken, action_graph, dim=0, dim_size=n_graphs, reduce="sum")


def from_cfg(cfg):
    cfg = cfg or {}
    missing = [k for k in N.NET_KEYS if k not in cfg]
    if missing:
        raise KeyError(
            "mapgraph.greedy_net.from_cfg: %s absent from cfg. Every net parameter comes "
            "from greedy_train.CFG and is stored in the model's meta.json; there is no "
            "default to fall back to." % ", ".join(missing))
    return GreedyNet(cfg["hidden"], cfg["entity_layers"], cfg["action_rounds"],
                     map_aggr=cfg["map_aggr"], act_aggr=cfg["act_aggr"], attn=cfg["attn"],
                     conv=cfg["conv"], conv_map=cfg["conv_map"], conv_a2e=cfg["conv_a2e"],
                     conv_e2a=cfg["conv_e2a"], dst_dim=cfg["dst_dim"],
                     update=cfg["update"], self_transform=cfg["self_transform"],
                     dropout=cfg["dropout"])


def load(model_dir, tag):
    model_path = os.path.join(model_dir, "model.pt")
    if not os.path.exists(model_path):
        return None, None
    blob = torch.load(model_path, map_location="cpu")
    meta = blob["meta"]
    if meta.get("schema_hash") != S.schema_hash():
        sys.stderr.write(
            "%s: meta schema hash %s != code %s -- trained on a different graph; "
            "unready until retrain\n"
            % (tag, str(meta.get("schema_hash"))[:12], S.schema_hash()[:12]))
        return None, meta
    net = from_cfg(meta.get("cfg") or {})
    net.encoder.load_state_dict(blob["encoder"])
    net.head.load_state_dict(blob["head"])
    net.eval()
    return net, meta


def reward_of(meta, q):
    return [float(v) * float(meta["y_sd"]) + float(meta["y_mean"]) for v in q]
