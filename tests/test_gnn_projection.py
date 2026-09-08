import time

import pytest
import torch

from advisor.mapgraph import train as T, build as B, net as N, graph_bench
from advisor.mapgraph.projection import Projection
from decisions.store import DecisionStore

pytestmark = pytest.mark.skip("not part of the suite")


def test_projection_matches_gameplay_hydration():
    reference, reference_net = B, N
    source = T.load_walk_source(window=2000, limit=100)
    for run_dir, heads in source["records"].jobs:
        st = DecisionStore(run_dir, readonly=True)
        try:
            projection = Projection(st.con)
            for offset in range(0, len(heads), 100):
                chunk = heads[offset:offset + 100]
                t = time.perf_counter()
                new, seconds = projection.read(chunk)
                print("PROJECTED", len(new), seconds, flush=True)
                for rows in st.hydrate_decisions(chunk):
                    for old, taken in rows:
                        direct = new[old["decision_id"]]
                        for name, cfg in graph_bench.configs().items():
                            a, b = reference.build_graph(old, cfg), B.build_graph(direct, cfg)
                            if a is None or b is None:
                                assert a is b
                                continue
                            if a.node_ids != b.node_ids:
                                print("NODE MISMATCH", old["decision_id"], name, len(a.node_ids), len(b.node_ids), flush=True)
                                print([(i,x,y) for i,(x,y) in enumerate(zip(a.node_ids,b.node_ids)) if x!=y][:5], flush=True)
                                raise AssertionError("nodes")
                            assert a.action_keys == b.action_keys
                            assert a.provenance == b.provenance
                            da, db = reference_net.to_data(a), N.to_data(b)
                            for key, value in da.to_dict().items():
                                if not torch.equal(value, db[key]):
                                    print("MISMATCH", old["decision_id"], name, key, flush=True)
                                    if key.endswith("index"):
                                        print(value.shape, db[key].shape, flush=True)
                                        ea = list(zip(a.src, a.dst, a.rel))
                                        eb = list(zip(b.src, b.dst, b.rel))
                                        print("ONLY OLD", [(a.node_ids[i], a.node_ids[j], r) for i,j,r in set(ea)-set(eb)][:10], flush=True)
                                        print("ONLY NEW", [(b.node_ids[i], b.node_ids[j], r) for i,j,r in set(eb)-set(ea)][:10], flush=True)
                                    if key == "x":
                                        for i in torch.where((value != db[key]).any(dim=1))[0].tolist()[:8]:
                                            print(a.node_ids[i], value[i].tolist(), db[key][i].tolist(), flush=True)
                                    raise AssertionError(key)
                print("PASS", offset + len(chunk), "seconds", time.perf_counter() - t, flush=True)
        finally:
            st.close()
