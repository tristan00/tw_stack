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

from advisor.mapgraph import greedy_train as GT
from advisor.mapgraph import train as T

STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_DIR = os.path.join(common.native(common.LOGS_SERVICES), "optuna_gnn_greedy")
TRIALS_JSONL = os.path.join(OUT_DIR, "trials_%s.jsonl" % STAMP)

FIXED = {"epochs": 60, "patience": 10, "bf16": True, "seed": 0, "device": "cuda"}
STUDY_PATIENCE = 12
SAMPLER_SEED = int(time.time()) % 100000


def _patience_cb(patience=STUDY_PATIENCE):
    def cb(study, trial):
        try:
            best = study.best_trial.number
        except ValueError:
            return
        stale = sum(1 for t in study.trials
                    if t.state.is_finished() and t.number > best)
        if stale >= patience:
            _log("PATIENCE: no improvement in %d trials since best trial %d -- "
                 "stopping the study" % (stale, best))
            study.stop()
    return cb


def _storage():
    from decisions import pg
    admin = pg.connect(dbname="postgres", autocommit=True)
    try:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname='optuna'").fetchone():
            admin.execute("CREATE DATABASE optuna")
    finally:
        admin.close()
    return "postgresql+psycopg://%s@%s:%d/optuna" % (pg.USER, pg.HOST, pg.PORT)


def _log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg), flush=True)


def _space(trial):
    p = {"lr": trial.suggest_float("lr", 1e-5, 3e-3, log=True),
         "weight_decay": trial.suggest_float("weight_decay", 1e-6, 1e-1, log=True),
         "hidden": trial.suggest_categorical("hidden", [32, 64, 128, 192, 256]),
         "batch": trial.suggest_categorical("batch", [192, 384, 768]),
         "dropout": trial.suggest_float("dropout", 0.0, 0.5),
         "grad_clip": trial.suggest_categorical("grad_clip", [0.5, 1.0, 2.0, 5.0, 10.0]),
         "entity_layers": trial.suggest_int("entity_layers", 1, 3),
         "action_rounds": trial.suggest_int("action_rounds", 1, 4),
         "attn": trial.suggest_categorical("attn", ["none", "act", "map", "all"]),
         "update": trial.suggest_categorical("update", ["mlp", "linear", "none"]),
         "conv": trial.suggest_categorical("conv", ["sage", "rel"]),
         "conv_e2a": trial.suggest_categorical("conv_e2a", ["rel", "sage"]),
         "map_aggr": trial.suggest_categorical("map_aggr",
                                               ["max", "mean", "add+mean", "mean+max"]),
         "act_aggr": trial.suggest_categorical("act_aggr", ["add+mean", "max", "mean"]),
         "self_transform": trial.suggest_categorical("self_transform", [False, True])}
    cm = trial.suggest_categorical("conv_map", ["inherit", "sage", "rel"])
    ca = trial.suggest_categorical("conv_a2e", ["inherit", "sage", "rel"])
    p["conv_map"] = None if cm == "inherit" else cm
    p["conv_a2e"] = None if ca == "inherit" else ca
    kinds = (p["conv"], p["conv_map"] or p["conv"], p["conv_a2e"] or p["conv"],
             p["conv_e2a"])
    if "rel" in kinds:
        p["dst_dim"] = trial.suggest_categorical("dst_dim", [32, 48, 96, 128])
    else:
        p["dst_dim"] = 48
    return p


def run(trials=None, budget_s=600.0, timeout_s=None):
    import optuna
    import torch
    os.makedirs(OUT_DIR, exist_ok=True)
    _log("optimize_greedy: walking the corpus once")
    w = T.walk(log=_log)
    ex = w["examples"]
    datas = T._tensorize(ex)
    ys = [e["y"] for e in ex]
    groups = [e["campaign_id"] for e in ex]
    _log("optimize_greedy: %d rows, %d campaigns, %.0fs budget per trial, patience %d, "
         "sampler seed %d" % (len(ex), len(w["campaigns"]), budget_s, STUDY_PATIENCE,
                              SAMPLER_SEED))

    def _fit(cfg, tag):
        return GT.fit_net(datas, ys, groups, cfg,
                          log=lambda s: _log("  %s %s" % (tag, s)))

    base_cfg = dict(GT.CFG, time_budget_s=budget_s)
    _log("BASELINE start (current greedy CFG at the %.0fs budget)" % budget_s)
    _, base_fit, _, _ = _fit(base_cfg, "base")
    _log("BASELINE val_mse=%.5f r2=%+0.4f epochs=%d stopped=%s"
         % (base_fit["val_mse"], base_fit["val_r2"] or 0.0, base_fit["epochs_run"],
            base_fit["stopped_by"]))

    study = optuna.create_study(
        study_name="gnn_greedy_%s" % STAMP, storage=_storage(), direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=SAMPLER_SEED, multivariate=True),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=8, n_warmup_steps=3))

    def objective(trial):
        p = _space(trial)
        cfg = dict(GT.CFG, **FIXED, **p, time_budget_s=budget_s)
        _log("TRIAL %d start %s" % (trial.number, json.dumps(p, sort_keys=True)))
        t0 = time.time()

        def on_epoch(epoch, score):
            trial.report(score, epoch)
            if trial.should_prune():
                raise optuna.TrialPruned()

        try:
            _, fit, _, _ = GT.fit_net(datas, ys, groups, cfg,
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
               "val_mse": fit["val_mse"], "val_r2": fit["val_r2"],
               "epochs": fit["epochs_run"], "stopped_by": fit["stopped_by"],
               "ts": time.time()}
        with open(TRIALS_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        vals = [t.value for t in study.trials if t.value is not None]
        _log("TRIAL %d done val_mse=%.5f r2=%+0.4f epochs=%d stopped=%s %.0fs "
             "(best so far %.5f)"
             % (trial.number, fit["val_mse"], fit["val_r2"] or 0.0, fit["epochs_run"],
                fit["stopped_by"], row["seconds"], min(vals + [fit["val_mse"]])))
        return fit["val_mse"]

    study.optimize(objective, n_trials=trials, timeout=timeout_s, gc_after_trial=True,
                   catch=(RuntimeError,), callbacks=[_patience_cb()])
    best = study.best_trial
    _log("STUDY COMPLETE best trial %d val_mse=%.5f (baseline %.5f) params %s"
         % (best.number, best.value, base_fit["val_mse"],
            json.dumps(best.params, sort_keys=True)))
    json.dump({"best_trial": best.number, "best_val_mse": best.value,
               "best_params": best.params, "n_trials": len(study.trials),
               "budget_s": budget_s,
               "baseline_val_mse": base_fit["val_mse"],
               "baseline_val_r2": base_fit["val_r2"],
               "baseline_cfg": {k: GT.CFG[k] for k in sorted(GT.CFG)}},
              open(os.path.join(OUT_DIR, "best_%s.json" % STAMP), "w"), indent=1)
    return 0


if __name__ == "__main__":
    common.require_venv()
    a = sys.argv[1:]
    n = int(a[a.index("--trials") + 1]) if "--trials" in a else None
    b = float(a[a.index("--budget") + 1]) if "--budget" in a else 600.0
    t = float(a[a.index("--timeout") + 1]) if "--timeout" in a else None
    raise SystemExit(run(n, b, t))
