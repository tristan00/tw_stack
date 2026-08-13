from __future__ import annotations

"""Training, for blocking screens.

Same architecture, same MARWIL/AWR loss, same target, same holdout rule as the action
model -- `train.fit_net` is called unchanged and knows nothing about which corpus it got.
What differs is the corpus and the weights, and that is the whole point: this is a
separate model for a separate decision family, exactly as advisor/interrupt_model.py is a
separate CatBoost from advisor/model.py. Nothing here loads the action model's encoder.

    D:\\twdata\\models\\mapgraph_interrupt\\{encoder.pt, head.pt, meta.json}

WHAT TO EXPECT. There are 255 trainable screens in the corpus at the time of writing
(pre_battle 172, occupation 40, battle_results 31, dilemma 8, diplomacy_proposal 4) across
69 campaigns, against 3,435 map decisions. That is not enough to be good and it is not
supposed to be yet -- the arm plays 10% so that it produces rows to measure while the
corpus grows. So `uniform_nll` is computed and stored beside the fit, the fit is reported
against it, and NOTHING here refuses to load a weak model: refusing would produce no
gnn_marwil interrupt rows, and those rows are the reason this exists.

Single-option screens (86 of 347) are dropped: a softmax over one candidate is 1.0, its
NLL is 0, and it contributes no gradient to the ranking. They are still answered live --
there is simply nothing to learn from them.
"""

import json
import math
import os
import shutil
import sys
import time

from advisor.mapgraph import schema as S
from advisor.mapgraph import interrupt_build as IB
from advisor.mapgraph import train as T

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

MODEL_DIR = common.MODEL_MAPGRAPH_INTERRUPT

# Below this, do not fit at all and leave whatever is on disk alone. The action model's
# floor is 40 over ~376 candidates; a screen offers 2-4, so a fit needs more decisions to
# say anything, not fewer.
MIN_ROWS = 120

# The corpus is a few hundred graphs, so the walk is seconds and the fit is short. This
# runs in the same between-campaign window as everything else and must not eat the action
# model's 300s.
CFG = dict(T.CFG, time_budget_s=60, batch=64, patience=15)


def context_for(store, rows):
    """{interrupt_id: {record, decision_id, age_s, interrupts_since}} for each interrupt."""
    by_camp = {}
    for did, ckey, ts in store.decision_index():
        by_camp.setdefault(ckey, []).append((ts, did))
    for v in by_camp.values():
        v.sort()

    irq_ts = {}
    for r in rows:
        irq_ts.setdefault(r.get("campaign_id"), []).append(r.get("ts") or 0.0)
    for v in irq_ts.values():
        v.sort()

    cache, out = {}, {}
    for r in rows:
        camp, ts = r.get("campaign_id"), r.get("ts") or 0.0
        pre = [(t, d) for t, d in by_camp.get(camp) or [] if t <= ts]
        if not pre:
            continue
        base_ts, did = pre[-1]
        if did not in cache:
            cache[did] = store.read_decision(did)
        out[r["interrupt_id"]] = {
            "record": cache[did], "decision_id": did,
            "age_s": round(ts - base_ts, 1),
            "interrupts_since": sum(1 for t in irq_ts.get(camp) or [] if base_ts <= t < ts)}
    return out


def walk(runs_root=None, limit=None, log=print):
    """Corpus -> (graph, y, taken-mask) examples, one per multi-option screen."""
    import torch
    from base_model import RUNS_ROOT, decision_deltas, target
    from store import DecisionStore, IncompatibleStore
    try:
        from advisor.mapgraph import net as N
    except ImportError:
        import net as N

    runs_root = runs_root or RUNS_ROOT
    examples = []
    tally = {"no_context": 0, "one_option": 0, "no_label": 0, "no_graph": 0,
             "taken_missing": 0, "unknown_screen": 0, "build_failed": 0}
    ages, chained, screens = [], [], {}
    runs = 0
    t0 = time.time()

    for db in common.run_dbs(runs_root):
        run_dir = os.path.dirname(db)
        try:
            st = DecisionStore(run_dir, readonly=True)
        except IncompatibleStore as e:
            log("interrupt_train: skipping %s -> %s" % (run_dir, str(e)[:100]))
            continue
        runs += 1
        try:
            rows = st.interrupt_rows()
            series = st.target_series()
            ctx = context_for(st, rows)
        finally:
            st.close()

        for r in rows:
            opts = sorted(r.get("options") or {})
            if len(opts) < 2 or not r.get("chosen"):
                tally["one_option"] += 1
                continue
            c = ctx.get(r["interrupt_id"])
            if c is None:
                tally["no_context"] += 1
                continue
            y = target(decision_deltas(c["record"].get("campaign"),
                                       series.get(r.get("campaign_id")) or {},
                                       r.get("turn")))
            if y is None:
                tally["no_label"] += 1
                continue
            mask = IB.taken_mask(None, opts, r.get("chosen"))
            if mask is None:
                tally["taken_missing"] += 1
                continue
            try:
                g = IB.build_screen_graph(c["record"], r["screen"], opts,
                                          meta=r.get("options"), panel=r.get("panel"))
            except ValueError as e:
                # An unknown screen is a real defect -- launcher/interrupts.py answers a
                # panel this schema has never heard of -- and mapgraph.invariants fails
                # the build on it. Here it must not take the retrain down with it.
                tally["unknown_screen" if "not a known interrupt screen" in str(e)
                      else "build_failed"] += 1
                log("interrupt_train: %s -> %s" % (r["screen"], str(e)[:160]))
                continue
            if g is None:
                tally["no_graph"] += 1
                continue
            data = N.to_data(g, taken=mask)
            data.y = torch.tensor([float(y)], dtype=torch.float32)
            examples.append({"data": data, "y": float(y), "screen": r["screen"],
                             "campaign_id": r.get("campaign_id"), "n_options": len(opts),
                             "counts": g.counts})
            ages.append(c["age_s"])
            chained.append(c["interrupts_since"])
            screens[r["screen"]] = screens.get(r["screen"], 0) + 1
            if limit and len(examples) >= limit:
                break

    ages.sort()
    log("interrupt_train: walk %.1fs -- %d screens, %s"
        % (time.time() - t0, len(examples), json.dumps(screens)))
    return {"examples": examples, "tally": tally, "runs": runs, "screens": screens,
            "campaigns": sorted({e["campaign_id"] for e in examples}),
            "context": {"age_s_median": ages[len(ages) // 2] if ages else None,
                        "age_s_max": ages[-1] if ages else None,
                        "chained_max": max(chained) if chained else 0,
                        "chained_rows": sum(1 for c in chained if c)}}


def uniform_nll(examples, idx=None):
    """Mean -log(1/k) over the candidate sets: what guessing scores."""
    picks = examples if idx is None else [examples[i] for i in idx]
    if not picks:
        return None
    return round(sum(math.log(max(2, e["n_options"])) for e in picks) / len(picks), 5)


def train(runs_root=None, cfg=None, log=None):
    log = log or (lambda s: sys.stderr.write(str(s) + "\n"))
    cfg = dict(CFG, **(cfg or {}))
    t0 = time.time()
    w = walk(runs_root, log=log)
    ex = w["examples"]
    if len(ex) < MIN_ROWS:
        return {"trained": False, "rows": len(ex), "need": MIN_ROWS,
                "tally": w["tally"], "screens": w["screens"]}

    from base_model import stable_split
    datas = [e["data"] for e in ex]
    groups = [e["campaign_id"] for e in ex]
    walked = time.time() - t0
    fit_cfg = dict(cfg, time_budget_s=max(T.MIN_FIT_S, cfg["time_budget_s"] - walked))
    net, fit, y_mean, y_sd = T.fit_net(datas, [e["y"] for e in ex], groups, fit_cfg, log=log)

    import torch
    val_idx, _trn = stable_split(len(datas), groups)
    meta = {"backend": "mapgraph_interrupt", "schema_version": S.SCHEMA_VERSION,
            "schema_hash": S.schema_hash(), "cfg": cfg, "rows": len(datas),
            "campaigns": sorted({e["campaign_id"] for e in ex}), "fit": fit,
            "y_mean": round(y_mean, 6), "y_sd": round(y_sd, 6),
            "screens": w["screens"], "tally": w["tally"], "context": w["context"],
            "n_scalars": S.N_SCALARS, "node_types": list(S.NODE_TYPES),
            "relations": list(S.RELATIONS),
            # What guessing scores on the same held-out screens. Reported, never enforced:
            # a near-random arm is the intended state while the corpus is this small, and
            # refusing to load it would produce no rows to measure.
            "uniform_nll": uniform_nll(ex, val_idx),
            "uniform_nll_all": uniform_nll(ex),
            "target": "base_model.target(decision_deltas(...)), z-scored -- identical to "
                      "the CatBoost interrupt target, unmodified",
            "loss": "advantage-weighted listwise NLL + MSE(v, y_z); no mse(q, y)"}
    stage = MODEL_DIR + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    os.makedirs(MODEL_DIR, exist_ok=True)
    torch.save(net.encoder.state_dict(), os.path.join(stage, "encoder.pt"))
    torch.save(net.head.state_dict(), os.path.join(stage, "head.pt"))
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"))
    for name in ("encoder.pt", "head.pt", "meta.json"):
        os.replace(os.path.join(stage, name), os.path.join(MODEL_DIR, name))
    shutil.rmtree(stage, ignore_errors=True)
    beat = (fit.get("val_listwise_nll") is not None and meta["uniform_nll"] is not None
            and fit["val_listwise_nll"] < meta["uniform_nll"])
    return {"trained": True, "backend": "mapgraph_interrupt", "rows": len(datas),
            "fit": fit, "campaigns": len(meta["campaigns"]), "screens": w["screens"],
            "tally": w["tally"], "context": w["context"],
            "uniform_nll": meta["uniform_nll"], "beats_uniform": bool(beat),
            "walk_seconds": round(time.time() - t0 - fit["seconds"], 1)}


if __name__ == "__main__":
    common.require_venv()
    a = sys.argv[1:]
    if a and a[0] == "report":
        w = walk()
        print(json.dumps({"examples": len(w["examples"]), "tally": w["tally"],
                          "screens": w["screens"], "campaigns": len(w["campaigns"]),
                          "context": w["context"], "need": MIN_ROWS,
                          "uniform_nll": uniform_nll(w["examples"]),
                          "schema_hash": S.schema_hash()}, indent=2))
    elif a and a[0] == "train":
        def _log(s):
            sys.stdout.write(str(s) + "\n")
            sys.stdout.flush()
        over = {}
        for k, dest in (("--budget", "time_budget_s"), ("--batch", "batch"),
                        ("--epochs", "epochs")):
            if k in a:
                over[dest] = int(a[a.index(k) + 1])
        print(json.dumps(train(cfg=over or None, log=_log), indent=2, default=str))
    else:
        raise SystemExit("usage: interrupt_train.py report | train [--budget S]")
