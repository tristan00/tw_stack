from __future__ import annotations


import gc
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
from advisor.mapgraph import graph_config as GC
from advisor.mapgraph import schema as S
from advisor.mapgraph import train as T

STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_DIR = os.path.join(common.native(common.LOGS_SERVICES), "optuna_gnn_greedy")
TRIALS_JSONL = os.path.join(OUT_DIR, "trials_%s.jsonl" % STAMP)

FIXED = {"patience": 4, "bf16": True, "seed": 0, "device": "cuda",
         "epoch_cap_s": 60, "batch": 512}
STUDY_PATIENCE = 100
MAX_TRIALS = 1000
TRIAL_BUDGET_S = 600
TPE_STARTUP = 11
SAMPLER_SEED = int(time.time()) % 100000
TUNE_WINDOW = 2500
GRAPH_PROBE = 200
GPU_MEMORY_FRACTION = 0.70


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
    p = {"lr": trial.suggest_float("lr", 1e-5, 5e-4, log=True),
         "weight_decay": trial.suggest_float("weight_decay", 1e-6, 1e-1, log=True),
         "hidden": trial.suggest_int("hidden", 4, 64, step=1),
         "dropout": trial.suggest_float("dropout", 0.0, 0.5),
         "grad_clip": trial.suggest_float("grad_clip", 0.1, 10.0, log=True),
         "entity_layers": trial.suggest_int("entity_layers", 1, 6),
         "action_rounds": trial.suggest_int("action_rounds", 1, 6),
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
        p["dst_dim"] = trial.suggest_int("dst_dim", 4, 64, step=1)
    else:
        p["dst_dim"] = 4
    return p


def _graph_space(trial):
    limits = GC.default_limits()
    for relation, (low, high) in GC.TUNING_RANGES.items():
        limits[relation] = trial.suggest_int("graph_edge_" + relation, low, high, step=1)
    return GC.GraphBuildConfig(edge_limits=limits).normalized()


def _probe_source(source, size):
    records = source["records"]
    if not size or len(records) <= size:
        return source
    points = [round(i * (len(records) - 1) / (size - 1)) for i in range(size)]
    return dict(source, records=records.select(points))


def _graph_gate(torch, max_corpus_gib, gpu_memory_fraction):
    free_bytes, total_bytes = torch.cuda.mem_get_info()
    free_gib = free_bytes / (1024 ** 3)
    total_gib = total_bytes / (1024 ** 3)
    automatic_gib = free_gib * gpu_memory_fraction
    allowed_gib = (automatic_gib if max_corpus_gib is None
                   else min(automatic_gib, max_corpus_gib))
    return {"free_gpu_gib": round(free_gib, 3),
            "total_gpu_gib": round(total_gib, 3),
            "gpu_memory_fraction": gpu_memory_fraction,
            "automatic_graph_gib": round(automatic_gib, 3),
            "max_corpus_gib": max_corpus_gib,
            "allowed_graph_gib": round(allowed_gib, 3)}


def run(trials=MAX_TRIALS, budget_s=TRIAL_BUDGET_S, timeout_s=None, baseline=False,
        max_corpus_gib=None, limit=None, window=TUNE_WINDOW,
        graph_probe=GRAPH_PROBE, gpu_memory_fraction=GPU_MEMORY_FRACTION,
        study_patience=STUDY_PATIENCE, on_trial=None, study_name=None):
    import optuna
    import torch
    if graph_probe < 2:
        raise ValueError("graph_probe must be at least 2")
    if not 0.0 < gpu_memory_fraction <= 1.0:
        raise ValueError("gpu_memory_fraction must be in (0, 1]")
    if max_corpus_gib is not None and max_corpus_gib <= 0:
        raise ValueError("max_corpus_gib must be positive")
    os.makedirs(OUT_DIR, exist_ok=True)
    source = T.load_walk_source(limit=limit, window=window, log=_log)
    if limit:
        source = dict(source, population_decisions=min(
            limit, source["population_decisions"]))
    probe_source = _probe_source(source, graph_probe)
    gate = _graph_gate(torch, max_corpus_gib, gpu_memory_fraction)
    _log("optimize_greedy: %d source decisions, %.0fs budget per trial, patience %s, "
         "sampler seed %d" % (len(source["records"]), budget_s, study_patience,
                              SAMPLER_SEED))
    _log("optimize_greedy: window=%d probe=%d GPU %.1f/%.1f GiB free, graph gate "
         "%.1f GiB" % (window, len(probe_source["records"]),
                        gate["free_gpu_gib"], gate["total_gpu_gib"],
                        gate["allowed_graph_gib"]))

    base_fit = None
    if baseline:
        w = T.walk_source(source, GC.DEFAULT, limit=limit, log=_log, workers=2)
        ex = w["examples"]
        datas = T._tensorize(ex)
        ys = [e["y"] for e in ex]
        groups = [e["campaign_id"] for e in ex]
        for e in ex:
            e["data"] = None
        base_cfg = dict(GT.CFG, time_budget_s=budget_s)
        _log("BASELINE start (current greedy CFG at the %.0fs budget)" % budget_s)
        base_log = lambda s: _log("  base %s" % s)
        base_prep = GT.prepare(datas, ys, groups, base_cfg, log=base_log, free_datas=True)
        _, base_fit, _, _ = GT.fit_net(datas, ys, groups, base_cfg, log=base_log,
                                       prep=base_prep)
        _log("BASELINE val_mse=%.5f r2=%+0.4f epochs=%d stopped=%s"
             % (base_fit["val_mse"], base_fit["val_r2"] or 0.0, base_fit["epochs_run"],
                base_fit["stopped_by"]))
        base_prep = None
        datas = None
        ex = None
        w = None
        gc.collect()
        torch.cuda.empty_cache()

    sampler = optuna.samplers.TPESampler(seed=SAMPLER_SEED, multivariate=True,
                                         n_startup_trials=TPE_STARTUP)
    study = optuna.create_study(
        study_name=study_name or "gnn_greedy_%s" % STAMP, storage=_storage(), direction="minimize",
        load_if_exists=study_name is not None,
        sampler=sampler, pruner=optuna.pruners.NopPruner())
    _log("optimize_greedy: sampler %s reports n_startup_trials=%d (asked for %d), "
         "pruner %s -- trials 0..%d are random, TPE from trial %d"
         % (type(sampler).__name__, sampler._n_startup_trials, TPE_STARTUP,
            type(study.pruner).__name__, TPE_STARTUP - 1, TPE_STARTUP))

    def objective(trial):
        probe = w = ex = datas = prep = None
        p = _space(trial)
        graph_config = _graph_space(trial)
        cfg = dict(GT.CFG, **FIXED, **p, time_budget_s=budget_s)
        trial.set_user_attr("model_config", cfg)
        _log("TRIAL %d start [%s] model=%s graph=%s"
             % (trial.number, "random" if trial.number < TPE_STARTUP else "tpe",
                json.dumps(p, sort_keys=True),
                json.dumps(graph_config.as_dict(), sort_keys=True)))
        t0 = time.time()

        def on_epoch(epoch, score):
            trial.report(score, epoch)
            if trial.should_prune():
                raise optuna.TrialPruned()

        try:
            probe = T.walk_source(
                probe_source, graph_config,
                log=lambda s: _log("  t%d probe %s" % (trial.number, s)))
            metrics = probe["metrics"]
            trial.set_user_attr("graph_config", graph_config.as_dict())
            trial.set_user_attr("graph_probe_metrics", metrics)
            trial.set_user_attr("graph_memory_gate", gate)
            corpus_gib = metrics["projected_tensor_gib"]
            if corpus_gib > gate["allowed_graph_gib"]:
                raise optuna.TrialPruned(
                    "projected graph tensors %.3f GiB exceed %.3f GiB GPU gate"
                    % (corpus_gib, gate["allowed_graph_gib"]))
            if probe_source is source:
                w = probe
            else:
                probe = None
                w = T.walk_source(
                    source, graph_config, limit=limit, workers=4,
                    log=lambda s: _log("  t%d %s" % (trial.number, s)))
            metrics = w["metrics"]
            trial.set_user_attr("graph_metrics", metrics)
            if len(w["examples"]) < GT.MIN_ROWS:
                raise optuna.TrialPruned("only %d trainable graphs" % len(w["examples"]))
            ex = w["examples"]
            datas = T._tensorize(ex)
            ys = [e["y"] for e in ex]
            groups = [e["campaign_id"] for e in ex]
            for e in ex:
                e["data"] = None
            prep = GT.prepare(datas, ys, groups, cfg,
                              free_datas=True,
                              log=lambda s: _log("  t%d %s" % (trial.number, s)))
            if not prep["val_rows"]:
                raise optuna.TrialPruned("stable split produced no validation rows")
            _, fit, _, _ = GT.fit_net(datas, ys, groups, cfg,
                                      log=lambda s: _log("  t%d %s" % (trial.number, s)),
                                      on_epoch=on_epoch, prep=prep, trial_started=t0,
                                      on_first_step=lambda seconds: trial.set_user_attr(
                                          "first_step_seconds", round(seconds, 3)))
        except optuna.TrialPruned as e:
            trial.set_user_attr("reason", str(e))
            raise
        except (RuntimeError, MemoryError) as e:
            msg = repr(e)[:200]
            _log("TRIAL %d FAILED %s" % (trial.number, msg))
            trial.set_user_attr("reason", msg)
            raise RuntimeError(msg) from None
        finally:
            probe = w = ex = datas = prep = None
            gc.collect()
            torch.cuda.empty_cache()
        row = {"trial": trial.number, "params": p,
               "graph_config": graph_config.as_dict(), "graph_metrics": metrics,
               "seconds": round(time.time() - t0, 1),
               "val_mse": fit["val_mse"], "val_r2": fit["val_r2"],
               "epochs": fit["epochs_run"], "stopped_by": fit["stopped_by"],
               "ts": time.time()}
        trial.set_user_attr("r2", fit["val_r2"])
        trial.set_user_attr("epochs", fit["epochs_run"])
        trial.set_user_attr("stopped", fit["stopped_by"])
        trial.set_user_attr("seconds", row["seconds"])
        trial.set_user_attr("first_step_seconds", fit["first_step_seconds"])
        with open(TRIALS_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        vals = [t.value for t in study.trials if t.value is not None]
        _log("TRIAL %d done val_mse=%.5f r2=%+0.4f epochs=%d stopped=%s %.0fs "
             "(best so far %.5f)"
             % (trial.number, fit["val_mse"], fit["val_r2"] or 0.0, fit["epochs_run"],
                fit["stopped_by"], row["seconds"], min(vals + [fit["val_mse"]])))
        return fit["val_mse"]

    callbacks = [_patience_cb(study_patience)] if study_patience else []
    if on_trial is not None:
        callbacks.append(on_trial)
    study.optimize(objective, n_trials=trials, timeout=timeout_s, gc_after_trial=True,
                   catch=(RuntimeError,), callbacks=callbacks)
    complete = [t for t in study.trials if t.value is not None]
    if not complete:
        _log("STUDY COMPLETE with no completed trials")
        json.dump({"best_trial": None, "best_val_mse": None, "best_params": None,
                   "n_trials": len(study.trials), "budget_s": budget_s,
                   "graph_memory_gate": gate, "window": window,
                   "graph_probe": graph_probe},
                  open(os.path.join(OUT_DIR, "best_%s.json" % STAMP), "w"), indent=1)
        return 2
    best = study.best_trial
    _log("STUDY COMPLETE best trial %d val_mse=%.5f (baseline %s) params %s"
         % (best.number, best.value,
            "%.5f" % base_fit["val_mse"] if base_fit else "skipped",
            json.dumps(best.params, sort_keys=True)))
    json.dump({"best_trial": best.number, "best_val_mse": best.value,
               "best_params": best.params, "n_trials": len(study.trials),
               "best_graph_config": best.user_attrs.get("graph_config"),
               "best_graph_metrics": best.user_attrs.get("graph_metrics"),
               "budget_s": budget_s,
               "graph_memory_gate": gate, "window": window,
               "graph_probe": graph_probe,
               "baseline_val_mse": base_fit["val_mse"] if base_fit else None,
               "baseline_val_r2": base_fit["val_r2"] if base_fit else None,
               "baseline_cfg": {k: GT.CFG[k] for k in sorted(GT.CFG)}},
              open(os.path.join(OUT_DIR, "best_%s.json" % STAMP), "w"), indent=1)
    return 0


if __name__ == "__main__":
    common.require_venv()
    a = sys.argv[1:]
    n = int(a[a.index("--trials") + 1]) if "--trials" in a else MAX_TRIALS
    b = float(a[a.index("--budget") + 1]) if "--budget" in a else TRIAL_BUDGET_S
    t = float(a[a.index("--timeout") + 1]) if "--timeout" in a else None
    g = float(a[a.index("--max-corpus-gib") + 1]) if "--max-corpus-gib" in a else None
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else None
    window = int(a[a.index("--window") + 1]) if "--window" in a else TUNE_WINDOW
    probe = int(a[a.index("--graph-probe") + 1]) if "--graph-probe" in a else GRAPH_PROBE
    fraction = (float(a[a.index("--gpu-memory-fraction") + 1])
                if "--gpu-memory-fraction" in a else GPU_MEMORY_FRACTION)
    raise SystemExit(run(n, b, t, baseline="--baseline" in a,
                         max_corpus_gib=g, limit=limit, window=window,
                         graph_probe=probe, gpu_memory_fraction=fraction))
