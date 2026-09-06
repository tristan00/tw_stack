from __future__ import annotations


import os
import sys
import time

from advisor.mapgraph import schema as S
from advisor.mapgraph import build as B
from advisor.mapgraph import graph_config as GC

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

THREADS = max(1, os.cpu_count() or 8)

CFG = {"hidden": 92, "entity_layers": 1, "action_rounds": 5,
       "map_aggr": "add+mean", "act_aggr": "mean", "attn": "all",
       "conv": "sage", "conv_map": None, "conv_a2e": None, "conv_e2a": "rel",
       "dst_dim": 4, "update": "linear", "self_transform": True,
       "dropout": 0.14751160117262713,
       "lr": 0.0002048320544808509, "weight_decay": 7.44846604643036e-05,
       "batch": 512, "patience": 10,
       "grad_clip": 0.2681914954781695, "bf16": True,
       "seed": 0, "time_budget_s": 600, "device": "cuda"}

MIN_FIT_S = 30


def _shard(args):
    db_path, out_dir, name, ranges, accept, rebuild, graph_config = args
    import os as _os
    import sys as _sys
    for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "NUMEXPR_NUM_THREADS"):
        _os.environ[_v] = "1"
    root = _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__))))
    if root not in _sys.path:
        _sys.path.insert(0, root)
    import common as _common
    for p in (_common.DECISIONS, _common.ADVISOR, _common.ROOT):
        if p not in _sys.path:
            _sys.path.insert(0, p)
    from store import DecisionStore
    from advisor import memory as M2
    from advisor.mapgraph import build as B2
    from advisor.mapgraph import corpus as CO2
    from advisor.mapgraph import net as N2
    import torch as _t
    _t.set_num_threads(1)

    fresh = {}
    st = DecisionStore(_os.path.dirname(db_path), readonly=True)
    try:
        for lo, hi in ranges:
            rows = st.labelled_decisions(after=lo - 1, before=hi)
            stamps = M2.replay_stamps(
                st, [r[0].get("decision_id") for r in rows
                     if r[0].get("decision_id") in rebuild])
            for rec, taken, counted in rows:
                did = rec.get("decision_id")
                if did not in rebuild:
                    continue
                patch = stamps.get(did)
                if patch:
                    (rec.setdefault("campaign", {})).update(patch)
                head = (did, rec.get("campaign_id"), rec.get("campaign"),
                        rec.get("turn"))
                th = CO2.taken_hash(taken)
                g = B2.build_graph(rec, graph_config)
                if g is None:
                    fresh[did] = head + ("no_graph", None, None, th)
                    continue
                if not g.action_nodes:
                    fresh[did] = head + ("no_actions", None, None, th)
                    continue
                want = (str(taken[0]), str(taken[1]), str(taken[2]), str(taken[3]))
                mask = [1.0 if k == want else 0.0 for k in g.action_keys]
                if sum(mask) != 1.0:
                    fresh[did] = head + ("taken_missing", None, None, th)
                    continue
                fresh[did] = head + (None, N2.to_data(g, taken=mask), g.counts, th)
            rows = None
    finally:
        st.close()

    if out_dir is None:
        return name, fresh, []
    recs = dict(fresh)
    carried = []
    path = _os.path.join(out_dir, name)
    if _os.path.exists(path):
        for rec in _t.load(path, weights_only=False):
            if rec[0] in accept and rec[0] not in recs:
                recs[rec[0]] = rec
                carried.append(rec[0])
    if recs:
        CO2.write_shard(out_dir, name, recs)
    return name, sorted(fresh), sorted(carried)


def walk(runs_root=None, limit=None, log=print, workers=None, window=None,
         graph_config=None):
    import concurrent.futures as cf
    import torch
    from base_model import (RUNS_ROOT, TARGET_WEIGHTS, TRAIN_WINDOW_CAMPAIGNS,
                            decision_deltas, target)
    from store import DecisionStore, IncompatibleStore
    started = time.time()
    graph_config = GC.from_dict(graph_config)
    log("mapgraph.train: walk enter graph=%s" % graph_config.fingerprint())
    runs_root = runs_root or RUNS_ROOT
    if window is None:
        window = TRAIN_WINDOW_CAMPAIGNS
    dbs = common.run_dbs(runs_root)

    from advisor.mapgraph import corpus as CO

    series, skipped, live = {}, [], []
    for db in dbs:
        run_dir = os.path.dirname(db)
        try:
            st = DecisionStore(run_dir, readonly=True)
        except IncompatibleStore as e:
            skipped.append(os.path.basename(run_dir))
            log("mapgraph.train: skipping %s -> %s" % (run_dir, str(e)[:100]))
            continue
        try:
            for camp, turns in st.target_series().items():
                series.setdefault(camp, {}).update(turns)
            floor = st.window_floor(window)
            live.append((db, st.max_decision_id(), st.taken_map(min_decision=floor),
                         floor))
        finally:
            st.close()

    t_walk = time.time()
    slots, built, reused = [], 0, 0
    for db, hi, taken_all, floor in live:
        run_key = os.path.basename(os.path.dirname(db))
        cached, cdir = ({}, None) if limit else CO.load(
            run_key, log=log, graph_config=graph_config)
        taken_now = (taken_all if floor is None else
                     {did: v for did, v in taken_all.items() if did >= floor})
        if floor is not None and len(taken_now) < len(taken_all):
            log("mapgraph.train: window %d campaigns -> %d of %d labelled decisions "
                "(floor %d)" % (window, len(taken_now), len(taken_all), floor))

        want = set()
        for did, (tup, _counted) in taken_now.items():
            hit = cached.get(did)
            if hit is None or hit[7] != CO.taken_hash(tup):
                want.add(did)
        if limit:
            want = set(sorted(want)[:max(limit * 8, 400)])

        by_shard = {}
        for did in taken_now:
            by_shard.setdefault(CO.shard_name(did), []).append(did)
        shard_want = {}
        for did in sorted(want):
            shard_want.setdefault(CO.shard_name(did), []).append(did)
        jobs = [(db, cdir, name, CO.ranges(dids),
                 frozenset(by_shard[name]), frozenset(dids), graph_config.as_dict())
                for name, dids in sorted(shard_want.items())]

        n_workers = workers or (min(16, THREADS, len(jobs))
                                if len(want) > CO.SHARD else 1)
        if jobs:
            log("mapgraph.train: rebuilding %d graphs over %d shard(s), %d worker(s)"
                % (len(want), len(jobs), n_workers))

        def _results():
            if n_workers <= 1 or len(jobs) <= 1:
                for j in jobs:
                    yield _shard(j)
                return
            with cf.ProcessPoolExecutor(max_workers=n_workers) as ex:
                yield from ex.map(_shard, jobs, chunksize=1)

        hold, done, from_fresh, from_cache = {}, {}, set(), set()
        for name, fresh, carried in _results():
            if cdir is None:
                hold.update(fresh)
                from_fresh.update(fresh)
                continue
            if fresh or carried:
                done[name] = len(fresh) + len(carried)
            from_fresh.update(fresh)
            from_cache.update(carried)
            log("mapgraph.train: %s written -- %d built, %d carried (%d of %d)"
                % (name, len(fresh), len(carried), len(from_fresh), len(want)))

        for did in taken_now:
            if did in cached and did not in from_fresh and did not in from_cache:
                from_cache.add(did)

        if done:
            covered = from_fresh | from_cache
            CO.write_manifest(cdir, {CO.shard_name(d) for d in covered},
                              len(covered), max(covered), log=log)
            pool = {did: cached[did] for did in from_cache
                    if CO.shard_name(did) not in done}
            for name in sorted(done):
                pool.update(CO.read_shard(cdir, name))
            cached = None
        else:
            pool = {did: cached[did] for did in from_cache}
            pool.update(hold)
        built += len(from_fresh)
        reused += len(from_cache)
        slots.extend(pool[k] for k in sorted(pool))

    graph_seconds = time.time() - t_walk
    log("mapgraph.train: graph load/build %.1fs -- %d built, %d reused"
        % (graph_seconds, built, reused))

    examples = []
    tally = {"no_graph": 0, "no_label": 0, "taken_missing": 0, "no_actions": 0}
    for did, camp_id, campaign, turn, drop, data, counts, _thash in slots:
        deltas = decision_deltas(campaign, series.get(camp_id) or {}, turn)
        y = target(deltas)
        if y is None:
            tally["no_label"] += 1
            continue
        if drop is not None:
            tally[drop] += 1
            continue
        gain = sum(TARGET_WEIGHTS.get(k, 1.0) * v for k, v in deltas.items()
                   if k != "survival" and v is not None)
        data.y = torch.tensor([float(y)], dtype=torch.float32)
        examples.append({"data": data, "y": float(y), "gain": float(gain),
                         "campaign_id": camp_id, "counts": counts})
        if limit and len(examples) >= limit:
            break
    metrics = graph_metrics(examples, built=built, reused=reused,
                            source_seconds=t_walk - started,
                            graph_seconds=graph_seconds,
                            total_seconds=time.time() - started,
                            population=len(slots))
    log("mapgraph.train: walk exit %.1fs -- %s"
        % (metrics["total_seconds"], format_graph_metrics(metrics)))
    return {"examples": examples, "tally": tally, "runs": len(dbs) - len(skipped),
            "n_decisions": len(slots),
            "campaigns": sorted({e["campaign_id"] for e in examples}),
            "graph_config": graph_config.as_dict(), "metrics": metrics}


def _percentile(values, pct):
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * pct + 0.5))]


def _data_bytes(data):
    import torch
    return sum(v.numel() * v.element_size() for v in data.to_dict().values()
               if torch.is_tensor(v))


def graph_metrics(examples, built=0, reused=0, source_seconds=0.0,
                  graph_seconds=0.0, total_seconds=0.0, population=None):
    nodes = [int(e["counts"]["nodes"]) for e in examples]
    edges = [int(e["counts"]["edges"]) for e in examples]
    actions = [int(e["counts"]["actions"]) for e in examples]
    tensor_bytes = sum(_data_bytes(e["data"]) for e in examples)
    n = len(examples)
    processed = int(built) + int(reused)
    population = int(population if population is not None else n)
    valid_rate = n / max(1, processed)
    projected = int(round(population * valid_rate))
    return {
        "graphs": n, "processed": processed, "built": int(built), "reused": int(reused),
        "source_seconds": round(float(source_seconds), 3),
        "graph_seconds": round(float(graph_seconds), 3),
        "total_seconds": round(float(total_seconds), 3),
        "graphs_per_second": (round(processed / graph_seconds, 2)
                              if graph_seconds else 0.0),
        "nodes_total": sum(nodes), "nodes_mean": round(sum(nodes) / n, 2) if n else 0.0,
        "nodes_p95": _percentile(nodes, 0.95), "nodes_max": max(nodes, default=0),
        "edges_total": sum(edges), "edges_mean": round(sum(edges) / n, 2) if n else 0.0,
        "edges_p95": _percentile(edges, 0.95), "edges_max": max(edges, default=0),
        "actions_total": sum(actions),
        "tensor_bytes": tensor_bytes,
        "tensor_gib": round(tensor_bytes / (1024 ** 3), 4),
        "population_decisions": population,
        "projected_graphs": projected,
        "projected_nodes_total": int(round(sum(nodes) / max(1, n) * projected)),
        "projected_edges_total": int(round(sum(edges) / max(1, n) * projected)),
        "projected_tensor_gib": round(
            tensor_bytes / max(1, n) * projected / (1024 ** 3), 4),
    }


def format_graph_metrics(metrics):
    return ("%d graphs %.1f/s, nodes mean/p95/max %.1f/%d/%d, "
            "edges mean/p95/max %.1f/%d/%d, tensors %.3f GiB, "
            "source/graph %.1fs/%.1fs"
            % (metrics["graphs"], metrics["graphs_per_second"],
               metrics["nodes_mean"], metrics["nodes_p95"], metrics["nodes_max"],
               metrics["edges_mean"], metrics["edges_p95"], metrics["edges_max"],
               metrics["tensor_gib"], metrics["source_seconds"],
               metrics["graph_seconds"]))


def load_walk_source(runs_root=None, limit=None, log=print, window=None):
    from base_model import (RUNS_ROOT, TARGET_WEIGHTS, TRAIN_WINDOW_CAMPAIGNS,
                            decision_deltas, target)
    from store import DecisionStore, IncompatibleStore
    from advisor import memory as M2
    started = time.time()
    log("mapgraph.train: source load enter")
    runs_root = runs_root or RUNS_ROOT
    window = TRAIN_WINDOW_CAMPAIGNS if window is None else window
    dbs = common.run_dbs(runs_root)
    series, raw, skipped, population = {}, [], [], 0
    for db in dbs:
        run_dir = os.path.dirname(db)
        try:
            st = DecisionStore(run_dir, readonly=True)
        except IncompatibleStore as e:
            skipped.append(os.path.basename(run_dir))
            log("mapgraph.train: skipping %s -> %s" % (run_dir, str(e)[:100]))
            continue
        try:
            floor = st.window_floor(window)
            population += st.labelled_count(min_decision=floor)
            hydrate_limit = max(limit * 2, limit + 50) if limit else None
            rows = st.labelled_decisions(
                after=(floor - 1) if floor is not None else None, limit=hydrate_limit,
                spread=bool(limit))
            campaign_keys = {r[0].get("campaign_id") for r in rows}
            for camp, turns in st.target_series(campaign_keys).items():
                series.setdefault(camp, {}).update(turns)
            stamps = M2.replay_stamps(st, [r[0].get("decision_id") for r in rows])
            for rec, taken, counted in rows:
                patch = stamps.get(rec.get("decision_id"))
                if patch:
                    rec.setdefault("campaign", {}).update(patch)
                raw.append((rec, taken, counted))
        finally:
            st.close()
    records = []
    for rec, taken, counted in raw:
        camp_id = rec.get("campaign_id")
        campaign = rec.get("campaign")
        turn = rec.get("turn")
        deltas = decision_deltas(campaign, series.get(camp_id) or {}, turn)
        y = target(deltas)
        gain = None if y is None else sum(
            TARGET_WEIGHTS.get(k, 1.0) * v for k, v in deltas.items()
            if k != "survival" and v is not None)
        records.append((rec, taken, counted, y, gain))
    seconds = time.time() - started
    log("mapgraph.train: source load exit %.1fs -- %d decisions, %d campaigns"
        % (seconds, len(records), len({r[0].get("campaign_id") for r in records})))
    return {"records": records, "runs": len(dbs) - len(skipped),
            "source_seconds": seconds, "window": window,
            "population_decisions": population}


def walk_source(source, graph_config=None, limit=None, log=print):
    from advisor.mapgraph import net as N
    import torch
    graph_config = GC.from_dict(graph_config)
    started = time.time()
    log("mapgraph.train: graph walk enter graph=%s" % graph_config.fingerprint())
    examples = []
    tally = {"no_graph": 0, "no_label": 0, "taken_missing": 0, "no_actions": 0}
    for rec, taken, counted, y, gain in source["records"]:
        if y is None:
            tally["no_label"] += 1
            continue
        g = B.build_graph(rec, graph_config)
        if g is None:
            tally["no_graph"] += 1
            continue
        if not g.action_nodes:
            tally["no_actions"] += 1
            continue
        want = tuple(str(v) for v in taken)
        mask = [1.0 if k == want else 0.0 for k in g.action_keys]
        if sum(mask) != 1.0:
            tally["taken_missing"] += 1
            continue
        data = N.to_data(g, taken=mask)
        data.y = torch.tensor([float(y)], dtype=torch.float32)
        examples.append({"data": data, "y": float(y), "gain": float(gain),
                         "campaign_id": rec.get("campaign_id"), "counts": g.counts})
        if limit and len(examples) >= limit:
            break
    seconds = time.time() - started
    metrics = graph_metrics(examples, built=len(examples), reused=0,
                            source_seconds=source.get("source_seconds", 0.0),
                            graph_seconds=seconds,
                            total_seconds=source.get("source_seconds", 0.0) + seconds,
                            population=source.get("population_decisions"))
    log("mapgraph.train: graph walk exit %.1fs -- %s"
        % (seconds, format_graph_metrics(metrics)))
    return {"examples": examples, "tally": tally, "runs": source["runs"],
            "n_decisions": len(source["records"]),
            "campaigns": sorted({e["campaign_id"] for e in examples}),
            "graph_config": graph_config.as_dict(), "metrics": metrics}


def _tensorize(examples):
    return [ex["data"] for ex in examples]


def _device(cfg, log):
    import torch
    want = str(cfg.get("device") or "")
    if want == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("mapgraph.train: device=cuda requested, CUDA unavailable")
        log("mapgraph.train: device cuda (%s)" % torch.cuda.get_device_name(0))
        return torch.device("cuda")
    if want == "cpu":
        log("mapgraph.train: device cpu")
        return torch.device("cpu")
    raise RuntimeError("mapgraph.train: device must be cuda or cpu, got %r" % want)


def _corpus_bytes(batches):
    import torch
    return sum(v.numel() * v.element_size() for b in batches
               for v in b.to_dict().values() if torch.is_tensor(v))


def _collate(items, size, dev, log, tag):
    import torch
    from torch_geometric.data import Batch
    batches = [Batch.from_data_list(items[k:k + size]).to(dev, non_blocking=True)
               for k in range(0, len(items), size)]
    if dev.type == "cuda" and batches:
        torch.cuda.synchronize()
        log("mapgraph.train: %s %d batches resident on %s (%.2fGB)"
            % (tag, len(batches), dev.type, _corpus_bytes(batches) / 1e9))
    return batches
