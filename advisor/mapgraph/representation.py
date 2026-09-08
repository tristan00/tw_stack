import copy
import math

import torch
from torch import nn
from torch.nn import functional as F
from torch_geometric.utils import scatter, softmax

from advisor.mapgraph import net as N, schema as S

IDENTITIES = ('race_idx', 'agent_idx', 'stance_idx', 'subtype_idx', 'atype_idx', 'term_idx', 'cat_idx')
VOCABS = (S.RACE_VOCAB, S.AGENT_VOCAB, S.STANCE_BUCKETS, S.SUBTYPE_BUCKETS, S.ATYPE_VOCAB, S.TERM_VOCAB, S.CAT_VOCAB)
BITS = tuple(max(1, math.ceil(math.log2(v))) for v in VOCABS)


def positional(index, size):
    phase = (index.float() + .5) / size.float().clamp(min=1)
    frequencies = 2. ** torch.arange(8, device=index.device)
    angles = phase[:, None] * frequencies[None, :] * math.pi
    return torch.cat((angles.sin(), angles.cos()), 1)


def corrupt(data, rate):
    view = copy.copy(data)
    if rate:
        view.x = data.x * (torch.rand_like(data.x) >= rate)
        for key in IDENTITIES:
            original = getattr(data, key)
            setattr(view, key, original * (torch.rand(original.shape, device=original.device) >= rate))
        view.g_ctx = data.g_ctx * (torch.rand_like(data.g_ctx) >= rate)
    return view


def sample_candidate(data):
    view = copy.copy(data)
    offsets = torch.cumsum(data.n_actions, 0) - data.n_actions
    selected = offsets + (torch.rand(len(offsets), device=offsets.device) * data.n_actions).long()
    view.is_taken = torch.zeros_like(data.is_taken)
    view.is_taken[selected] = 1
    alternative = offsets + ((selected - offsets + 1 +
        (torch.rand(len(offsets), device=offsets.device) * (data.n_actions - 1).clamp(min=1)).long()) % data.n_actions)
    return view, selected, alternative


class GraphRepresentation(nn.Module):

    def __init__(self, config):
        super().__init__()
        self.encoder = N.Encoder(**{k: config[k] for k in N.NET_KEYS})
        width = config['hidden']
        latent, hidden = config['latent'], config['representation_hidden']
        self.bottleneck = nn.Sequential(nn.Linear(width * 2 + S.G_CTX_DIM, hidden), nn.SiLU(), nn.Linear(hidden, latent), nn.LayerNorm(latent))
        if config['representation_objective'] in ('contrastive', 'hybrid'):
            self.projector = nn.Sequential(nn.Linear(latent, hidden), nn.SiLU(), nn.Linear(hidden, latent))
        if config['representation_objective'] == 'contrastive':
            return
        self.node_decoder = nn.Sequential(nn.Linear(latent + 16, hidden), nn.SiLU(),
            nn.Linear(hidden, S.MAX_FIELDS + len(S.NODE_TYPES) + sum(BITS) + 1))
        self.relation_query = nn.Embedding(S.N_RELATIONS, 16)
        self.edge_decoder = nn.Sequential(nn.Linear(latent + 48, hidden), nn.SiLU(), nn.Linear(hidden, 4))
        self.global_decoder = nn.Sequential(nn.Linear(latent, hidden), nn.SiLU(), nn.Linear(hidden, S.G_CTX_DIM + 2))
        fields = torch.zeros(len(S.NODE_TYPES), S.MAX_FIELDS)
        for i, name in enumerate(S.NODE_TYPES):
            fields[i, :len(S.TYPE_FIELDS[name])] = 1
        self.register_buffer('field_mask', fields)

    def encode(self, data):
        actions, groups = self.encode_all(data)
        return scatter(actions * data.is_taken[:, None], groups, dim=0, dim_size=len(data.n_actions), reduce='sum')

    def encode_all(self, data):
        h, _, membership = self.encoder(data)
        count = len(data.n_actions)
        pool = scatter(h, membership, dim=0, dim_size=count, reduce='mean')
        actions = h[data.action_index]
        action_groups = membership[data.action_index]
        context = torch.sign(data.g_ctx) * torch.log1p(data.g_ctx.abs())
        return self.bottleneck(torch.cat((actions, pool[action_groups], context[action_groups]), 1)), action_groups

    def reconstruction(self, z, data):
        group = data.batch
        n = len(data.n_actions)
        counts = data.ptr[1:] - data.ptr[:-1]
        local = torch.arange(len(group), device=z.device) - data.ptr[group]
        positions = positional(local, counts[group])
        decoded = self.node_decoder(torch.cat((z[group], positions), 1)).float()
        center = self.encoder.type_enc.center[data.node_type]
        spread = self.encoder.type_enc.spread[data.node_type]
        scalars = torch.asinh((data.x - center) / spread)
        field_mask = self.field_mask[data.node_type]
        numeric = ((decoded[:, :S.MAX_FIELDS] - scalars).square() * field_mask).sum(1) / field_mask.sum(1).clamp(min=1)
        cursor = S.MAX_FIELDS
        node_type = F.cross_entropy(decoded[:, cursor:cursor + len(S.NODE_TYPES)], data.node_type, reduction='none')
        cursor += len(S.NODE_TYPES)
        identity = torch.zeros_like(numeric)
        for key, bits in zip(IDENTITIES, BITS):
            values = getattr(data, key)
            targets = ((values[:, None] >> torch.arange(bits, device=z.device)) & 1).float()
            identity += F.binary_cross_entropy_with_logits(decoded[:, cursor:cursor + bits], targets, reduction='none').mean(1) / len(BITS)
            cursor += bits
        parts = {key: scatter(values, group, dim=0, dim_size=n, reduce='mean').mean()
                 for key, values in [('scalar', numeric), ('node_type', node_type), ('identity', identity)]}
        candidate_nodes = data.action_index[data.is_taken.bool()]
        candidate_probability = softmax(decoded[:, -1], group, num_nodes=n)
        parts['candidate'] = -candidate_probability[candidate_nodes].clamp(min=1e-12).log().mean()
        indices = torch.cat([getattr(data, p + '_index') for p in ('edge', 'a2e', 'e2a')], 1)
        relations = torch.cat([getattr(data, p + '_rel') for p in ('edge', 'a2e', 'e2a')])
        values = torch.cat([getattr(data, p + '_val') for p in ('edge', 'a2e', 'e2a')])
        directions = torch.cat([getattr(data, p + '_dir') for p in ('edge', 'a2e', 'e2a')])
        src, dst = indices
        eg = group[src]
        random_dst = data.ptr[eg] + (torch.rand(len(src), device=z.device) * counts[eg]).long()
        width = len(group)
        existing = torch.sort((src * width + dst) * S.N_RELATIONS + relations).values
        negatives = (src * width + random_dst) * S.N_RELATIONS + relations
        places = torch.searchsorted(existing, negatives).clamp(max=max(0, len(existing) - 1))
        keep = (existing[places] != negatives) & (src != random_dst)
        negsrc, negdst = src[keep], random_dst[keep]
        allsrc, alldst = torch.cat((src, negsrc)), torch.cat((dst, negdst))
        allgroup = group[allsrc]
        query = self.relation_query(torch.cat((relations, relations[keep])))
        edge_output = self.edge_decoder(torch.cat((z[allgroup], positions[allsrc], positions[alldst], query), 1)).float()
        targets = torch.cat((torch.ones(len(src), device=z.device), torch.zeros(len(negsrc), device=z.device)))
        topology = F.binary_cross_entropy_with_logits(edge_output[:, 0], targets, reduction='none')
        parts['topology'] = scatter(topology, allgroup, dim=0, dim_size=n, reduce='mean').mean()
        edge_target = torch.cat((torch.asinh(values / 50)[:, None], directions), 1)
        attribute = (edge_output[:len(src), 1:] - edge_target).square().mean(1)
        parts['edge_attributes'] = scatter(attribute, eg, dim=0, dim_size=n, reduce='mean').mean()
        edge_counts = scatter(torch.ones_like(eg, dtype=z.dtype), eg, dim=0, dim_size=n, reduce='sum')
        global_target = torch.cat((torch.sign(data.g_ctx) * torch.log1p(data.g_ctx.abs()),
            torch.log1p(counts.float())[:, None], torch.log1p(edge_counts)[:, None]), 1)
        parts['global'] = (self.global_decoder(z).float() - global_target).square().mean()
        return sum(parts.values()) / len(parts), parts

    def contrastive(self, left, right, alternative=None, available=None):
        a = F.normalize(self.projector(left).float(), dim=1)
        b = F.normalize(self.projector(right).float(), dim=1)
        scores = a @ b.T / .2
        labels = torch.arange(len(a), device=a.device)
        forward, backward = scores, scores.T
        if alternative is not None:
            hard = F.normalize(self.projector(alternative).float(), dim=1)
            left_negative = ((a * hard).sum(1) / .2).masked_fill(~available, -1e9)
            right_negative = ((b * hard).sum(1) / .2).masked_fill(~available, -1e9)
            forward = torch.cat((scores, left_negative[:, None]), 1)
            backward = torch.cat((scores.T, right_negative[:, None]), 1)
        loss = (F.cross_entropy(forward, labels) + F.cross_entropy(backward, labels)) / 2
        accuracy = (forward.argmax(1) == labels).float().mean()
        return loss, accuracy
