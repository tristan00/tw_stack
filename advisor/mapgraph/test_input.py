import unittest
from unittest.mock import patch

import numpy as np
import torch
from torch_geometric.data import Batch

from advisor.mapgraph import net as N
from advisor.mapgraph import schema as S
from advisor.mapgraph import train as T


def reference_norm(datas, cap):
    x = torch.cat([d.x for d in datas])
    nt = torch.cat([d.node_type for d in datas])
    center = torch.zeros(len(S.NODE_TYPES), S.MAX_FIELDS)
    spread = torch.ones_like(center)
    fitted = 0
    for t in range(len(S.NODE_TYPES)):
        rows = x[nt == t]
        if rows.shape[0] < 8:
            continue
        for c in range(S.MAX_FIELDS):
            values = rows[:, c]
            if len(values) > cap:
                generator = torch.Generator().manual_seed(t * S.MAX_FIELDS + c)
                values = values[torch.randint(len(values), (cap,), generator=generator)]
            q = torch.quantile(values, torch.tensor([0.25, 0.5, 0.75]))
            iqr = float(q[2] - q[0])
            scale = iqr / 1.349 if iqr > 0 else float(values.std(unbiased=False))
            if scale <= 0:
                continue
            center[t, c] = q[1]
            spread[t, c] = scale
            fitted += 1
    return center, spread, fitted


class InputTests(unittest.TestCase):

    def setUp(self):
        generator = torch.Generator().manual_seed(91)
        self.datas = []
        for size in (13, 80, 173, 1024):
            types = torch.randint(len(S.NODE_TYPES), (size,), generator=generator)
            x = torch.randn(size, S.MAX_FIELDS, generator=generator)
            widths = torch.tensor([len(S.TYPE_FIELDS[name]) for name in S.NODE_TYPES])
            x[torch.arange(S.MAX_FIELDS)[None, :] >= widths[types, None]] = 0
            data = N.DecisionGraph(x=x, node_type=types, g_ctx=torch.zeros(1, S.G_CTX_DIM),
                                  n_actions=torch.tensor([2]), action_index=torch.tensor([0, size - 1]),
                                  y=torch.randn(1, generator=generator), is_taken=torch.tensor([1., 0.]))
            for name in ("edge", "a2e", "e2a"):
                data[name + "_index"] = torch.randint(size, (2, size * 2), generator=generator)
                data[name + "_rel"] = torch.zeros(size * 2, dtype=torch.long)
                data[name + "_val"] = torch.randn(size * 2, generator=generator)
                data[name + "_dir"] = torch.randn(size * 2, 2, generator=generator)
            self.datas.append(data)

    def test_normalization_matches_original_with_and_without_sampling(self):
        for cap in (2_000_000, 17):
            with self.subTest(cap=cap), patch.object(N, "_NORM_SAMPLE", cap):
                actual = N.norm_stats(self.datas)
                expected = reference_norm(self.datas, cap)
                self.assertEqual(actual[2], expected[2])
                torch.testing.assert_close(actual[0], expected[0], rtol=1e-6, atol=1e-7)
                torch.testing.assert_close(actual[1], expected[1], rtol=1e-6, atol=1e-7)

    def test_vectorized_batch_and_roundtrip_match_pyg(self):
        actual = T._batch(self.datas)
        expected = Batch.from_data_list(self.datas)
        self.assertEqual(set(actual.keys()), set(expected.keys()))
        for key in actual.keys():
            self.assertTrue(torch.equal(actual[key], expected[key]), key)
        for actual_data, original in zip(actual.to_data_list(), self.datas):
            for key in original.keys():
                self.assertTrue(torch.equal(actual_data[key], original[key]), key)

    def test_grouped_norm_constant_and_zero_iqr_fields(self):
        types = torch.tensor([0] * 32 + [1] * 8 + [2] * 7)
        x = torch.zeros(len(types), S.MAX_FIELDS)
        x[:32, 0] = 42
        x[31, 1] = 1000
        x[32:40, 0] = torch.arange(8)
        datas = [N.DecisionGraph(x=x, node_type=types)]
        actual, expected = N.norm_stats(datas), reference_norm(datas, N._NORM_SAMPLE)
        self.assertEqual(actual[2], expected[2])
        torch.testing.assert_close(actual[0], expected[0])
        torch.testing.assert_close(actual[1], expected[1])

    def test_vectorized_labels_match_tensor_arithmetic(self):
        ys = [float(d.y) for d in self.datas]
        mean, sd = 0.123456789, 0.987654321
        actual = (np.asarray(ys, dtype=np.float32) - np.float32(mean)) / np.float32(sd)
        expected = torch.cat([(d.y - mean) / sd for d in self.datas])
        self.assertTrue(torch.equal(torch.from_numpy(actual), expected))

    def test_contiguous_transport_preserves_every_tensor(self):
        import pickle
        from advisor.mapgraph.source import _pack, GraphView
        block = pickle.loads(pickle.dumps(_pack(self.datas), protocol=5))
        views = [GraphView(block, i) for i in range(len(self.datas))]
        for actual, original in zip(views, self.datas):
            for key in original.keys():
                self.assertTrue(torch.equal(actual[key], original[key]), key)
        actual_batch, expected_batch = T._batch(views), T._batch(self.datas)
        for key in actual_batch.keys():
            self.assertTrue(torch.equal(actual_batch[key], expected_batch[key]), key)
        actual_norm, expected_norm = N.norm_stats(views), N.norm_stats(self.datas)
        torch.testing.assert_close(actual_norm[0], expected_norm[0])
        torch.testing.assert_close(actual_norm[1], expected_norm[1])

    def test_consuming_partitions_preserves_shuffled_batches_and_releases_blocks(self):
        from advisor.mapgraph.source import _pack, GraphView
        for use_views in (False, True):
            originals = [d.clone() for d in self.datas]
            block = _pack(originals)
            datas = [GraphView(block, i) for i in range(len(originals))] if use_views else originals
            partitions = [[datas[3], datas[0], datas[2]], [datas[1]]]
            expected = [[T._batch(part[i:i + 2]) for i in range(0, len(part), 2)] for part in partitions]
            actual = T._collate_partitions(partitions, 2, torch.device("cpu"), lambda s: None)
            self.assertEqual(partitions, [[], []])
            if use_views:
                self.assertEqual(block, {})
            for observed, reference in zip(actual, expected):
                for batch, wanted in zip(observed, reference):
                    for key in wanted.keys():
                        self.assertTrue(torch.equal(batch[key], wanted[key]), key)
                    for row, wanted_row in zip(batch.to_data_list(), wanted.to_data_list()):
                        for key in wanted_row.keys():
                            self.assertTrue(torch.equal(row[key], wanted_row[key]), key)


if __name__ == "__main__":
    unittest.main()
