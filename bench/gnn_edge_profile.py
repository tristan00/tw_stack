import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys
import time

import numpy as np
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor.mapgraph import build as B
from advisor.mapgraph import graph_config as GC
from advisor.mapgraph import schema as S
from advisor.mapgraph import train as T
from advisor.mapgraph.arrays import to_arrays


def percentile(histogram, fraction):
    threshold = sum(histogram.values()) * fraction
    running = 0
    for value, count in sorted(histogram.items()):
        running += count
        if running >= threshold:
            return int(value)
    return 0


def median(histogram):
    total = sum(histogram.values())
    if not total:
        return 0
    lower = percentile(histogram, ((total + 1) // 2) / total)
    upper = percentile(histogram, (total // 2 + 1) / total)
    return (lower + upper + 1) // 2


def write_report(report, document, out):
    rows = []
    for name, evidence in document["evidence"].items():
        forward = {int(k): v for k, v in evidence["forward"].items()}
        reverse = {int(k): v for k, v in evidence["reverse"].items()}
        rows.append({"relation": name, "default": document["limits"][name],
                     "observations_forward": sum(forward.values()), "observations_reverse": sum(reverse.values()),
                     "median_forward": median(forward), "median_reverse": median(reverse),
                     "basis": "observed median" if evidence["observed"] else "unobserved: user default 1"})
    with (out / "defaults.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    text = ["# GNN edge selection measurements", "",
            "Every relation has its own integer cap. Zero disables it. The cap applies independently to incoming forward and reverse neighbors.", "",
            "All selection uses squared endpoint distance, then stable neighbor identity. Missing locations sort last; when the destination has no location, identity determines ordering. Duplicate connections to the same neighbor are removed. Spatial selection uses a KD-tree without a distance cutoff or an all-pairs matrix.", "",
            "Defaults use the larger of the two positive-degree medians, rounded up to an integer. Unobserved relations default to 1. These are structural defaults, not prediction-quality optima.", "",
            f"Calibration window: {document['window']}; population: {document['population']:,} labelled decisions; evenly spread sample: {document['sample']:,}; valid graphs: {document['valid_graphs']:,}; excluded by training's label/action matching rules: {document['skipped']}.", "",
            "The proposed tuning set includes proximity relations and the highest-volume relations with defaults above one. Upper bounds use the larger directional p99. All ranges use integer steps of one.", "",
            "| Tunable relation | Default | Range |", "|---|---:|---:|"]
    for name, bounds in document["tuning_ranges"].items():
        text.append(f"| `{name}` | {document['limits'][name]} | {bounds[0]}–{bounds[1]} |")
    text += ["", "Only those tunable caps vary below; every other cap remains at its per-relation median default. Small uses 1, large uses each upper bound, and disabled uses 0. Node counts therefore remain unchanged.", "",
             "| Configuration | Graphs | Mean nodes | Minimum edges | Mean directed edges | P95 edges | Maximum edges | Mean tensor KiB | Candidate edges retained | Total seconds | Observed peak RSS GiB |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in report["variants"].items():
        text.append(f"| {name} | {row['graphs']:,} | {row['nodes_mean']:.1f} | {row['edges_min']:,} | {row['edges_mean']:.1f} | {row['edges_p95']:.0f} | {row['edges_max']:,} | {row['tensor_mib_mean'] * 1024:.1f} | {row['candidate_retention']:.1%} | {row['seconds']:.2f} | {row['peak_rss_gib']:.3f} |")
    text += ["", "Sizes are measured CPU tensor payloads for individual graphs, including node features and labels. They are not GPU training-memory measurements. Timings include fresh input queries, graph construction, and candidate-degree instrumentation for each configuration; source-head selection is recorded separately in measure.json. Benchmarks run sequentially in one process without retaining graph corpora between configurations. RSS is sampled after each graph and can miss transient peaks. The existing GPU-budget oversized-graph rejection is unchanged.", "",
             "| Relation | Default | Forward median | Reverse median | Forward observations | Reverse observations | Basis |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        text.append(f"| `{row['relation']}` | {row['default']} | {row['median_forward']} | {row['median_reverse']} | {row['observations_forward']:,} | {row['observations_reverse']:,} | {row['basis']} |")
    text += ["", "Observation counts are node/relation occurrences across sampled graphs, not distinct campaign entities. Nodes with no candidate of that relation are excluded from its degree distribution. The full histograms are in evidence.json; sample.json records the selected source heads.", "",
             "The graph schema version is now 12. Models trained under the previous selection rules require retraining; old graph configurations are rejected rather than silently translated.", ""]
    (out / "results.md").write_text("\n".join(text), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("calibrate", "measure"))
    parser.add_argument("--sample", type=int, default=512)
    parser.add_argument("--window", type=int, default=4000)
    parser.add_argument("--out", type=Path, default=Path("bench/gnn-edge-selection"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    print("edge profile enter", args.mode, flush=True)
    source = T.load_walk_source(window=args.window)
    count = min(args.sample, len(source["records"]))
    indices = np.linspace(0, len(source["records"]) - 1, count, dtype=int).tolist()
    records = source["records"].select(indices)
    identity = [{"run": run, "heads": heads} for run, heads in records.jobs]
    (args.out / "sample.json").write_text(json.dumps(identity, default=str))
    histograms = [Counter() for _ in range(S.N_RELATIONS)]
    def observe(relation, counts):
        values, frequencies = np.unique(counts[counts > 0], return_counts=True)
        histograms[relation].update(dict(zip(values.tolist(), frequencies.tolist())))
    variants = {"calibration": GC.GraphBuildConfig({name: 0 for name in S.RELATIONS})}
    if args.mode == "measure":
        document = json.loads(GC.DEFAULTS_PATH.read_text())
        variants = {"small": GC.GraphBuildConfig(dict(document["limits"], **{r: 1 for r in document["tuning_ranges"]})),
                    "default": GC.GraphBuildConfig(),
                    "large": GC.GraphBuildConfig(dict(document["limits"], **{r: bounds[1] for r, bounds in document["tuning_ranges"].items()})),
                    "disabled_tunables": GC.GraphBuildConfig(dict(document["limits"], **{r: 0 for r in document["tuning_ranges"]}))}
    report = {"window": args.window, "population": len(source["records"]), "sample": count,
              "source_seconds": source["source_seconds"], "variants": {}}
    for variant, config in variants.items():
        stage = time.perf_counter()
        nodes, edges, sizes = [], [], []
        peak_rss, build_seconds, skipped = 0, 0.0, 0
        relation_totals = np.zeros(S.N_RELATIONS, dtype=np.int64)
        candidate_totals = np.zeros(S.N_RELATIONS, dtype=np.int64)
        def count_candidates(relation, counts):
            candidate_totals[relation] += counts.sum()
        for rec, taken, counted, y, gain in records:
            want = tuple(str(v) for v in taken)
            matches = sum((str(e.get("context_kind")), str(e.get("context_id")),
                           str(o.get("action_type") or ""), str(o.get("key") or "")) == want
                          for e in rec.get("entities", []) for o in e.get("offers", []))
            if y is None or matches != 1:
                skipped += 1
                continue
            tick = time.perf_counter()
            graph = B.build_graph(rec, config, observe if args.mode == "calibrate" else count_candidates)
            build_seconds += time.perf_counter() - tick
            nodes.append(len(graph.x))
            edges.append(len(graph.src))
            relation_totals += np.bincount(graph.rel, minlength=S.N_RELATIONS)
            if args.mode == "measure":
                mask = [float(k == tuple(str(v) for v in taken)) for k in graph.action_keys]
                if sum(mask) != 1:
                    raise ValueError("taken action missing in measured graph")
                arrays = to_arrays(graph, y=y, taken=mask)
                sizes.append(sum(a.nbytes for a in arrays.values()))
                del arrays
            peak_rss = max(peak_rss, psutil.Process().memory_info().rss)
            if len(nodes) % 32 == 0:
                print(variant, len(nodes), "graphs", round(time.perf_counter() - stage, 2), "seconds", flush=True)
            del graph
        report["variants"][variant] = {"graphs": len(nodes), "skipped": skipped, "seconds": time.perf_counter() - stage,
                                       "build_seconds": build_seconds, "query_seconds": records.query_seconds,
                                       "input_seconds": records.input_seconds, "peak_rss_gib": peak_rss / 2**30,
                                       "nodes_mean": float(np.mean(nodes)), "nodes_min": min(nodes), "nodes_max": max(nodes),
                                       "edges_mean": float(np.mean(edges)), "edges_min": min(edges), "edges_max": max(edges),
                                       "edges_p95": float(np.percentile(edges, 95)), "tensor_bytes": sum(sizes),
                                       "tensor_mib_mean": float(np.mean(sizes)) / 2**20 if sizes else 0,
                                       "config": config.as_dict(), "relation_totals": relation_totals.tolist(),
                                       "candidate_totals": candidate_totals.tolist(),
                                       "candidate_retention": float(relation_totals.sum() / max(1, candidate_totals.sum()))}
        print(json.dumps(report["variants"][variant]), flush=True)
    if args.mode == "calibrate":
        limits, evidence = {}, {}
        for index, name in enumerate(S.RELATIONS):
            forward, reverse = histograms[index], histograms[index + S.N_FORWARD_RELATIONS]
            limits[name] = max(1, median(forward), median(reverse))
            evidence[name] = {"forward": dict(sorted(forward.items())), "reverse": dict(sorted(reverse.items())),
                              "observed": bool(forward or reverse), "default": limits[name]}
        tunables = list(GC.TUNING_RANGES)
        ranges = {name: [0, max(percentile(histograms[S.REL_INDEX[name]], .99),
                               percentile(histograms[S.REL_INDEX[name] + S.N_FORWARD_RELATIONS], .99))]
                  for name in tunables}
        document = {"method": "Maximum of forward and reverse positive incoming-degree medians; p99 tuning upper bounds; one for unobserved relations (user specified)",
                    "window": args.window, "sample": count, "population": report["population"],
                    "valid_graphs": report["variants"]["calibration"]["graphs"],
                    "skipped": report["variants"]["calibration"]["skipped"],
                    "limits": limits, "tuning_ranges": ranges, "evidence": evidence}
        GC.DEFAULTS_PATH.write_text(json.dumps(document, indent=2) + "\n")
        (args.out / "evidence.json").write_text(json.dumps(document, indent=2) + "\n")
    report["seconds"] = time.perf_counter() - started
    (args.out / (args.mode + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    if args.mode == "measure":
        write_report(report, document, args.out)
    print("edge profile exit", report["seconds"], flush=True)


if __name__ == "__main__":
    main()

