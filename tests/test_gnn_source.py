import pytest
import torch

from advisor.mapgraph import train as T

pytestmark = pytest.mark.skip("not part of the suite")


def test_serial_and_parallel_graphs_match():
    source = T.load_walk_source(window=2000, limit=600)
    serial = T.walk_source(source, workers=1)
    parallel = T.walk_source(source, workers=2)
    assert serial["tally"] == parallel["tally"]
    assert len(serial["examples"]) == len(parallel["examples"])
    for left, right in zip(serial["examples"], parallel["examples"]):
        for key in ("y", "gain", "campaign_id", "counts"):
            assert left[key] == right[key], key
        for key, value in left["data"].to_dict().items():
            assert torch.equal(value, right["data"][key]), key
