import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisor.mapgraph import train as T
from advisor.mapgraph import build as B
from advisor.mapgraph import net as N
from advisor.mapgraph import graph_bench
from advisor.mapgraph.projection import Projection
from decisions.store import DecisionStore
import torch


def main():
    reference = B
    reference_net = N
    if "--reference-build" in sys.argv:
        import importlib.util
        path = sys.argv[sys.argv.index("--reference-build") + 1]
        spec = importlib.util.spec_from_file_location("reference_build", path)
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
    if "--reference-net" in sys.argv:
        import importlib.util
        path = sys.argv[sys.argv.index("--reference-net") + 1]
        spec = importlib.util.spec_from_file_location("reference_net", path)
        reference_net = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference_net)
    source = T.load_walk_source(window=2000, limit=int(sys.argv[1]) if len(sys.argv) > 1 else 100)
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
                    for old, taken, counted in rows:
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


if __name__ == "__main__":
    main()
