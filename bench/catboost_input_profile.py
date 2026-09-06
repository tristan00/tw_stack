import argparse
import cProfile
import itertools
import json
from pathlib import Path
import pstats
import sys
import time

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor import model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=int, default=4000)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pool", action="store_true")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--fit", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    if args.limit:
        original = model.DecisionStore.taken_rows

        def limited_generator(self, *a, **kw):
            rows = original(self, *a, **kw)
            try:
                yield from itertools.islice(rows, args.limit)
            finally:
                rows.close()

        model.DecisionStore.taken_rows = limited_generator
    started = time.perf_counter()
    profile = cProfile.Profile()
    if args.profile:
        profile.enable()
    data = model.gather(window=args.window, **({"as_pool": True} if args.pool else {}))
    if args.profile:
        profile.disable()
        profile.dump_stats(str(out / "input.prof"))
        with (out / "profile.txt").open("w") as file:
            pstats.Stats(profile, stream=file).sort_stats("cumtime").print_stats(65)
    metrics = {k: v for k, v in data.items() if k not in
               ("full", "pool", "y", "groups", "confirmed", "num", "cat")}
    metrics.update(seconds=time.perf_counter() - started,
                   rows=len(data["y"]),
                   peak_rss_mib=psutil.Process().memory_info().peak_wset / 2**20)
    if args.fit:
        report = {}
        fitted = model.fit_es(data["pool"], data["y"], [], data["groups"], "smoke", report,
                              iterations=5, base={"depth": 4, "thread_count": 4,
                                                   "learning_rate": 0.1})
        predictions = fitted.predict(data["pool"])
        assert len(predictions) == len(data["y"])
        metrics["fit"] = report
        metrics["peak_with_fit_mib"] = psutil.Process().memory_info().peak_wset / 2**20
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2), flush=True)


if __name__ == "__main__":
    main()
