import cProfile
import json
import os
from pathlib import Path
import pstats
import sys
import time

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor.mapgraph import train as T


def main():
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    from advisor.mapgraph import net
    source = T.load_walk_source(window=2000, limit=250)
    profile = cProfile.Profile()
    profile.enable()
    walked = T.walk_source(source, workers=1)
    profile.disable()
    profile.dump_stats(str(out / "input.prof"))
    with (out / "profile.txt").open("w") as file:
        pstats.Stats(profile, stream=file).sort_stats("cumtime").print_stats(65)
        pstats.Stats(profile, stream=file).sort_stats("tottime").print_stats(40)
    process = psutil.Process()
    metrics = dict(walked["metrics"], wall_seconds=time.perf_counter() - started,
                   cpu_seconds=sum(process.cpu_times()[:2]),
                   peak_rss_gib=process.memory_info().peak_wset / 2**30)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
