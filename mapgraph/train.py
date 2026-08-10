from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(r"D:\tw_stack", "advisor"))
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

try:
    from mapgraph import schema as S
    from mapgraph import build as B
except ImportError:
    import schema as S
    import build as B

THREADS_TRAIN = max(1, os.cpu_count() or 16)

# batch/lr are sized for the gpu, not for accuracy: across batch 128..2048 the held-out
# rmse moved less than the run-to-run noise (sd ~0.06, and the batch-32 baseline itself
# spanned 2.74..2.87), while fit time moved 10-20x. Larger batches want a larger lr.
CFG = {"hidden": 64, "lr": 4e-3, "weight_decay": 1e-4, "batch": 1024, "epochs": 400,
       "patience": 50, "grad_clip": 5.0, "aux_weight": 0.25, "seed": 0,
       "time_budget_s": 600, "device": "auto"}

_NF = {name: i for i, name in enumerate(S.NODE_FIELDS)}


def walk(runs_root=None, light=False, limit=None):
    import features as F
    from base_model import RUNS_ROOT, decision_deltas, target
    from store import DecisionStore, IncompatibleStore
    assert tuple(S.ACTION_TYPES) == tuple(F.ACTION_TYPES), \
        "mapgraph.schema.ACTION_TYPES drifted from features.ACTION_TYPES -- retrain would " \
        "mis-embed action types; sync the tuples"
    runs_root = runs_root or RUNS_ROOT
    dbs = sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")))
    decisions, series, eseries, skipped_dbs = [], {}, {}, []
    for db in dbs:
        run_dir = os.path.dirname(db)
        try:
            s = DecisionStore(run_dir, readonly=True)
        except IncompatibleStore as e:
            skipped_dbs.append(os.path.basename(run_dir))
            sys.stderr.write("mapgraph.train: skipping %s -> %s\n" % (run_dir, str(e)[:120]))
            continue
        try:
            with s.snapshot_read():
                for rec, taken, counted in s.labelled_decisions():
                    rec["_run_dir"] = run_dir
                    decisions.append((rec, taken, counted))
                for camp, turns in s.target_series().items():
                    series.setdefault(camp, {}).update(turns)
                for camp, ents in s.entity_series().items():
                    for k, tv in ents.items():
                        eseries.setdefault(camp, {}).setdefault(k, {}).update(tv)
        finally:
            s.close()

    act_idx, mov_idx, hist, counts = {}, {}, {}, {}
    examples = []
    first_sha = None
    tallies = {"no_regions": 0, "too_many_regions": 0, "no_label": 0, "no_graph": 0,
               "taken_offer_missing": 0}
    for rec, taken, was_counted in decisions:
        ik = (rec.get("campaign_id"), rec.get("turn"))
        act_idx[ik] = act_idx.get(ik, 0) + 1
        (rec.setdefault("campaign", {}))["act_index"] = act_idx[ik]
        rec["campaign"]["move_index"] = mov_idx.get(ik, 0) + 1
        if taken and taken[2] == "move":
            mov_idx[ik] = mov_idx.get(ik, 0) + 1
        h = hist.setdefault(rec.get("campaign_id"), [])
        cnt = counts.setdefault(rec.get("campaign_id"), {})
        F.stamp_prev_actions(rec["campaign"], h)
        F.stamp_action_counts(rec["campaign"], cnt)
        if taken and taken[2] != "noop":
            h.append(taken[2])
            del h[:-F.PREV_ACTIONS]
            F.bump_action_counts(cnt, taken[2])

        regions = (rec.get("world") or {}).get("regions") or []
        if not regions:
            tallies["no_regions"] += 1
            continue
        if len(regions) > S.MAX_REGIONS:
            tallies["too_many_regions"] += 1
        turns = series.get(rec.get("campaign_id")) or {}
        y = target(decision_deltas(rec.get("campaign"), turns, rec.get("turn")))
        if y is None:
            tallies["no_label"] += 1
            continue
        offer_row = None
        for e in rec.get("entities") or []:
            if (e.get("context_kind"), str(e.get("context_id"))) != (taken[0], taken[1]):
                continue
            for o in e.get("offers") or []:
                if (o.get("action_type"), str(o.get("key"))) == (taken[2], taken[3]):
                    offer_row = o
                    break
            break
        if offer_row is None:
            tallies["taken_offer_missing"] += 1
            continue
        g = B.build_graph(rec)
        if g is None:
            tallies["no_graph"] += 1
            continue
        bound = B.bind_offer(g, taken[0], taken[1], taken[2], taken[3],
                             offer_row.get("params"), offer_row.get("available", True))
        node_deltas = _node_deltas(g, eseries.get(rec.get("campaign_id")) or {},
                                   int(rec.get("turn") or 0))
        if not examples:
            first_sha = hashlib.sha1(json.dumps(g.x).encode()).hexdigest()
        examples.append({"g": None if light else g, "counts": g.counts, "bound": bound,
                         "y": float(y), "node_deltas": node_deltas,
                         "campaign_id": rec.get("campaign_id"),
                         "src": (rec.get("_run_dir"), rec.get("decision_id")),
                         "act_index": rec["campaign"].get("act_index"),
                         "turn": rec.get("turn")})
        if limit and len(examples) >= limit:
            break

    dists = {}
    for ex in examples:
        for _, kind, delta in ex["node_deltas"]:
            dists.setdefault(kind, []).append(delta)
    for k in dists:
        dists[k].sort()
    return {"examples": examples, "dists": dists, "tallies": tallies,
            "runs": len(dbs) - len(skipped_dbs), "skipped_dbs": skipped_dbs,
            "n_decisions": len(decisions), "graph0_x_sha1": first_sha,
            "campaigns": sorted({ex["campaign_id"] for ex in examples})}


def _node_deltas(g, ents, turn):
    out = []
    for idx, meta in enumerate(g.node_meta):
        if meta.get("kind") == "character" and meta.get("own_kind"):
            kind = meta["own_kind"]
            turns = ents.get((kind, meta["cqi"]))
            if not turns:
                continue
            base = g.x[idx][_NF["rank"]] * 10.0
            vals = [turns[t] for t in turns if t >= turn and turns[t] is not None]
            if vals:
                out.append((idx, kind, max(vals) - base))
        elif meta.get("kind") == "settlement" and meta.get("own_snap"):
            turns = ents.get(("province", meta["key"]))
            if not turns:
                continue
            prior = [t for t in turns if t < turn and turns[t] is not None]
            if not prior:
                continue
            base = turns[max(prior)]
            vals = [turns[t] for t in turns if t >= turn and turns[t] is not None]
            if vals:
                out.append((idx, "province", max(vals) - base))
    return out


def _pct(v, sample):
    n = len(sample)
    if n == 0:
        return 0.5
    import bisect
    lo = bisect.bisect_left(sample, v)
    hi = bisect.bisect_right(sample, v)
    return (lo + 0.5 * (hi - lo)) / n


def _tensorize(examples, dists):
    try:
        from mapgraph import net as N
    except ImportError:
        import net as N
    datas = []
    for ex in examples:
        g = ex["g"]
        node_y = [0.0] * len(g.x)
        node_m = [0.0] * len(g.x)
        for idx, kind, delta in ex["node_deltas"]:
            node_y[idx] = _pct(delta, dists.get(kind) or [])
            node_m[idx] = 1.0
        d = N.to_data(g, [ex["bound"]], y=ex["y"], node_y=node_y, node_y_mask=node_m)
        datas.append(d)
        ex["g"] = None
    return datas


def _resolve_device(cfg, log):
    import torch
    want = str(cfg.get("device") or "auto")
    if want == "cpu":
        return torch.device("cpu")
    try:
        if torch.cuda.is_available():
            log("mapgraph.train: device cuda (%s)" % torch.cuda.get_device_name(0))
            return torch.device("cuda")
    except Exception as e:
        log("mapgraph.train: cuda probe failed -> %s" % repr(e)[:120])
    if want == "cuda":
        raise RuntimeError("mapgraph.train: device=cuda requested but CUDA is unavailable")
    log("mapgraph.train: device cpu (CUDA unavailable in this torch build)")
    return torch.device("cpu")


def _collate(items, size, dev, log):
    """Collate once, up front, and keep the batches on the training device.

    The corpus is ~10k single-offer graphs of a few hundred nodes each. Re-collating
    it in Python every epoch cost more than the forward/backward it fed, so the GPU
    sat idle waiting on `Batch.from_data_list`. Collating once and leaving the result
    resident removes both that work and the per-step host->device copy.
    """
    import torch
    from torch_geometric.data import Batch
    batches = [Batch.from_data_list(items[k:k + size]) for k in range(0, len(items), size)]
    if dev.type != "cuda":
        return batches, False
    need = sum(v.numel() * v.element_size() for b in batches
               for v in b.to_dict().values() if torch.is_tensor(v))
    free = torch.cuda.mem_get_info()[0]
    # the corpus grows every retrain; leave half of free VRAM for activations and grads
    if need >= free * 0.5:
        log("mapgraph.train: corpus %.1fGB vs %.1fGB free -- streaming batches per step"
            % (need / 1e9, free / 1e9))
        return batches, False
    try:
        return [b.to(dev) for b in batches], True
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        log("mapgraph.train: corpus did not fit VRAM -- streaming batches per step")
        return batches, False


def _fit(datas, ys, groups, cfg, log=print):
    import torch
    from base_model import grouped_split
    try:
        from mapgraph import net as N
    except ImportError:
        import net as N
    torch.set_num_threads(THREADS_TRAIN)
    torch.manual_seed(cfg["seed"])
    torch.set_float32_matmul_precision("high")
    dev = _resolve_device(cfg, log)
    val_idx, trn_idx = grouped_split(len(datas), groups)
    y_trn = [ys[i] for i in trn_idx]
    y_mean = sum(y_trn) / len(y_trn)
    y_sd = (sum((v - y_mean) ** 2 for v in y_trn) / max(1, len(y_trn) - 1)) ** 0.5
    if y_sd < 1e-6:
        y_sd = 1.0
    for d in datas:
        d.y_z = (d.y - y_mean) / y_sd

    net = N.Net(hidden=cfg["hidden"]).to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    gen = torch.Generator().manual_seed(cfg["seed"])
    trn = [datas[i] for i in trn_idx]
    val = [datas[i] for i in val_idx]
    # one seeded shuffle fixes batch membership; epochs then shuffle the batch order
    trn = [trn[i] for i in torch.randperm(len(trn), generator=gen).tolist()]
    loader, resident = _collate(trn, cfg["batch"], dev, log)
    vloader, _ = _collate(val, cfg["batch"], dev, log) if val else ([], False)
    mse = torch.nn.MSELoss()

    def _loss(batch):
        out = net(batch)
        yz = batch.y_z[out["offer_graph"]]
        lq = mse(out["q"], yz)
        lv = mse(out["v"], yz)
        m = batch.node_y_mask
        aux = ((net.head.node_scores(out["h"]) - batch.node_y) ** 2 * m).sum() \
            / m.sum().clamp(min=1.0)
        return lq + lv + cfg["aux_weight"] * aux, lq

    best, best_state, bad, epochs_run, stopped_by = None, None, 0, 0, "epochs"
    t0 = time.time()
    epoch = 0
    while epoch < cfg["epochs"]:
        epochs_run = epoch + 1
        try:
            net.train()
            for i in torch.randperm(len(loader), generator=gen).tolist():
                batch = loader[i] if resident else loader[i].to(dev)
                opt.zero_grad(set_to_none=True)
                loss, _ = _loss(batch)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), cfg["grad_clip"])
                opt.step()
            if vloader:
                net.eval()
                tot = torch.zeros((), device=dev)
                n = 0
                with torch.no_grad():
                    for batch in vloader:
                        b = batch if resident else batch.to(dev)
                        _, lq = _loss(b)
                        tot += lq.detach() * b.num_graphs  # stays on device: no per-batch sync
                        n += b.num_graphs
                score = float(tot) / max(n, 1.0)
                if best is None or score < best - 1e-5:
                    best, bad = score, 0
                    best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
                else:
                    bad += 1
                    if bad >= cfg["patience"]:
                        stopped_by = "patience"
                        break
        except torch.cuda.OutOfMemoryError as e:
            if dev.type != "cuda":
                raise
            log("mapgraph.train: CUDA OOM at epoch %d -> falling back to cpu and continuing: %s"
                % (epochs_run, repr(e)[:120]))
            dev = torch.device("cpu")
            net = net.cpu()
            loader = [b.cpu() for b in loader]
            vloader = [b.cpu() for b in vloader]
            resident = True
            best_state = {k: v.cpu() for k, v in (best_state or {}).items()} or None
            torch.cuda.empty_cache()
            continue
        if time.time() - t0 > cfg["time_budget_s"]:
            stopped_by = "time_budget"
            break
        epoch += 1
    net = net.cpu()
    if best_state is not None:
        net.load_state_dict(best_state)
    fit = {"val_mse_z": round(best, 5) if best is not None else None,
           "val_rmse_raw": round((best ** 0.5) * y_sd, 5) if best is not None else None,
           "epochs_run": epochs_run, "stopped_by": stopped_by,
           "val_rows": len(val_idx), "train_rows": len(trn_idx),
           "device": dev.type,
           "seconds": round(time.time() - t0, 1)}
    return net, fit, y_mean, y_sd


def train(runs_root=None, cfg=None, log=None):
    log = log or (lambda s: sys.stderr.write(str(s) + "\n"))
    cfg = dict(CFG, **(cfg or {}))
    t0 = time.time()
    w = walk(runs_root)
    examples = w["examples"]
    if len(examples) < S.MIN_ROWS:
        return {"trained": False, "rows": len(examples), "need": S.MIN_ROWS,
                "runs": w["runs"], "n_decisions": w["n_decisions"],
                "campaigns": len(w["campaigns"])}
    datas = _tensorize(examples, w["dists"])
    ys = [ex["y"] for ex in examples]
    groups = [ex["campaign_id"] for ex in examples]
    net, fit, y_mean, y_sd = _fit(datas, ys, groups, cfg, log=log)
    meta = {"backend": "gnn", "cfg": cfg, "rows": len(datas),
            "campaigns": sorted(set(groups)), "fit": fit,
            "y_mean": round(y_mean, 6), "y_sd": round(y_sd, 6),
            "schema_version": S.SCHEMA_VERSION, "schema_hash": S.schema_hash(),
            "node_fields": list(S.NODE_FIELDS), "edge_fields": list(S.EDGE_FIELDS),
            "g_ctx_fields": list(S.G_CTX_FIELDS), "offer_fields": list(S.OFFER_FIELDS),
            "num": list(S.NODE_FIELDS) + list(S.G_CTX_FIELDS) + list(S.OFFER_FIELDS),
            "cat": ["race", "stance", "action_type", "action_key"],
            "vocabs": {"races": list(S.RACES), "action_types": list(S.ACTION_TYPES),
                       "stance_buckets": S.STANCE_BUCKETS, "key_buckets": S.KEY_BUCKETS},
            "corpus_gate": {"require_regions": True, "max_regions": S.MAX_REGIONS},
            "aux": {"n_labelled_nodes": sum(len(ex["node_deltas"]) for ex in examples)},
            "tallies": w["tallies"],
            "target": "gain(future_max - decision_snapshot), z-scored; see base_model"}
    import torch
    stage = S.MODEL_DIR + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    os.makedirs(S.MODEL_DIR, exist_ok=True)
    torch.save(net.encoder.state_dict(), os.path.join(stage, "encoder.pt"))
    torch.save(net.head.state_dict(), os.path.join(stage, "head.pt"))
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"))
    for name in ("encoder.pt", "head.pt", "meta.json"):
        os.replace(os.path.join(stage, name), os.path.join(S.MODEL_DIR, name))
    shutil.rmtree(stage, ignore_errors=True)
    return {"trained": True, "backend": "gnn", "rows": len(datas), "fit": fit,
            "runs": w["runs"], "n_decisions": w["n_decisions"],
            "campaigns": len(set(groups)), "aux_nodes": meta["aux"]["n_labelled_nodes"],
            "walk_seconds": round(time.time() - t0 - fit["seconds"], 1)}


def _report(runs_root=None):
    w = walk(runs_root, light=True)
    ex = w["examples"]
    nodes = sorted(e["counts"]["nodes"] for e in ex)
    edges = sorted(e["counts"]["edges"] for e in ex)
    ys = [e["y"] for e in ex]

    def _q(vals, p):
        return vals[int(p * (len(vals) - 1))] if vals else None

    print(json.dumps({
        "n_graphs": len(ex), "campaigns": len(w["campaigns"]), "runs": w["runs"],
        "n_decisions_walked": w["n_decisions"], "tallies": w["tallies"],
        "nodes": {"min": _q(nodes, 0), "p50": _q(nodes, 0.5), "p95": _q(nodes, 0.95),
                  "max": _q(nodes, 1.0)},
        "edges": {"min": _q(edges, 0), "p50": _q(edges, 0.5), "p95": _q(edges, 0.95),
                  "max": _q(edges, 1.0)},
        "y": {"mean": round(sum(ys) / max(1, len(ys)), 4),
              "min": round(min(ys), 3) if ys else None,
              "max": round(max(ys), 3) if ys else None},
        "aux_labelled_nodes": sum(len(e["node_deltas"]) for e in ex),
        "aux_dists": {k: len(v) for k, v in w["dists"].items()},
        "schema_hash": S.schema_hash(), "graph0_x_sha1": w["graph0_x_sha1"],
    }, indent=2))


def _overfit(limit=10):
    import torch
    w = walk(limit=limit)
    ex = w["examples"]
    if not ex:
        raise SystemExit("overfit: no examples")
    datas = _tensorize(ex, w["dists"])
    ys = [e["y"] for e in ex]
    cfg = dict(CFG, epochs=500, patience=10 ** 9, time_budget_s=10 ** 9)
    import statistics
    net, fit, y_mean, y_sd = _fit(datas, ys, ["only_one_campaign_group"] * len(datas), cfg)
    from torch_geometric.loader import DataLoader
    net.eval()
    qs, vs, zs = [], [], []
    with torch.no_grad():
        for batch in DataLoader(datas, batch_size=len(datas)):
            out = net(batch)
            qs = out["q"].tolist()
            vs = out["v"].tolist()
            zs = batch.y_z[out["offer_graph"]].tolist()
    mse = sum((q - z) ** 2 for q, z in zip(qs, zs)) / len(qs)
    spread = statistics.pstdev([q - v for q, v in zip(qs, vs)])
    print(json.dumps({"n": len(datas), "final_train_mse_z": round(mse, 5),
                      "impact_spread": round(spread, 5), "epochs": fit["epochs_run"]}))
    if mse >= 0.01:
        raise SystemExit("overfit FAILED: mse_z %.5f >= 0.01 -- plumbing suspect" % mse)
    if spread <= 1e-4:
        raise SystemExit("overfit FAILED: impact spread %.6f ~ 0 -- twin heads collapsed" % spread)
    print("overfit OK")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        _report(sys.argv[2] if len(sys.argv) > 2 else None)
    elif len(sys.argv) > 1 and sys.argv[1] == "overfit":
        n = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 10
        _overfit(n)
    elif len(sys.argv) > 1 and sys.argv[1] == "train":
        print(json.dumps(train(), indent=2, default=str))
    else:
        raise SystemExit("usage: train.py report [runs_root] | overfit [--limit N] | train")
