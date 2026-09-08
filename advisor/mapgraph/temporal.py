import torch
from torch import nn


class TemporalReward(nn.Module):

    def __init__(self, embedding_dim, hidden, kind, layers, dropout, history_length):
        super().__init__()
        self.kind = kind
        self.project = nn.Sequential(nn.Linear(embedding_dim, hidden), nn.LayerNorm(hidden), nn.SiLU())
        if kind in ('gru', 'lstm'):
            recurrent = nn.GRU if kind == 'gru' else nn.LSTM
            self.temporal = recurrent(hidden, hidden, layers, batch_first=True,
                                      dropout=dropout if layers > 1 else 0)
        elif kind == 'transformer':
            layer = nn.TransformerEncoderLayer(hidden, 4, hidden * 4, dropout,
                                                batch_first=True, norm_first=True)
            self.temporal = nn.TransformerEncoder(layer, layers, enable_nested_tensor=False)
            self.position = nn.Parameter(torch.randn(1, history_length + 1, hidden) * 0.02)
        else:
            raise ValueError(kind)
        self.head = nn.Sequential(nn.Linear(hidden * 2, hidden),
                                  nn.SiLU(), nn.Dropout(dropout), nn.Linear(hidden, 1))

    def forward(self, current, past, lengths):
        action = self.project(current)
        encoded = self.project(past)
        if self.kind == 'transformer':
            tokens = torch.cat((encoded, action.unsqueeze(1)), dim=1) + self.position
            padding = torch.arange(past.shape[1], device=lengths.device)[None, :] >= lengths[:, None]
            padding = torch.cat((padding, torch.zeros(len(lengths), 1, device=lengths.device, dtype=torch.bool)), dim=1)
            context = self.temporal(tokens, src_key_padding_mask=padding)[:, -1]
        else:
            packed = nn.utils.rnn.pack_padded_sequence(
                encoded, lengths.clamp(min=1).cpu(), batch_first=True, enforce_sorted=False)
            _, state = self.temporal(packed)
            if self.kind == 'lstm':
                state = state[0]
            context = state[-1] * (lengths > 0).unsqueeze(-1)
        return self.head(torch.cat((action, context), dim=-1)).squeeze(-1)
