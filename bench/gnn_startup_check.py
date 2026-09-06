import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor.mapgraph import graph_config as GC
from advisor.mapgraph import greedy_train as GT
from advisor.mapgraph import optimize_greedy as O
from advisor.mapgraph import train as T
from bench.gnn_study_run import monitor


class StartupComplete(Exception):
    pass


def main():
    import torch
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.stdout = (out / "run.log").open("w", buffering=1, encoding="utf-8")
    sys.stderr = sys.stdout
    trial = json.loads(Path("bench/gnn-study-100-20260906/trials.json").read_text())[0]
    graph_config = GC.from_dict(trial["attrs"]["graph_config"])
    cfg = dict(trial["attrs"]["model_config"], time_budget_s=0)
    stop = threading.Event()
    thread = threading.Thread(target=monitor, args=(out, stop), daemon=True)
    thread.start()
    try:
        source = T.load_walk_source(window=2000)
        started = time.time()
        print("TRIAL_START", started, flush=True)
        probe = T.walk_source(O._probe_source(source, O.GRAPH_PROBE), graph_config)
        gate = O._graph_gate(torch, None, O.GPU_MEMORY_FRACTION)
        if probe["metrics"]["projected_tensor_gib"] > gate["allowed_graph_gib"]:
            raise RuntimeError("Original trial configuration exceeds the current GPU budget")
        del probe
        walked = T.walk_source(source, graph_config, workers=2)
        datas = T._tensorize(walked["examples"])
        ys = [e["y"] for e in walked["examples"]]
        groups = [e["campaign_id"] for e in walked["examples"]]
        for example in walked["examples"]:
            example["data"] = None
        prep = GT.prepare(datas, ys, groups, cfg, free_datas=True)
        def first_step(seconds):
            result = dict(graph_metrics=walked["metrics"], first_step_seconds=seconds,
                          model_config=cfg, graph_config=graph_config.as_dict(),
                          gpu_peak_gib=torch.cuda.max_memory_allocated() / 2**30)
            (out / "results.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result, indent=2), flush=True)
            raise StartupComplete()
        try:
            GT.fit_net(datas, ys, groups, cfg, prep=prep, trial_started=started,
                       on_first_step=first_step)
        except StartupComplete:
            pass
    finally:
        stop.set()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
