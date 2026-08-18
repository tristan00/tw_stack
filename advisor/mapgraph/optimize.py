from __future__ import annotations


import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

from advisor.mapgraph import train as T

STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_DIR = os.path.join(common.native(common.LOGS_SERVICES), "optuna_gnn")
STORAGE = "sqlite:///" + os.path.join(OUT_DIR, "optuna_gnn.db").replace("\\", "/")
TRIALS_JSONL = os.path.join(OUT_DIR, "trials.jsonl")
FIXED = {"update": "mlp", "epochs": 60, "patience": 10, "grad_clip": 5.0,
         "bf16": True, "seed": 0, "device": "cuda", "conv_map": None, "conv_a2e": None}


def _log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg)
    print(line, flush=True)


def _space(trial):
    p = {"lr": trial.suggest_float("lr", 5e-5, 3e-3, log=True),
         "weight_decay": trial.suggest_float("weight_decay", 1e-5, 3e-2, log=True),
         "hidden": trial.suggest_categorical("hidden", [32, 64, 128, 256]),
         "batch": trial.suggest_categorical("batch", [192, 384, 768]),
         "dropout": trial.suggest_float("dropout", 0.0, 0.3),
         "entity_layers": trial.suggest_int("entity_layers", 1, 2),
         "action_rounds": trial.suggest_int("action_rounds", 1, 4),
         "attn": trial.suggest_categorical("attn", ["none", "act", "map", "all"]),
         "conv": trial.suggest_categorical("conv", ["sage", "rel"]),
         "conv_e2a": trial.suggest_categorical("conv_e2a", ["rel", "sage"]),
         "map_aggr": trial.suggest_categorical("map_aggr",
                                               ["max", "mean", "add+mean", "mean+max"]),
         "act_aggr": trial.suggest_categorical("act_aggr", ["add+mean", "max", "mean"]),
         "self_transform": trial.suggest_categorical("self_transform", [False, True]),
         "adv_tau": trial.suggest_float("adv_tau", 0.3, 3.0, log=True),
         "adv_clip": trial.suggest_float("adv_clip", 3.0, 50.0, log=True),
         "value_weight": trial.suggest_float("value_weight", 0.02, 0.5, log=True)}
    if p["conv"] == "rel" or p["conv_e2a"] == "rel":
        p["dst_dim"] = trial.suggest_categorical("dst_dim", [32, 48, 96])
    else:
        p["dst_dim"] = 48
    return p


def run(trials=60, budget_s=600.0):
    import optuna
    import torch
    os.makedirs(OUT_DIR, exist_ok=True)
    _log("optimize: walking the corpus once")
    w = T.walk(log=_log)
    ex = w["examples"]
    datas = T._tensorize(ex)
    ys = [e["y"] for e in ex]
    groups = [e["campaign_id"] for e in ex]
    _log("optimize: %d rows, %d campaigns, starting %d trials at %.0fs budget each"
         % (len(ex), len(w["campaigns"]), trials, budget_s))

    study = optuna.create_study(
        study_name="gnn_%s" % STAMP, storage=STORAGE, direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=0, multivariate=True),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=8, n_warmup_steps=3))

    def objective(trial):
        p = _space(trial)
        cfg = dict(T.CFG, **FIXED, **p, time_budget_s=budget_s)
        _log("TRIAL %d start %s" % (trial.number, json.dumps(p, sort_keys=True)))
        t0 = time.time()

        def on_epoch(epoch, score):
            trial.report(score, epoch)
            if trial.should_prune():
                raise optuna.TrialPruned()

        try:
            _, fit, _, _ = T.fit_net(datas, ys, groups, cfg,
                                     log=lambda s: _log("  t%d %s" % (trial.number, s)),
                                     on_epoch=on_epoch)
        except optuna.TrialPruned:
            torch.cuda.empty_cache()
            raise
        except RuntimeError as e:
            msg = repr(e)[:200]
            _log("TRIAL %d FAILED %s" % (trial.number, msg))
            e = None
            import gc
            gc.collect()
            torch.cuda.empty_cache()
            raise RuntimeError(msg) from None
        finally:
            torch.cuda.empty_cache()
        row = {"trial": trial.number, "params": p, "seconds": round(time.time() - t0, 1),
               "val_nll": fit["val_listwise_nll"], "val_mse": fit["val_value_mse"],
               "epochs": fit["epochs_run"], "stopped_by": fit["stopped_by"], "ts": time.time()}
        with open(TRIALS_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        _log("TRIAL %d done val_nll=%.5f epochs=%d stopped=%s %.0fs (best so far %.5f)"
             % (trial.number, fit["val_listwise_nll"], fit["epochs_run"],
                fit["stopped_by"], row["seconds"],
                min([t.value for t in study.trials
                     if t.value is not None] + [fit["val_listwise_nll"]])))
        return fit["val_listwise_nll"]

    study.optimize(objective, n_trials=trials, gc_after_trial=True,
                   catch=(RuntimeError,))
    best = study.best_trial
    _log("STUDY COMPLETE best trial %d val_nll=%.5f params %s"
         % (best.number, best.value, json.dumps(best.params, sort_keys=True)))
    json.dump({"best_trial": best.number, "best_val_nll": best.value,
               "best_params": best.params, "n_trials": len(study.trials),
               "baseline_cfg": {k: T.CFG[k] for k in sorted(T.CFG)}},
              open(os.path.join(OUT_DIR, "best_%s.json" % STAMP), "w"), indent=1)
    return 0


if __name__ == "__main__":
    common.require_venv()
    a = sys.argv[1:]
    n = int(a[a.index("--trials") + 1]) if "--trials" in a else 60
    b = float(a[a.index("--budget") + 1]) if "--budget" in a else 600.0
    raise SystemExit(run(n, b))
