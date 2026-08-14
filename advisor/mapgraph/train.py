from __future__ import annotations


import glob
import json
import os
import shutil
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

CFG = {"hidden": 256, "entity_layers": 1, "action_rounds": 3,
       "map_aggr": "max", "act_aggr": "add+mean", "attn": "act",
       "conv": "sage", "conv_map": None, "conv_a2e": None, "conv_e2a": "rel",
       "dst_dim": 48, "update": "linear", "self_transform": False,
       "lr": 3.501e-4, "weight_decay": 4.320e-3, "batch": 384, "epochs": 24, "patience": 4,
       "grad_clip": 5.0, "adv_tau": 1.0, "adv_clip": 20.0, "value_weight": 0.1351, "bf16": True,
       "seed": 0, "time_budget_s": 300, "device": "cuda"}

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
    finally:
        st.close()

    out = []
    for rec, taken, counted in rows:
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


def walk(runs_root=None, limit=None, log=print, workers=None):
    import concurrent.futures as cf
    import torch
    from base_model import RUNS_ROOT, decision_deltas, target
    from store import DecisionStore, IncompatibleStore
    runs_root = runs_root or RUNS_ROOT
    dbs = common.run_dbs(runs_root)

    try:
        from advisor.mapgraph import corpus as CO
    except ImportError:
        import corpus as CO

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
            live.append((db, st.max_decision_id(), st.taken_map()))
        finally:
            st.close()

    n_workers = 1 if workers is None else workers
    t_walk = time.time()
    slots, built, reused = [], 0, 0
    for db, hi, taken_now in live:
        run_key = os.path.basename(os.path.dirname(db))
        cached, cdir = ({}, None) if limit else CO.load(run_key, log=log)

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
                jobs = [(db, None, None)]
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
        y = target(decision_deltas(campaign, series.get(camp_id) or {}, turn))
        if y is None:
            tally["no_label"] += 1
            continue
        if drop is not None:
            tally[drop] += 1
            continue
        data.y = torch.tensor([float(y)], dtype=torch.float32)
        examples.append({"data": data, "y": float(y), "campaign_id": camp_id,
                         "counts": counts})
        if limit and len(examples) >= limit:
            break
    return {"examples": examples, "tally": tally, "runs": len(dbs) - len(skipped),
            "n_decisions": len(slots),
            "campaigns": sorted({e["campaign_id"] for e in examples})}


def _tensorize(examples):
    return [ex["data"] for ex in examples]


def _device(cfg, log):
    import torch
    want = str(cfg.get("device") or "auto")
    if want != "cpu" and torch.cuda.is_available():
        log("mapgraph.train: device cuda (%s)" % torch.cuda.get_device_name(0))
        return torch.device("cuda")
    if want == "cuda":
        raise RuntimeError("mapgraph.train: device=cuda requested, CUDA unavailable")
    log("mapgraph.train: device cpu")
    return torch.device("cpu")


def _corpus_bytes(batches):
    import torch
    return sum(v.numel() * v.element_size() for b in batches
               for v in b.to_dict().values() if torch.is_tensor(v))


def _collate(items, size, dev, log, tag):
    import torch
    from torch_geometric.data import Batch
    batches = [Batch.from_data_list(items[k:k + size])
               for k in range(0, len(items), size)]
    if dev.type != "cuda" or not batches:
        return batches, False
    need, free = _corpus_bytes(batches), torch.cuda.mem_get_info()[0]
    if need >= free * 0.5:
        log("mapgraph.train: %s corpus %.2fGB vs %.2fGB free -- streaming batches"
            % (tag, need / 1e9, free / 1e9))
        return batches, False
    try:
        out = [b.to(dev, non_blocking=True) for b in batches]
        torch.cuda.synchronize()
        log("mapgraph.train: %s %d batches resident on gpu (%.2fGB of %.2fGB free)"
            % (tag, len(out), need / 1e9, free / 1e9))
        return out, True
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        log("mapgraph.train: %s did not fit VRAM -- streaming batches" % tag)
        return batches, False


def fit_net(datas, ys, groups, cfg, log=print):
    import torch
    from base_model import stable_split
    try:
        from advisor.mapgraph import net as N
    except ImportError:
        import net as N

    torch.set_num_threads(THREADS)
    torch.manual_seed(cfg["seed"])
    torch.set_float32_matmul_precision("high")
    dev = _device(cfg, log)
    val_idx, trn_idx = stable_split(len(datas), groups)
    y_trn = [ys[i] for i in trn_idx]
    y_mean = sum(y_trn) / max(1, len(y_trn))
    y_sd = (sum((v - y_mean) ** 2 for v in y_trn) / max(1, len(y_trn) - 1)) ** 0.5 or 1.0
    for d in datas:
        d.y_z = (d.y - y_mean) / y_sd

    net = N.from_cfg(cfg).to(dev)
    net.encoder.type_enc.fit_norm([datas[i] for i in trn_idx], log=log)
    opt = torch.optim.AdamW(net.parameters(), lr=cfg["lr"],
                            weight_decay=cfg["weight_decay"],
                            fused=(dev.type == "cuda"))
    gen = torch.Generator().manual_seed(cfg["seed"])

    order0 = torch.randperm(len(trn_idx), generator=gen).tolist()
    trn = [datas[trn_idx[i]] for i in order0]
    loader, resident = _collate(trn, cfg["batch"], dev, log, "train")
    vloader, v_resident = (_collate([datas[i] for i in val_idx], cfg["batch"], dev, log,
                                    "val") if val_idx else ([], False))

    amp = (dev.type == "cuda") and bool(cfg.get("bf16", True))

    def step(b, train):
        if b.x.device != dev:
            b = b.to(dev, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp):
            out = net(b)
        q = out["q"].float()
        v = out["v"].float()
        n = int(b.n_actions.numel())
        nll = N.listwise_nll(q, out["action_graph"], b.is_taken, n)
        vloss = torch.nn.functional.mse_loss(v, b.y_z)
        adv = (b.y_z - v.detach()) / cfg["adv_tau"]
        w = torch.exp(adv.clamp(max=3.0)).clamp(max=cfg["adv_clip"])
        rank = (nll * w).mean()
        return rank + cfg["value_weight"] * vloss, nll.mean(), vloss

    best, best_state, bad, stopped = None, None, 0, "epochs"
    out_of_time = False
    best_v, curve = None, []
    val_s = None
    VAL_S_GUESS = 6.0
    t0 = time.time()
    for epoch in range(cfg["epochs"]):
        net.train()
        for i in torch.randperm(len(loader), generator=gen).tolist():
            opt.zero_grad(set_to_none=True)
            loss, _, _ = step(loader[i], True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), cfg["grad_clip"])
            opt.step()
            if time.time() - t0 > cfg["time_budget_s"] - (val_s or VAL_S_GUESS):
                stopped = "time_budget"
                out_of_time = True
                break
        if vloader:
            net.eval()
            t_val = time.time()
            tot_n = torch.zeros((), device=dev)
            tot_v = torch.zeros((), device=dev)
            n = 0
            with torch.no_grad():
                for b in vloader:
                    _, nll, vl = step(b, False)
                    k = int(b.n_actions.numel())
                    tot_n += nll.detach() * k
                    tot_v += vl.detach() * k
                    n += k
            score = float(tot_n) / max(n, 1)
            val_s = max(val_s or 0.0, time.time() - t_val)
            curve.append([epoch + 1, round(time.time() - t0, 1), round(score, 5)])
            improved = best is None or score < best - 1e-5
            if improved:
                best, bad = score, 0
                best_v = float(tot_v) / max(n, 1)
                best_state = {k: v.detach().cpu().clone()
                              for k, v in net.state_dict().items()}
            else:
                bad += 1
            log("mapgraph.train: epoch %3d  val_nll %.4f  best %.4f  gate %s  %s  %.0fs"
                % (epoch + 1, score, best,
                   [round(float(v), 4) for v in net.encoder.a2e_gate],
                   "*" if improved else " ", time.time() - t0))
            if bad >= cfg["patience"]:
                stopped = "patience"
                break
        if out_of_time:
            break
        if time.time() - t0 > cfg["time_budget_s"]:
            stopped = "time_budget"
            break
    net = net.cpu()
    if best_state:
        net.load_state_dict(best_state)
    fit = {"val_listwise_nll": round(best, 5) if best is not None else None,
           "val_value_mse": round(best_v, 5) if best_v is not None else None,
           "epochs_run": epoch + 1, "stopped_by": stopped, "device": dev.type,
           "val_rows": len(val_idx), "train_rows": len(trn_idx),
           "resident": bool(resident and v_resident),
           "curve": curve,
           "seconds": round(time.time() - t0, 1)}
    return net, fit, y_mean, y_sd


def train(runs_root=None, cfg=None, log=None):
    log = log or (lambda s: sys.stderr.write(str(s) + "\n"))
    cfg = dict(CFG, **(cfg or {}))
    t0 = time.time()
    w = walk(runs_root, log=log)
    ex = w["examples"]
    if len(ex) < S.MIN_ROWS:
        return {"trained": False, "rows": len(ex), "need": S.MIN_ROWS,
                "tally": w["tally"], "n_decisions": w["n_decisions"]}
    datas = _tensorize(ex)
    walked = time.time() - t0
    fit_cfg = dict(cfg, time_budget_s=max(MIN_FIT_S, cfg["time_budget_s"] - walked))
    log("mapgraph.train: walk+tensorize %.1fs, %.1fs left of the %ds budget for the fit"
        % (walked, fit_cfg["time_budget_s"], cfg["time_budget_s"]))
    net, fit, y_mean, y_sd = fit_net(datas, [e["y"] for e in ex],
                                  [e["campaign_id"] for e in ex], fit_cfg, log=log)
    import torch
    meta = {"backend": "mapgraph", "schema_version": S.SCHEMA_VERSION,
            "schema_hash": S.schema_hash(),
            "cfg": dict(fit_cfg, time_budget_s=cfg["time_budget_s"]),
            "rows": len(datas),
            "campaigns": sorted({e["campaign_id"] for e in ex}), "fit": fit,
            "y_mean": round(y_mean, 6), "y_sd": round(y_sd, 6),
            "n_scalars": S.N_SCALARS, "node_types": list(S.NODE_TYPES),
            "relations": list(S.RELATIONS), "tally": w["tally"],
            "target": "base_model.target(decision_deltas(...)), z-scored -- identical "
                      "to the CatBoost target, unmodified",
            "loss": "advantage-weighted listwise NLL + MSE(v, y_z); no mse(q, y)"}
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
    return {"trained": True, "backend": "mapgraph", "rows": len(datas), "fit": fit,
            "campaigns": len(meta["campaigns"]), "tally": w["tally"],
            "walk_seconds": round(time.time() - t0 - fit["seconds"], 1)}


def _overfit(limit=8):
    import torch
    from torch_geometric.data import Batch
    try:
        from advisor.mapgraph import net as N
    except ImportError:
        import net as N
    w = walk(limit=limit)
    ex = w["examples"]
    if not ex:
        raise SystemExit("overfit: no examples (tally %s)" % w["tally"])
    datas = _tensorize(ex)
    net = N.Net()
    opt = torch.optim.AdamW(net.parameters(), lr=3e-3)
    b = Batch.from_data_list(datas)
    n = int(b.n_actions.numel())
    base = float(sum(float(torch.log(v.float())) for v in b.n_actions) / n)
    for i in range(300):
        opt.zero_grad(set_to_none=True)
        out = net(b)
        loss = N.listwise_nll(out["q"], out["action_graph"], b.is_taken, n).mean()
        loss.backward()
        opt.step()
    out = net(b)
    q = out["q"].detach()
    print(json.dumps({"n_graphs": n, "uniform_nll": round(base, 4),
                      "final_nll": round(float(loss), 4),
                      "q_std": round(float(q.std()), 4)}))
    if float(loss) >= base * 0.5:
        raise SystemExit("overfit FAILED: nll %.3f did not beat half of uniform %.3f"
                         % (float(loss), base))
    print("overfit OK")


if __name__ == "__main__":
    common.require_venv()
    a = sys.argv[1:]
    if a and a[0] == "overfit":
        _overfit(int(a[a.index("--limit") + 1]) if "--limit" in a else 8)
    elif a and a[0] == "report":
        w = walk(light=True) if False else walk(limit=200)
        print(json.dumps({"examples": len(w["examples"]), "tally": w["tally"],
                          "campaigns": len(w["campaigns"]),
                          "schema_hash": S.schema_hash()}, indent=2))
    elif a and a[0] == "train":
        def _log(s):
            sys.stdout.write(str(s) + "\n")
            sys.stdout.flush()
        over = {}
        for k, cast in (("--budget", int), ("--batch", int), ("--epochs", int)):
            if k in a:
                over[{"--budget": "time_budget_s", "--batch": "batch",
                      "--epochs": "epochs"}[k]] = cast(a[a.index(k) + 1])
        print(json.dumps(train(cfg=over or None, log=_log), indent=2, default=str))
    else:
        raise SystemExit("usage: train.py overfit [--limit N] | report | train")
