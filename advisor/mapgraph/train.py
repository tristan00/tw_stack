from __future__ import annotations


import os
import sys
import time

from advisor.mapgraph import schema as S
from advisor.mapgraph import build as B

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

THREADS = max(1, os.cpu_count() or 8)

CFG = {"hidden": 128, "entity_layers": 1, "action_rounds": 2,
       "map_aggr": "max", "act_aggr": "max", "attn": "all",
       "conv": "rel", "conv_map": None, "conv_a2e": None, "conv_e2a": "sage",
       "dst_dim": 32, "update": "mlp", "self_transform": False,
       "dropout": 0.15413814698176437,
       "lr": 6.232513234350002e-05, "weight_decay": 2.7754941337109324e-05,
       "batch": 384, "epochs": 14, "patience": 10,
       "grad_clip": 5.0, "bf16": True,
       "seed": 0, "time_budget_s": 1200, "device": "cuda"}

MIN_FIT_S = 30


def _shard(args):
    db_path, lo, hi = args
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
    from advisor.mapgraph import build as B2
    from advisor.mapgraph import net as N2
    import torch as _t
    _t.set_num_threads(1)

    st = DecisionStore(_os.path.dirname(db_path), readonly=True)
    try:
        rows = st.labelled_decisions(after=lo, before=hi)
        from advisor import memory as M2
        stamps = (M2.replay_stamps(st, [r[0].get("decision_id") for r in rows])
                  if rows else {})
    finally:
        st.close()

    out = []
    for rec, taken, counted in rows:
        patch = stamps.get(rec.get("decision_id"))
        if patch:
            (rec.setdefault("campaign", {})).update(patch)
        head = (rec.get("decision_id"), rec.get("campaign_id"), rec.get("campaign"),
                rec.get("turn"))
        g = B2.build_graph(rec)
        if g is None:
            out.append(head + ("no_graph", None, None))
            continue
        if not g.action_nodes:
            out.append(head + ("no_actions", None, None))
            continue
        want = (str(taken[0]), str(taken[1]), str(taken[2]), str(taken[3]))
        mask = [1.0 if k == want else 0.0 for k in g.action_keys]
        if sum(mask) != 1.0:
            out.append(head + ("taken_missing", None, None))
            continue
        out.append(head + (None, N2.to_data(g, taken=mask), g.counts))
    return out


def walk(runs_root=None, limit=None, log=print, workers=None, window=None):
    import concurrent.futures as cf
    import torch
    from base_model import (RUNS_ROOT, TARGET_WEIGHTS, TRAIN_WINDOW_CAMPAIGNS,
                            decision_deltas, target)
    from store import DecisionStore, IncompatibleStore
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
            live.append((db, st.max_decision_id(), st.taken_map(),
                         st.window_floor(window)))
        finally:
            st.close()

    n_workers = 1 if workers is None else workers
    t_walk = time.time()
    slots, built, reused = [], 0, 0
    for db, hi, taken_all, floor in live:
        run_key = os.path.basename(os.path.dirname(db))
        cached, cdir = ({}, None) if limit else CO.load(run_key, log=log)
        taken_now = (taken_all if floor is None else
                     {did: v for did, v in taken_all.items() if did >= floor})
        if floor is not None and len(taken_now) < len(taken_all):
            log("mapgraph.train: window %d campaigns -> %d of %d labelled decisions "
                "(floor %d)" % (window, len(taken_now), len(taken_all), floor))

        want = {}
        for did, (tup, _counted) in taken_now.items():
            th = CO.taken_hash(tup)
            hit = cached.get(did)
            if hit is None or hit[7] != th:
                want[did] = th
        if limit:
            want = dict(sorted(want.items())[:max(limit * 8, 400)])

        jobs = []
        if want:
            if len(want) > 0.5 * max(1, len(taken_now)):
                jobs = [(db, (floor - 1) if floor is not None else None, None)]
            else:
                jobs = [(db, lo - 1, hi2) for lo, hi2 in CO.ranges(want)]

        fresh = {}
        if jobs and (n_workers <= 1 or len(jobs) <= 1):
            for j in jobs:
                for rec in _shard(j):
                    fresh[rec[0]] = rec
        elif jobs:
            with cf.ProcessPoolExecutor(max_workers=n_workers) as ex:
                for part in ex.map(_shard, jobs, chunksize=1):
                    for rec in part:
                        fresh[rec[0]] = rec

        merged = {}
        for did in taken_now:
            rec = fresh.get(did)
            if rec is not None:
                merged[did] = rec + (want.get(did) or CO.taken_hash(taken_now[did][0]),)
                built += 1
            elif did in cached:
                merged[did] = cached[did]
                reused += 1
        if cdir is not None and merged and fresh:
            CO.save(cdir, merged, dirty=set(fresh), log=log)
        slots.extend(merged[k] for k in sorted(merged))

    log("mapgraph.train: walk %.1fs -- %d graphs built, %d reused from cache"
        % (time.time() - t_walk, built, reused))

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
    return {"examples": examples, "tally": tally, "runs": len(dbs) - len(skipped),
            "n_decisions": len(slots),
            "campaigns": sorted({e["campaign_id"] for e in examples})}


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
