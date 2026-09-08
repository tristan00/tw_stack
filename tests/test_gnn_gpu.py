import pytest
import torch

from advisor.mapgraph import train as T
from tests.test_gnn_input import make_datas

pytestmark = pytest.mark.skip("not part of the suite")


@pytest.mark.skipif(not torch.cuda.is_available(), reason="requires CUDA")
def test_gpu_graph_fields_preserve_batches_and_release_source_storage():
    from advisor.mapgraph.source import _pack, GraphView
    datas = make_datas()
    block = _pack(datas)
    views = [GraphView(block, i) for i in range(len(datas))]
    partitions = [[views[3], views[0], views[2]], [views[1]]]
    expected = [[T._batch(part[i:i + 2]) for i in range(0, len(part), 2)] for part in partitions]
    for key, (value, offsets, dim) in block.items():
        if key not in ("x", "node_type"):
            block[key] = torch.as_tensor(value, device="cuda"), offsets, dim
    actual = T._collate_partitions(partitions, 2, torch.device("cuda"), lambda s: None)
    assert block == {}
    for observed, reference in zip(actual, expected):
        for batch, wanted in zip(observed, reference):
            for key in wanted.keys():
                assert torch.equal(batch[key].cpu(), wanted[key]), key
