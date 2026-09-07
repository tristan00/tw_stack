import os
import unittest

import torch

from advisor.mapgraph import train as T
from tests.adhoc import test_gnn_input as inputs


@unittest.skipUnless(os.environ.get("TW_RUN_GPU_TESTS") == "1" and torch.cuda.is_available(),
                     "requires TW_RUN_GPU_TESTS=1 and CUDA")
class GraphGpuTests(unittest.TestCase):
    setUp = inputs.InputTests.setUp

    def test_gpu_graph_fields_preserve_batches_and_release_source_storage(self):
        from advisor.mapgraph.source import _pack, GraphView
        block = _pack(self.datas)
        views = [GraphView(block, i) for i in range(len(self.datas))]
        partitions = [[views[3], views[0], views[2]], [views[1]]]
        expected = [[T._batch(part[i:i + 2]) for i in range(0, len(part), 2)] for part in partitions]
        for key, (value, offsets, dim) in block.items():
            if key not in ("x", "node_type"):
                block[key] = torch.as_tensor(value, device="cuda"), offsets, dim
        actual = T._collate_partitions(partitions, 2, torch.device("cuda"), lambda s: None)
        self.assertEqual(block, {})
        for observed, reference in zip(actual, expected):
            for batch, wanted in zip(observed, reference):
                for key in wanted.keys():
                    self.assertTrue(torch.equal(batch[key].cpu(), wanted[key]), key)
