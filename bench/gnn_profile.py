import cProfile
import gc
import json
import os
import sys
import threading
import time

import psutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisor.mapgraph import train as T
from advisor.mapgraph import greedy_train as GT
from advisor.mapgraph import graph_config as GC


def monitor():
    while not stop.wait(2):
        mem = process.memory_info()
        children = process.children(recursive=True)
        child_rss = 0
        for child in children:
            try:
                child_rss += child.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        print(json.dumps({"phase": phase, "seconds": round(time.perf_counter() - started, 2),
                          "rss_gib": round(mem.rss / 2**30, 3),
                          "tree_rss_gib": round((mem.rss + child_rss) / 2**30, 3),
                          "private_gib": round(mem.private / 2**30, 3),
                          "cpu_s": sum(process.cpu_times()[:2]),
                          "available_gib": round(psutil.virtual_memory().available / 2**30, 3)}), flush=True)
        if psutil.virtual_memory().available < 8 * 2**30:
            print("RESOURCE STOP: less than 8 GiB available", flush=True)
            children = process.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(children, timeout=5)
            os._exit(3)


def main():
    global started, process, phase, stop
    output = "bench/gnn-profile"
    if "--output" in sys.argv:
        output = sys.argv[sys.argv.index("--output") + 1]
        sys.stdout = open(output + ".log", "w", buffering=1)
        sys.stderr = open(output + "-stderr.log", "w", buffering=1)
    started = time.perf_counter()
    process = psutil.Process()
    phase = "imports"
    stop = threading.Event()
    gc_seconds = [0.0, 0.0]
    def collection(event, info):
        if event == "start":
            gc_seconds[0] = time.perf_counter()
        else:
            gc_seconds[1] += time.perf_counter() - gc_seconds[0]
    gc.callbacks.append(collection)

    threading.Thread(target=monitor, daemon=True).start()
    limit = int(sys.argv[1]) or None
    profile = cProfile.Profile()
    if "--no-profile" not in sys.argv:
        profile.enable()
    phase = "source"
    source = T.load_walk_source(window=2000, limit=limit, log=lambda s: print(s, flush=True))
    profile.disable()
    if "--no-profile" not in sys.argv:
        profile.dump_stats(output + "-source.prof")
    if "--source-only" not in sys.argv:
        import torch
        phase = "graphs"
        profile = cProfile.Profile()
        if "--no-profile" not in sys.argv:
            profile.enable()
        workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 1
        walked = T.walk_source(source, GC.DEFAULT, limit=limit, workers=workers)
        profile.disable()
        if "--no-profile" not in sys.argv:
            profile.dump_stats(output + "-graphs.prof")
        datas = T._tensorize(walked["examples"])
        ys = [e["y"] for e in walked["examples"]]
        groups = [e["campaign_id"] for e in walked["examples"]]
        for e in walked["examples"]:
            e["data"] = None
        cfg = dict(GT.CFG, time_budget_s=20)
        if "--small-model" in sys.argv:
            cfg.update(hidden=32, entity_layers=1, action_rounds=1)
        phase = "prepare"
        gc_seconds[1] = 0.0
        profile = cProfile.Profile()
        if "--no-profile" not in sys.argv:
            profile.enable()
        t = time.perf_counter()
        prep = GT.prepare(datas, ys, groups, cfg, free_datas=True)
        print("PREPARE_SECONDS", time.perf_counter() - t, flush=True)
        profile.disable()
        if "--no-profile" not in sys.argv:
            profile.dump_stats(output + "-prepare.prof")
        print("PREPARE_GC_SECONDS", gc_seconds[1], flush=True)
        phase = "fit"
        _, fit, _, _ = GT.fit_net(datas, ys, groups, cfg, prep=prep)
        print(json.dumps(fit), flush=True)
        print("GPU_PEAK_GIB", torch.cuda.max_memory_allocated() / 2**30, flush=True)
    stop.set()
    print("TOTAL_SECONDS", time.perf_counter() - started, flush=True)


if __name__ == "__main__":
    main()
