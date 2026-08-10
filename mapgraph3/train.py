from __future__ import annotations

"""v3 training.

The target is `base_model.target(decision_deltas(...))` -- byte for byte the same
quantity CatBoost is scored against. It is z-scored for the network output and
otherwise untouched. Same target is also the only reason the two models are comparable.

Two supervision channels, and they do different jobs:

  MSE(v, y_z)        the state value. y is a property of the campaign and turn, which is
                     exactly what a value head should predict.

  listwise NLL       -log p(taken action) under a softmax over that decision's candidate
                     set. This is the term v2 did not have at all: v2 broadcast one
                     outcome scalar onto every candidate and regressed them all to it, so
                     no loss term ever compared two candidates and the ranking was
                     unsupervised.

  advantage weight   exp((y_z - v)/tau), clipped. Upweights decisions from turns that
                     went better than the state predicted, which is what turns plain
                     imitation into a policy-improvement step (MARWIL / AWR).

There is deliberately no `mse(q, y_z)`. That term is what made v2's advantage a capacity
artifact: q and v regressed to the same scalar, so q-v was zero by construction and
non-zero only because v underfits.
"""

import glob
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(r"D:\tw_stack", "advisor"))
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

try:
    from mapgraph3 import schema as S
    from mapgraph3 import build as B
except ImportError:
    import schema as S
    import build as B

THREADS = max(1, os.cpu_count() or 8)

CFG = {"hidden": 192, "entity_layers": 2, "action_rounds": 2,
       "lr": 2e-3, "weight_decay": 1e-4, "batch": 16, "epochs": 200, "patience": 25,
       "grad_clip": 5.0, "adv_tau": 1.0, "adv_clip": 20.0, "value_weight": 1.0, "bf16": True,
       # In-session budget. session.py retrains between campaigns with the game shut
       # down, so every second here is a second the run is not collecting data --
       # 5 retrains x 100 campaigns. Pass a bigger budget explicitly for an offline fit.
       "seed": 0, "time_budget_s": 900, "device": "auto"}


def walk(runs_root=None, limit=None, log=print):
    """Corpus -> (graph, y, taken-mask) examples.

    Note what is NOT here: v2 called features.stamp_prev_actions and
    stamp_action_counts to write CatBoost history features into the record before
    building the graph. v3 does not. The only advisor code touched is the label.
    """
    from base_model import RUNS_ROOT, decision_deltas, target
    from store import DecisionStore, IncompatibleStore
    runs_root = runs_root or RUNS_ROOT
    dbs = sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")))
    decisions, series, skipped = [], {}, []
    for db in dbs:
        run_dir = os.path.dirname(db)
        try:
            st = DecisionStore(run_dir, readonly=True)
        except IncompatibleStore as e:
            skipped.append(os.path.basename(run_dir))
            log("mapgraph3.train: skipping %s -> %s" % (run_dir, str(e)[:100]))
            continue
        try:
            with st.snapshot_read():
                for rec, taken, counted in st.labelled_decisions():
                    decisions.append((rec, taken))
                for camp, turns in st.target_series().items():
                    series.setdefault(camp, {}).update(turns)
        finally:
            st.close()

    examples = []
    tally = {"no_graph": 0, "no_label": 0, "taken_missing": 0, "no_actions": 0}
    for rec, taken in decisions:
        turns = series.get(rec.get("campaign_id")) or {}
        y = target(decision_deltas(rec.get("campaign"), turns, rec.get("turn")))
        if y is None:
            tally["no_label"] += 1
            continue
        g = B.build_graph(rec)
        if g is None:
            tally["no_graph"] += 1
            continue
        if not g.action_nodes:
            tally["no_actions"] += 1
            continue
        want = (str(taken[0]), str(taken[1]), str(taken[2]), str(taken[3]))
        mask = [1.0 if k == want else 0.0 for k in g.action_keys]
        if sum(mask) != 1.0:
            # a softmax with no positive (or two) is undefined; drop rather than fudge
            tally["taken_missing"] += 1
            continue
        examples.append({"g": g, "y": float(y), "taken": mask,
                         "campaign_id": rec.get("campaign_id"),
                         "counts": g.counts})
        if limit and len(examples) >= limit:
            break
    return {"examples": examples, "tally": tally, "runs": len(dbs) - len(skipped),
            "n_decisions": len(decisions),
            "campaigns": sorted({e["campaign_id"] for e in examples})}


def _tensorize(examples):
    try:
        from mapgraph3 import net as N
    except ImportError:
        import net as N
    out = []
    for ex in examples:
        out.append(N.to_data(ex["g"], y=ex["y"], taken=ex["taken"]))
        ex["g"] = None
    return out


def _device(cfg, log):
    import torch
    want = str(cfg.get("device") or "auto")
    if want != "cpu" and torch.cuda.is_available():
        log("mapgraph3.train: device cuda (%s)" % torch.cuda.get_device_name(0))
        return torch.device("cuda")
    if want == "cuda":
        raise RuntimeError("mapgraph3.train: device=cuda requested, CUDA unavailable")
    log("mapgraph3.train: device cpu")
    return torch.device("cpu")


def _fit(datas, ys, groups, cfg, log=print):
    import torch
    from torch_geometric.data import Batch
    from base_model import grouped_split
    try:
        from mapgraph3 import net as N
    except ImportError:
        import net as N

    torch.set_num_threads(THREADS)
    torch.manual_seed(cfg["seed"])
    dev = _device(cfg, log)
    val_idx, trn_idx = grouped_split(len(datas), groups)
    y_trn = [ys[i] for i in trn_idx]
    y_mean = sum(y_trn) / max(1, len(y_trn))
    y_sd = (sum((v - y_mean) ** 2 for v in y_trn) / max(1, len(y_trn) - 1)) ** 0.5 or 1.0
    for d in datas:
        d.y_z = (d.y - y_mean) / y_sd

    net = N.Net(cfg["hidden"], cfg["entity_layers"], cfg["action_rounds"]).to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=cfg["lr"],
                            weight_decay=cfg["weight_decay"])
    gen = torch.Generator().manual_seed(cfg["seed"])

    def batches(idx):
        order = [idx[i] for i in torch.randperm(len(idx), generator=gen).tolist()]
        return [Batch.from_data_list([datas[i] for i in order[k:k + cfg["batch"]]])
                for k in range(0, len(order), cfg["batch"])]

    # bf16 on the message passing. The 5090 has an order of magnitude more bf16 throughput
    # than fp32, and bf16 keeps fp32's exponent range so no loss scaler is needed. The
    # losses are computed in fp32: a softmax over up to ~1300 candidates and an MSE are
    # exactly where reduced precision would bite.
    amp = (dev.type == "cuda") and bool(cfg.get("bf16", True))

    def step(b, train):
        b = b.to(dev)
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
    t0 = time.time()
    for epoch in range(cfg["epochs"]):
        net.train()
        for b in batches(trn_idx):
            opt.zero_grad(set_to_none=True)
            loss, _, _ = step(b, True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), cfg["grad_clip"])
            opt.step()
        if val_idx:
            net.eval()
            tot_n, tot_v, n = 0.0, 0.0, 0
            with torch.no_grad():
                for b in batches(val_idx):
                    _, nll, vl = step(b, False)
                    k = int(b.n_actions.numel())
                    tot_n += float(nll) * k
                    tot_v += float(vl) * k
                    n += k
            score = tot_n / max(n, 1)
            improved = best is None or score < best - 1e-5
            if improved:
                best, bad = score, 0
                best_state = {k: v.detach().cpu().clone()
                              for k, v in net.state_dict().items()}
            else:
                bad += 1
            log("mapgraph3.train: epoch %3d  val_nll %.4f  best %.4f  gate %s  %s  %.0fs"
                % (epoch + 1, score, best,
                   [round(float(v), 4) for v in net.encoder.a2e_gate],
                   "*" if improved else " ", time.time() - t0))
            if bad >= cfg["patience"]:
                stopped = "patience"
                break
        if time.time() - t0 > cfg["time_budget_s"]:
            stopped = "time_budget"
            break
    net = net.cpu()
    if best_state:
        net.load_state_dict(best_state)
    fit = {"val_listwise_nll": round(best, 5) if best is not None else None,
           "epochs_run": epoch + 1, "stopped_by": stopped, "device": dev.type,
           "val_rows": len(val_idx), "train_rows": len(trn_idx),
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
    net, fit, y_mean, y_sd = _fit(datas, [e["y"] for e in ex],
                                  [e["campaign_id"] for e in ex], cfg, log=log)
    import torch
    meta = {"backend": "gnn3", "schema_version": S.SCHEMA_VERSION,
            "schema_hash": S.schema_hash(), "cfg": cfg, "rows": len(datas),
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
    return {"trained": True, "backend": "gnn3", "rows": len(datas), "fit": fit,
            "campaigns": len(meta["campaigns"]), "tally": w["tally"],
            "walk_seconds": round(time.time() - t0 - fit["seconds"], 1)}


def _overfit(limit=8):
    """Can it drive the listwise loss down on a handful of graphs?

    Spread is measured on q across the candidate set -- never on q-v, which is the
    self-confirming artifact v2's _overfit gate was reading.
    """
    import torch
    from torch_geometric.data import Batch
    try:
        from mapgraph3 import net as N
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
