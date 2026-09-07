import os
import unittest

import torch

from advisor.mapgraph import train as T


@unittest.skipUnless(os.environ.get("TW_RUN_INTEGRATION") == "1", "requires TW_RUN_INTEGRATION=1 and the database")
class GraphSourceTests(unittest.TestCase):
    def test_serial_and_parallel_graphs_match(self):
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
        print("PASS: serial and worker graphs, memory, labels, order, and tallies")
