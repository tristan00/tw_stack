from __future__ import annotations


import json
import os
import shutil
import sys
import time

from advisor.mapgraph import schema as S
from advisor.mapgraph import graph_config as GC
from advisor.mapgraph import train as T

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

MODEL_DIR = common.MODEL_MAPGRAPH_GREEDY

CFG = dict(T.CFG)
MIN_ROWS = S.MIN_ROWS


def prepare(datas, ys, groups, cfg, log=print, norm=None, free_datas=False):
    import numpy as np
    import torch
    from base_model import stable_split
    from advisor.mapgraph import net as N

    started = time.perf_counter()
    torch.set_num_threads(int(cfg.get("threads") or T.THREADS))
    dev = T._device(cfg, log)
    val_idx, trn_idx = stable_split(len(datas), groups)
    y_trn = [ys[i] for i in trn_idx]
    y_mean = sum(y_trn) / max(1, len(y_trn))
    y_sd = (sum((v - y_mean) ** 2 for v in y_trn) / max(1, len(y_trn) - 1)) ** 0.5 or 1.0
    normalized_y = ((np.asarray(ys, dtype=np.float32) - np.float32(y_mean))
                    / np.float32(y_sd))
    for i, d in enumerate(datas):
        d.y_z = torch.from_numpy(normalized_y[i:i + 1])

    if norm is None:
        t_n = time.time()
        norm = N.norm_stats([datas[i] for i in trn_idx], log=log)
        log("mapgraph.greedy_train: norm stats %.1fs" % (time.time() - t_n))

    gen = torch.Generator().manual_seed(cfg["seed"])
    order0 = torch.randperm(len(trn_idx), generator=gen).tolist()
    trn = [datas[trn_idx[i]] for i in order0]
    val = [datas[i] for i in val_idx]
    if free_datas:
        datas.clear()
        loader, vloader = T._collate_partitions((trn, val), cfg["batch"], dev, log)
    else:
        loader = T._collate(trn, cfg["batch"], dev, log, "greedy train")
        vloader = T._collate(val, cfg["batch"], dev, log, "greedy val") if val_idx else []
    del trn, val

    val_var = None
    if vloader:
        yv = torch.cat([b.y_z for b in vloader])
        val_var = float(yv.var(unbiased=False)) or 1.0
    log("mapgraph.greedy_train: prepare exit %.1fs" % (time.perf_counter() - started))
    return {"batch": cfg["batch"], "seed": cfg["seed"], "norm": norm, "loader": loader,
            "vloader": vloader, "val_var": val_var, "y_mean": y_mean, "y_sd": y_sd,
            "train_rows": len(trn_idx), "val_rows": len(val_idx)}


def fit_net(datas, ys, groups, cfg, log=print, on_epoch=None, free_datas=False,
            prep=None, trial_started=None, on_first_step=None):
    import torch
    from advisor.mapgraph import greedy_net as GN

    torch.set_num_threads(int(cfg.get("threads") or T.THREADS))
    torch.manual_seed(cfg["seed"])
    torch.set_float32_matmul_precision("high")
    dev = T._device(cfg, log)
    if prep is None:
        prep = prepare(datas, ys, groups, cfg, log=log, free_datas=free_datas)
    elif (prep["batch"], prep["seed"]) != (cfg["batch"], cfg["seed"]):
        raise RuntimeError("mapgraph.greedy_train: prepared batch/seed %r does not match "
                           "cfg %r" % ((prep["batch"], prep["seed"]),
                                       (cfg["batch"], cfg["seed"])))
    loader, vloader, val_var = prep["loader"], prep["vloader"], prep["val_var"]
    y_mean, y_sd = prep["y_mean"], prep["y_sd"]

    net = GN.from_cfg(cfg).to(dev)
    net.encoder.type_enc.load_norm(prep["norm"])
    opt = torch.optim.AdamW(net.parameters(), lr=cfg["lr"],
                            weight_decay=cfg["weight_decay"],
                            fused=(dev.type == "cuda"))
    gen = torch.Generator().manual_seed(cfg["seed"])
    amp = (dev.type == "cuda") and bool(cfg.get("bf16", True))

    def step(b):
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp):
            out = net(b)
        n = int(b.n_actions.numel())
        q = GN.taken_q(out["q"].float(), out["action_graph"], b.is_taken, n)
        return torch.nn.functional.mse_loss(q, b.y_z, reduction="none")

    best, best_state, bad, stopped = None, None, 0, "time_budget"
    out_of_time = False
    curve = []
    val_s = None
    VAL_S_GUESS = 6.0
    t0 = time.time()
    first_step_seconds = None
    epoch = -1
    while True:
        epoch += 1
        net.train()
        t_ep = time.time()
        for i in torch.randperm(len(loader), generator=gen).tolist():
            opt.zero_grad(set_to_none=True)
            loss = step(loader[i]).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), cfg["grad_clip"])
            opt.step()
            if first_step_seconds is None:
                if dev.type == "cuda":
                    torch.cuda.synchronize()
                first_step_seconds = time.time() - (trial_started if trial_started is not None else t0)
                log("mapgraph.greedy_train: first GPU training step complete %.3fs since %s"
                    % (first_step_seconds, "trial start" if trial_started is not None else "fit start"))
                if on_first_step is not None:
                    on_first_step(first_step_seconds)
            if time.time() - t0 > cfg["time_budget_s"] - (val_s or VAL_S_GUESS):
                stopped = "time_budget"
                out_of_time = True
                break
            if cfg.get("epoch_cap_s") and time.time() - t_ep > cfg["epoch_cap_s"]:
                raise RuntimeError("epoch %d exceeded the %.0fs epoch cap"
                                   % (epoch + 1, cfg["epoch_cap_s"]))
        if vloader:
            net.eval()
            t_val = time.time()
            tot = torch.zeros((), device=dev)
            n = 0
            with torch.no_grad():
                for b in vloader:
                    se = step(b)
                    tot += se.detach().sum()
                    n += int(se.numel())
            score = float(tot) / max(n, 1)
            val_s = max(val_s or 0.0, time.time() - t_val)
            curve.append([epoch + 1, round(time.time() - t0, 1), round(score, 5)])
            improved = best is None or score < best - 1e-5
            if improved:
                best, bad = score, 0
                best_state = {k: v.detach().cpu().clone()
                              for k, v in net.state_dict().items()}
            else:
                bad += 1
            log("mapgraph.greedy_train: epoch %3d  val_mse %.4f  best %.4f  r2 %+.3f  %s  %.0fs"
                % (epoch + 1, score, best, 1.0 - best / (val_var or 1.0),
                   "*" if improved else " ", time.time() - t0))
            if on_epoch is not None:
                on_epoch(epoch + 1, score)
            if bad >= max(1, cfg["patience"]):
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
    fit = {"val_mse": round(best, 5) if best is not None else None,
           "val_r2": (round(1.0 - best / (val_var or 1.0), 5) if best is not None else None),
           "val_var": round(val_var, 5) if val_var is not None else None,
           "epochs_run": epoch + 1, "stopped_by": stopped, "device": dev.type,
           "val_rows": prep["val_rows"], "train_rows": prep["train_rows"],
           "curve": curve,
           "seconds": round(time.time() - t0, 1),
           "first_step_seconds": round(first_step_seconds, 3) if first_step_seconds is not None else None}
    return net, fit, y_mean, y_sd


def _save(model_dir, net, meta):
    import torch
    stage = model_dir + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    os.makedirs(model_dir, exist_ok=True)
    torch.save({"encoder": net.encoder.state_dict(), "head": net.head.state_dict(),
                "meta": meta}, os.path.join(stage, "model.pt"))
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"))
    os.replace(os.path.join(stage, "model.pt"), os.path.join(model_dir, "model.pt"))
    os.replace(os.path.join(stage, "meta.json"), os.path.join(model_dir, "meta.json"))
    shutil.rmtree(stage, ignore_errors=True)


def _meta(backend, cfg, ex, fit, y_mean, y_sd, tally, graph_config=None,
          graph_metrics=None):
    graph_config = GC.from_dict(graph_config)
    return {"backend": backend, "schema_version": S.SCHEMA_VERSION,
            "schema_hash": S.schema_hash(), "cfg": cfg, "rows": len(ex),
            "graph_config": graph_config.as_dict(),
            "graph_fingerprint": graph_config.fingerprint(),
            "graph_metrics": graph_metrics,
            "campaigns": sorted({e["campaign_id"] for e in ex}), "fit": fit,
            "y_mean": round(y_mean, 6), "y_sd": round(y_sd, 6),
            "n_scalars": S.N_SCALARS, "node_types": list(S.NODE_TYPES),
            "relations": list(S.RELATIONS), "tally": tally}


def _budget(cfg, walked, log):
    fit_cfg = dict(cfg, time_budget_s=max(T.MIN_FIT_S, cfg["time_budget_s"] - walked))
    log("mapgraph.greedy_train: walk+tensorize %.1fs, %.1fs left of the %ds budget for "
        "the fit" % (walked, fit_cfg["time_budget_s"], cfg["time_budget_s"]))
    return fit_cfg


def train(runs_root=None, cfg=None, log=None, model_dir=MODEL_DIR, limit=None,
          graph_config=None):
    log = log or (lambda s: sys.stderr.write(str(s) + "\n"))
    cfg = dict(CFG, **(cfg or {}))
    t0 = time.time()
    graph_config = GC.from_dict(graph_config)
    w = T.walk(runs_root, graph_config=graph_config, limit=limit, log=log)
    ex = w["examples"]
    if len(ex) < MIN_ROWS:
        return {"trained": False, "backend": "mapgraph_greedy", "rows": len(ex),
                "need": MIN_ROWS, "tally": w["tally"], "n_decisions": w["n_decisions"]}
    datas = T._tensorize(ex)
    for e in ex:
        e["data"] = None
    n_rows = len(datas)
    fit_cfg = _budget(cfg, time.time() - t0, log)
    net, fit, y_mean, y_sd = fit_net(datas, [e["y"] for e in ex],
                                     [e["campaign_id"] for e in ex], fit_cfg, log=log,
                                     free_datas=True)
    if fit["device"] == "cuda":
        import torch
        torch.cuda.empty_cache()
    meta = _meta("mapgraph_greedy", dict(fit_cfg, time_budget_s=cfg["time_budget_s"]),
                 ex, fit, y_mean, y_sd, w["tally"], graph_config, w.get("metrics"))
    _save(model_dir, net, meta)
    return {"trained": True, "backend": "mapgraph_greedy", "rows": n_rows, "fit": fit,
            "campaigns": len(meta["campaigns"]), "tally": w["tally"],
            "model_dir": model_dir,
            "walk_seconds": round(time.time() - t0 - fit["seconds"], 1)}


_OVERRIDES = {"--budget": ("time_budget_s", int), "--batch": ("batch", int),
              "--patience": ("patience", int),
              "--device": ("device", str), "--threads": ("threads", int),
              "--hidden": ("hidden", int), "--entity-layers": ("entity_layers", int),
              "--action-rounds": ("action_rounds", int), "--lr": ("lr", float)}


def _cli(a):
    over = {}
    for flag, (key, cast) in _OVERRIDES.items():
        if flag in a:
            over[key] = cast(a[a.index(flag) + 1])
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else None
    out = a[a.index("--out") + 1] if "--out" in a else None
    graph_config = (GC.from_dict(json.loads(a[a.index("--graph-config") + 1]))
                    if "--graph-config" in a else GC.DEFAULT)
    return over, limit, out, graph_config


if __name__ == "__main__":
    common.require_venv()
    a = sys.argv[1:]
    cmd = a[0] if a else ""

    def _log(s):
        sys.stdout.write(str(s) + "\n")
        sys.stdout.flush()

    if cmd == "train":
        over, limit, out, graph_config = _cli(a)
        print(json.dumps(train(cfg=over or None, log=_log, model_dir=out or MODEL_DIR,
                               limit=limit, graph_config=graph_config), indent=2,
                         default=str))
    elif cmd == "report":
        w = T.walk(limit=200)
        print(json.dumps({"examples": len(w["examples"]), "tally": w["tally"],
                          "campaigns": len(w["campaigns"]), "need": MIN_ROWS,
                          "schema_hash": S.schema_hash(), "cfg": CFG}, indent=2))
    else:
        raise SystemExit(
            "usage: greedy_train.py train [--budget S] [--batch N] [--epochs N] "
            "[--device cuda|cpu] [--limit N] [--out DIR] "
            "[--graph-config JSON] | report")
