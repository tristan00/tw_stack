from __future__ import annotations


import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from advisor.mapgraph import graph_config as GC
from advisor.mapgraph import train as T


def configs():
    return {
        "default": GC.DEFAULT,
        "sparse": GC.GraphBuildConfig(
            spatial_neighbor_count=4,
            spatial_max_distance=50,
            spatial_node_types=("lord", "hero"),
            spatial_pair_types=("hero:hero", "hero:lord", "lord:lord"),
            attack_context_radius=15,
            attack_context_max_neighbors=4,
            attack_context_node_types=("lord", "hero"),
        ),
        "dense": GC.GraphBuildConfig(
            spatial_neighbor_count=32,
            attack_context_radius=50,
            include_own_citizenry_nodes=True,
        ),
    }


def run(limit=200, window=1000, log=print):
    source = T.load_walk_source(limit=limit, window=window, log=log)
    out = {"source": {"decisions": len(source["records"]),
                      "population_decisions": source["population_decisions"],
                      "seconds": round(source["source_seconds"], 3),
                      "window": source["window"]}, "graphs": {}}
    T.walk_source(source, GC.DEFAULT, limit=1, log=lambda _s: None)
    for name, config in configs().items():
        walked = T.walk_source(source, config, limit=limit, log=log)
        out["graphs"][name] = {"config": config.as_dict(),
                               "fingerprint": config.fingerprint(),
                               "metrics": walked["metrics"],
                               "tally": walked["tally"]}
    return out


if __name__ == "__main__":
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 200
    window = int(sys.argv[sys.argv.index("--window") + 1]) if "--window" in sys.argv else 1000
    print(json.dumps(run(limit=limit, window=window), indent=2))
