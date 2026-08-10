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

BUDGET. A retrain must fit in `time_budget_s` end to end, walk included, because it runs
between campaigns with the game shut down. Measured on 20.4k graphs / RTX 5090:

    corpus walk + tensorize   125s      <- now the dominant cost
    fit                       130s      18 epochs at ~7.3s each
    total                     256s

Before this pass the same retrain got 4 epochs in 989s. What moved:
  - the backward of `param[node_type]` in TypeNorm/TypeEncoders was 90% of all gpu time
    (an atomic scatter from ~22k rows into 19); F.embedding's segment-reduced backward
    is the same maths ~50x cheaper. 75s/epoch -> 10.9s at batch 64.
  - collate once and keep batches resident instead of re-collating every epoch (v2 knew
    this; v3 had lost it), batch 16 -> 128, fused AdamW, one val sync per epoch not 364.
  - to_data splits edges with array masks instead of a python loop over 61M edges.

CORPUS CACHE -- the next thing, and the one that matters at scale. The walk is now 49%
of the budget and it is pure repetition: every retrain rebuilds graphs for decisions that
have not changed. decision_points / entity_snapshots / action_offers are append-only, so
everything at or below a frozen max(decision_id) is immutable and its graph is a pure
function of the record plus build.py/schema.py/the reference DB. Cache the TENSORIZED
graph keyed by a fingerprint over those sources -- but NOT the label (decision_deltas
reads future turns, so y changes as a campaign advances) and NOT the taken mask
(attach_taken can rewrite it; store a hash and rebuild that one graph if it moved).
Steady-state cost then becomes O(new decisions), not O(corpus), which is what a 250k
corpus needs. Sharded parallel reads are NOT the answer and were measured: see walk().
"""

import glob
import json
import os
import shutil
import sys
import time

try:
    from mapgraph3 import schema as S
    from mapgraph3 import build as B
except ImportError:
    import schema as S
    import build as B

# Resolve advisor/ and decisions/ against the checkout this file lives in -- common.py
# derives ROOT from its own location -- not a hardcoded D:\tw_stack. In the live checkout
# that is the same path; in a worktree it is the difference between testing your own edits
# and silently importing the main checkout's copies alongside your own mapgraph3.
# This block sits AFTER the import above on purpose: putting the repo root on sys.path
# earlier would change which of the two branches wins when train.py is run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

THREADS = max(1, os.cpu_count() or 8)

CFG = {"hidden": 192, "entity_layers": 2, "action_rounds": 2,
       # batch 16 left the 5090 idle: ~1200 launch-bound steps an epoch. Measured on
       # 20.4k graphs, resident: b16 87s/epoch, b64 10.9s, b128 8.4s, b256 13.9s.
       # NOTE lr is unchanged at 2e-3 -- 8x fewer optimizer steps per epoch is a real
       # change to the optimisation and the right lr for b128 has NOT been measured yet.
       # patience 25 was never reachable when an epoch cost 250s, so it had never been
       # exercised. With a 5s epoch the full curve is now visible, and it says the fit
       # peaks around epoch 14 and then OVERFITS steadily -- val_nll 3.994 at 14 rising
       # to 4.28 by 39, which is 7x the epoch-to-epoch noise, so it is a trend and not
       # jitter. 25 epochs of that is ~170s of a 300s budget spent getting worse.
       # 10 still clears the noise band comfortably. Lowering it cannot cost accuracy --
       # the fit restores best_state either way, so this only decides how long it keeps
       # looking. Single seed, single split: worth re-checking across seeds.
       "lr": 2e-3, "weight_decay": 1e-4, "batch": 128, "epochs": 200, "patience": 10,
       "grad_clip": 5.0, "adv_tau": 1.0, "adv_clip": 20.0, "value_weight": 1.0, "bf16": True,
       # TOTAL budget for a retrain -- corpus walk AND fit, not the fit alone. session.py
       # retrains between campaigns with the game shut down, so every second here is a
       # second the run is not collecting data. The fit gets whatever the walk leaves.
       "seed": 0, "time_budget_s": 300, "device": "auto"}

MIN_FIT_S = 30


def _shard(args):
    """Worker: read one decision_id range, build and tensorize its graphs.

    Runs in a separate process, so it re-derives sys.path rather than inheriting it.
    Returns one slot per decision including the rejected ones -- the parent needs them
    to reproduce the tally, and it applies the no_label check first, in the same order
    the serial walk did.
    """
    db_path, lo, hi = args
    import os as _os
    import sys as _sys
    # One thread per worker. torch and numpy each default to a pool the width of the
    # box, so N workers spin N*24 threads over 24 cores and burn far more CPU spinning
    # in barriers than the work costs: measured 1195s of worker CPU for a walk that
    # takes 141s single-threaded. This stage is python and sqlite, not BLAS.
    for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "NUMEXPR_NUM_THREADS"):
        _os.environ[_v] = "1"
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if root not in _sys.path:
        _sys.path.insert(0, root)
    import common as _common
    for p in (_common.DECISIONS, _common.ADVISOR, _common.ROOT):
        if p not in _sys.path:
            _sys.path.insert(0, p)
    from store import DecisionStore
    try:
        from mapgraph3 import build as B2
        from mapgraph3 import net as N2
    except ImportError:
        import build as B2
        import net as N2
    import torch as _t
    _t.set_num_threads(1)

    st = DecisionStore(_os.path.dirname(db_path), readonly=True)
    try:
        # No snapshot_read: decision_points / entity_snapshots / action_offers are
        # append-only and `hi` was frozen by the parent, so every worker sees exactly
        # the same rows without needing a shared transaction.
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


# Small enough that a slow shard cannot stall the pool at the tail: graph cost varies a
# lot with turn number, so a few big shards leave most workers idle at the end.
SHARD = 400


def walk(runs_root=None, limit=None, log=print, workers=None):
    """Corpus -> (graph, y, taken-mask) examples.

    Note what is NOT here: v2 called features.stamp_prev_actions and
    stamp_action_counts to write CatBoost history features into the record before
    building the graph. v3 does not. The only advisor code touched is the label.
    """
    import concurrent.futures as cf
    import torch
    from base_model import RUNS_ROOT, decision_deltas, target
    from store import DecisionStore, IncompatibleStore
    runs_root = runs_root or RUNS_ROOT
    dbs = sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")))

    try:
        from mapgraph3 import corpus as CO
    except ImportError:
        import corpus as CO

    # pass 1: label series, the frozen watermark, and what each decision played. All
    # small tables -- target_series is ~2k rows and action_taken ~22k, against 9M offers.
    series, skipped, live = {}, [], []
    for db in dbs:
        run_dir = os.path.dirname(db)
        try:
            st = DecisionStore(run_dir, readonly=True)
        except IncompatibleStore as e:
            skipped.append(os.path.basename(run_dir))
            log("mapgraph3.train: skipping %s -> %s" % (run_dir, str(e)[:100]))
            continue
        try:
            for camp, turns in st.target_series().items():
                series.setdefault(camp, {}).update(turns)
            live.append((db, st.max_decision_id(), st.taken_map()))
        finally:
            st.close()

    # pass 2: build the graphs. The corpus walk is the single largest cost in a retrain
    # and it is embarrassingly parallel over decision_id ranges: the tables are
    # append-only and the watermark is frozen above, so each shard is a stable slice.
    # Shards are consumed in ascending decision_id, which is the order the serial walk
    # produced, so the example sequence -- and therefore batch composition -- is
    # unchanged.
    # Sharded reads default OFF because they measured SLOWER, not faster. Serial walk:
    # 141s wall, ~141s cpu. 12 workers over 400-decision shards: 256s wall and 381s cpu
    # and still unfinished. Bounding decision_id turns one sequential scan of
    # action_offers into ~9M index-then-rowid fetches, and 12 readers thrash the page
    # cache on a 4.8GB file, so total cpu goes UP and parallelism does not pay for it.
    # Left reachable via workers=N because the sharding itself is sound -- what is wrong
    # is re-reading the whole database at all. The fix is not more workers, it is not
    # re-reading rows that were already turned into graphs (see CORPUS CACHE below).
    n_workers = 1 if workers is None else workers
    t_walk = time.time()
    slots, built, reused = [], 0, 0
    for db, hi, taken_now in live:
        run_key = os.path.basename(os.path.dirname(db))
        cached, cdir = ({}, None) if limit else CO.load(run_key, log=log)

        # Rebuild a decision if it is not cached, or if what it played has changed since
        # it was cached. Everything else is reused as-is: the tables it was built from
        # are append-only, so its record cannot have moved.
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
                # most of the corpus is stale: one unbounded read beats thousands of
                # indexed range lookups (measured -- see the note above)
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

    log("mapgraph3.train: walk %.1fs -- %d graphs built, %d reused from cache"
        % (time.time() - t_walk, built, reused))

    examples = []
    tally = {"no_graph": 0, "no_label": 0, "taken_missing": 0, "no_actions": 0}
    for did, camp_id, campaign, turn, drop, data, counts, _thash in slots:
        # label first, exactly as the serial walk did, so a decision that is both
        # unlabelled and ungraphable is still counted as no_label
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
    """Graphs are tensorized in the shard workers now, so this just collects them."""
    return [ex["data"] for ex in examples]


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


def _corpus_bytes(batches):
    import torch
    return sum(v.numel() * v.element_size() for b in batches
               for v in b.to_dict().values() if torch.is_tensor(v))


def _collate(items, size, dev, log, tag):
    """Collate ONCE, up front, and keep the batches on the training device.

    The graphs are immutable after _tensorize, so re-running Batch.from_data_list every
    epoch -- which is what this did, ~160 times an epoch -- recomputes a result that
    cannot have changed, and re-crosses PCIe with it. v2 had learned this and v3 had
    lost it again.

    Residency is measured against free VRAM, never assumed: the corpus grows at every
    retrain, so the streaming path is the one that has to survive. Half of free VRAM is
    left for activations, which at batch 128 are larger than the corpus itself.
    """
    import torch
    from torch_geometric.data import Batch
    batches = [Batch.from_data_list(items[k:k + size])
               for k in range(0, len(items), size)]
    if dev.type != "cuda" or not batches:
        return batches, False
    need, free = _corpus_bytes(batches), torch.cuda.mem_get_info()[0]
    if need >= free * 0.5:
        log("mapgraph3.train: %s corpus %.2fGB vs %.2fGB free -- streaming batches"
            % (tag, need / 1e9, free / 1e9))
        return batches, False
    try:
        out = [b.to(dev, non_blocking=True) for b in batches]
        torch.cuda.synchronize()
        log("mapgraph3.train: %s %d batches resident on gpu (%.2fGB of %.2fGB free)"
            % (tag, len(out), need / 1e9, free / 1e9))
        return out, True
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        log("mapgraph3.train: %s did not fit VRAM -- streaming batches" % tag)
        return batches, False


def _fit(datas, ys, groups, cfg, log=print):
    import torch
    from base_model import grouped_split
    try:
        from mapgraph3 import net as N
    except ImportError:
        import net as N

    torch.set_num_threads(THREADS)
    torch.manual_seed(cfg["seed"])
    torch.set_float32_matmul_precision("high")
    dev = _device(cfg, log)
    val_idx, trn_idx = grouped_split(len(datas), groups)
    y_trn = [ys[i] for i in trn_idx]
    y_mean = sum(y_trn) / max(1, len(y_trn))
    y_sd = (sum((v - y_mean) ** 2 for v in y_trn) / max(1, len(y_trn) - 1)) ** 0.5 or 1.0
    for d in datas:
        d.y_z = (d.y - y_mean) / y_sd

    net = N.Net(cfg["hidden"], cfg["entity_layers"], cfg["action_rounds"]).to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=cfg["lr"],
                            weight_decay=cfg["weight_decay"],
                            fused=(dev.type == "cuda"))
    gen = torch.Generator().manual_seed(cfg["seed"])

    # One seeded shuffle fixes batch MEMBERSHIP for the run; each epoch then permutes
    # batch ORDER. This is a real change to the optimisation and not a free win: the
    # model now draws from a fixed set of minibatch gradients instead of resampling a
    # fresh partition every epoch. It is what makes collate-once possible, and it is
    # what v2 shipped. Validation is never shuffled -- the reported score is a
    # graph-weighted mean, so it does not depend on batching or order.
    order0 = torch.randperm(len(trn_idx), generator=gen).tolist()
    trn = [datas[trn_idx[i]] for i in order0]
    loader, resident = _collate(trn, cfg["batch"], dev, log, "train")
    vloader, v_resident = (_collate([datas[i] for i in val_idx], cfg["batch"], dev, log,
                                    "val") if val_idx else ([], False))

    # bf16 on the message passing. The 5090 has an order of magnitude more bf16 throughput
    # than fp32, and bf16 keeps fp32's exponent range so no loss scaler is needed. The
    # losses are computed in fp32: a softmax over up to ~1300 candidates and an MSE are
    # exactly where reduced precision would bite.
    amp = (dev.type == "cuda") and bool(cfg.get("bf16", True))

    def step(b, train):
        if b.x.device != dev:               # no-op when the batch is already resident
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
    best_v, curve = None, []
    # The budget has to cover the validation pass that follows the last training step,
    # otherwise a fit that stops exactly at the limit overruns it by a whole val pass.
    # Seeded with a guess, replaced by the measured cost after the first epoch.
    val_s = 6.0
    t0 = time.time()
    for epoch in range(cfg["epochs"]):
        net.train()
        for i in torch.randperm(len(loader), generator=gen).tolist():
            opt.zero_grad(set_to_none=True)
            loss, _, _ = step(loader[i], True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), cfg["grad_clip"])
            opt.step()
            # Checked per step, not per epoch: an epoch that overshoots the budget by a
            # whole epoch is fatal against a 300s target. Breaks to validation -- and
            # leaves room for it -- so best_state still reflects a scored model.
            if time.time() - t0 > cfg["time_budget_s"] - val_s:
                stopped = "time_budget"
                break
        if vloader:
            net.eval()
            t_val = time.time()
            # Accumulated on-device: float(nll) per batch forced a gpu->cpu sync on
            # every validation batch and serialised the whole pass. One sync per epoch.
            tot_n = torch.zeros((), device=dev)
            tot_v = torch.zeros((), device=dev)
            n = 0
            with torch.no_grad():
                for b in vloader:
                    _, nll, vl = step(b, False)
                    k = int(b.n_actions.numel())    # metadata, not a sync
                    tot_n += nll.detach() * k
                    tot_v += vl.detach() * k
                    n += k
            score = float(tot_n) / max(n, 1)
            val_s = max(val_s, time.time() - t_val)
            # Kept in meta so "what does the time cap cost us" is answerable later from
            # stored evidence instead of from a console log that is gone.
            curve.append([epoch + 1, round(time.time() - t0, 1), round(score, 5)])
            improved = best is None or score < best - 1e-5
            if improved:
                best, bad = score, 0
                best_v = float(tot_v) / max(n, 1)
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
           "val_value_mse": round(best_v, 5) if best_v is not None else None,
           "epochs_run": epoch + 1, "stopped_by": stopped, "device": dev.type,
           "val_rows": len(val_idx), "train_rows": len(trn_idx),
           "resident": bool(resident and v_resident),
           "curve": curve,          # [epoch, seconds, val_listwise_nll] per epoch
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
    # The budget is for the whole retrain, so the fit gets what the walk left. MIN_FIT_S
    # is a floor: a walk that overruns the budget should still produce a model rather
    # than silently return an untrained one.
    walked = time.time() - t0
    fit_cfg = dict(cfg, time_budget_s=max(MIN_FIT_S, cfg["time_budget_s"] - walked))
    log("mapgraph3.train: walk+tensorize %.1fs, %.1fs left of the %ds budget for the fit"
        % (walked, fit_cfg["time_budget_s"], cfg["time_budget_s"]))
    net, fit, y_mean, y_sd = _fit(datas, [e["y"] for e in ex],
                                  [e["campaign_id"] for e in ex], fit_cfg, log=log)
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
