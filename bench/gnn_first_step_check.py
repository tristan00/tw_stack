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


class FirstStepComplete(Exception):
    pass


def main():
    import torch
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=False)
    sys.stdout = (out / "run.log").open("w", buffering=1, encoding="utf-8")
    sys.stderr = sys.stdout
    trials = json.loads(Path("bench/gnn-study-12edges-window2500-20260906-162344/trials.json").read_text())
    trial = next(t for t in trials if t["number"] == 0)
    config = GC.from_dict(trial["attrs"]["graph_config"])
    cfg = trial["attrs"]["model_config"]
    stop = threading.Event()
    thread = threading.Thread(target=monitor, args=(out, stop), daemon=True)
    thread.start()
    try:
        source = T.load_walk_source(window=2500)
        started = time.time()
        probe = T.walk_source(O._probe_source(source, O.GRAPH_PROBE), config)
        gate = O._graph_gate(torch, None, O.GPU_MEMORY_FRACTION)
        if probe["metrics"]["projected_tensor_gib"] > gate["allowed_graph_gib"]:
            raise RuntimeError("Original graph exceeds GPU budget")
        del probe
        walked = T.walk_source(source, config, workers=4)
        datas = T._tensorize(walked["examples"])
        ys = [e["y"] for e in walked["examples"]]
        groups = [e["campaign_id"] for e in walked["examples"]]
        for example in walked["examples"]:
            example["data"] = None
        prep = GT.prepare(datas, ys, groups, cfg, free_datas=True)
        prepared = time.time() - started
        def first_step(seconds):
            result = {"first_step_seconds": seconds, "prepared_seconds": prepared,
                      "first_step_compute_and_setup_seconds": seconds - prepared,
                      "window": 2500, "model_config": cfg, "graph_config": config.as_dict(),
                      "graph_metrics": walked["metrics"], "gate": gate,
                      "gpu_peak_allocated_gib": torch.cuda.max_memory_allocated() / 2**30,
                      "gpu_peak_reserved_gib": torch.cuda.max_memory_reserved() / 2**30}
            (out / "results.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result), flush=True)
            raise FirstStepComplete()
        try:
            GT.fit_net(datas, ys, groups, cfg, prep=prep, trial_started=started, on_first_step=first_step)
        except FirstStepComplete:
            pass
    finally:
        stop.set()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
