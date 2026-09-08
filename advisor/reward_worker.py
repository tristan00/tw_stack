from contextlib import redirect_stderr, redirect_stdout
import json
import logging
import multiprocessing
from pathlib import Path
import time
import traceback

MAX_TRIAL_SECONDS = 600


LOG = logging.getLogger(__name__)


def validate_budget(seconds):
    if not 0 < seconds <= MAX_TRIAL_SECONDS:
        raise ValueError("trial budget must be in (0, 600] seconds")
    return float(seconds)


def worker_entry(family, window, config, directory, deadline, device, seed, max_cache_bytes):
    directory = Path(directory)
    with (directory / "worker.log").open("w", encoding="utf-8", buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            logging.basicConfig(level=logging.INFO, stream=log, force=True, format="%(asctime)s %(message)s")
            try:
                if family == "sequence":
                    from advisor.mapgraph.live import fit
                elif family == "temporal_graph":
                    from advisor.temporal_graph.tuning.train import fit
                else:
                    raise ValueError("unknown reward model family")
                result = fit(window, config, directory, deadline, device, seed, max_cache_bytes)
                response = dict(status="complete", result=result)
            except Exception as error:
                traceback.print_exc()
                bounded = isinstance(error, (TimeoutError, MemoryError, FloatingPointError))
                bounded |= type(error).__name__ == "OutOfMemoryError"
                bounded |= isinstance(error, ValueError) and any(
                    term in str(error) for term in ("exceed max_nodes", "exceed max_edges", "exceeds dense_pair_limit"))
                response = dict(status="pruned" if bounded else "failed", error=repr(error))
            temporary = directory / "result.tmp"
            temporary.write_text(json.dumps(response, allow_nan=False), encoding="utf-8")
            temporary.replace(directory / "result.json")


def run_trial(family, window, config, directory, budget, device, seed, max_cache_bytes,
              context=None, clock=time.perf_counter):
    budget = validate_budget(budget)
    started = clock()
    deadline = started + budget
    context = multiprocessing.get_context("spawn") if context is None else context
    process = context.Process(target=worker_entry, args=(family, window, config, str(directory),
                              deadline, device, seed, max_cache_bytes))
    LOG.info("trial worker enter budget=%.2f", budget)
    try:
        process.start()
        while process.is_alive() and clock() < deadline:
            process.join(timeout=min(1.0, max(0.0, deadline - clock())))
        if process.is_alive() or clock() > deadline:
            raise TimeoutError("trial exhausted its %.2f second wall-clock budget" % budget)
        if process.exitcode != 0:
            raise RuntimeError("trial worker exited with code %s; inspect worker.log" % process.exitcode)
        path = Path(directory) / "result.json"
        if not path.is_file():
            raise RuntimeError("trial worker exited without a result")
        return json.loads(path.read_text(encoding="utf-8"))
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
                if process.is_alive():
                    raise RuntimeError("trial worker could not be stopped")
            process.close()
        cache = Path(directory) / "graph_cache.bin"
        cache.unlink(missing_ok=True)
        LOG.info("trial worker exit seconds=%.3f", clock() - started)
