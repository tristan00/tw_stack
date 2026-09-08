from __future__ import annotations


import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

from advisor.mapgraph import graph_config as GC
from advisor.mapgraph import sequence_config as SC
from advisor.reward_data import METRIC, remaining
from advisor.reward_worker import run_trial, validate_budget

OUT_DIR = os.path.join(common.native(common.LOGS_SERVICES), "optuna_gnn_sequence")

STUDY_PATIENCE = 100
MAX_TRIALS = 1000
TRIAL_BUDGET_S = 600
TPE_STARTUP = 11
SAMPLER_SEED = int(time.time()) % 100000
TUNE_WINDOW = 2500


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
         "hidden": trial.suggest_int("hidden", 4, 128, step=1),
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


def _fit_trial(trial, window, cfg, graph_config, directory, deadline, log):
    import optuna

    os.makedirs(directory, exist_ok=False)
    response = run_trial("sequence", window, dict(model=cfg, graph=graph_config.as_dict()),
        directory, min(600, remaining(deadline)), cfg["device"], cfg["seed"], 0)
    if response["status"] == "pruned":
        raise optuna.TrialPruned(response["error"])
    if response["status"] != "complete":
        raise RuntimeError(response["error"])
    return response["result"]

def run(trials=MAX_TRIALS, budget_s=TRIAL_BUDGET_S, timeout_s=None,
        window=TUNE_WINDOW, study_patience=STUDY_PATIENCE):
    import optuna

    started = time.perf_counter()
    validate_budget(budget_s)
    if trials < 1 or budget_s <= 0 or window < 1 or study_patience < 1:
        raise ValueError("Trials, budget, window and study patience must be positive")
    if timeout_s is not None and timeout_s <= 0:
        raise ValueError("Study timeout must be positive")
    study_deadline = started + timeout_s if timeout_s is not None else float("inf")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    _log("tuning enter window=%d trials=%d trial_budget=%.1fs study_timeout=%s seed=%d" %
         (window, trials, budget_s, timeout_s, SAMPLER_SEED))
    remaining(study_deadline)
    sampler = optuna.samplers.TPESampler(seed=SAMPLER_SEED, multivariate=True,
                                         n_startup_trials=TPE_STARTUP)
    study = optuna.create_study(study_name="gnn_sequence_%s" % stamp, storage=_storage(),
        direction="minimize", sampler=sampler, pruner=optuna.pruners.NopPruner())
    study.set_user_attr("model_family", "sequence")
    study.set_user_attr("metric", METRIC)
    study.set_user_attr("source", "live corpus; advisor.reward_data")
    study.set_user_attr("window", window)
    study.set_user_attr("trial_budget_seconds", budget_s)
    study.set_user_attr("sampler_seed", SAMPLER_SEED)
    directory = os.path.join(OUT_DIR, study.study_name)
    os.makedirs(directory, exist_ok=False)

    def objective(trial):
        trial_started = time.perf_counter()
        deadline = min(trial_started + budget_s, study_deadline)
        cfg = dict(_space(trial), **SC.suggest(trial))
        graph_config = _graph_space(trial)
        artifacts = os.path.join(directory, "trial_%06d" % trial.number)
        trial.set_user_attr("model_config", cfg)
        trial.set_user_attr("graph_config", graph_config.as_dict())
        trial.set_user_attr("artifacts", artifacts)
        _log("TRIAL %d enter params=%s" % (trial.number, json.dumps(trial.params, sort_keys=True)))

        def log(message):
            _log("  t%d %s" % (trial.number, message))

        try:
            fit = _fit_trial(trial, window, cfg, graph_config, artifacts, deadline, log)
            trial.set_user_attr("graph_metrics", fit["graph_metrics"])
            remaining(deadline)
        except TimeoutError as error:
            trial.set_user_attr("reason", str(error))
            raise optuna.TrialPruned(str(error)) from None
        finally:
            seconds = time.perf_counter() - trial_started
            trial.set_user_attr("seconds", seconds)
            _log("TRIAL %d exit %.1fs" % (trial.number, seconds))
        if time.perf_counter() >= deadline:
            trial.set_user_attr("reason", "Trial deadline exceeded during cleanup")
            raise optuna.TrialPruned("Trial deadline exceeded during cleanup")
        attributes = dict(r2=fit["val_r2"], epochs=fit["epochs_run"], stopped=fit["stopped_by"],
            encoder_seconds=fit["encoder"]["seconds"], encoder_epochs=fit["encoder"]["epochs"],
            encoder_val_loss=fit["encoder"]["val_loss"], sequence_seconds=fit["sequence"]["seconds"],
            embedding_seconds=fit["embedding_seconds"],
            normalized_val_mse=fit["normalized_val_mse"])
        for key, value in attributes.items():
            trial.set_user_attr(key, value)
        _log("TRIAL %d score val_mse=%.5f R2=%.5f" % (trial.number, fit["val_mse"], fit["val_r2"]))
        return fit["val_mse"]

    study.optimize(objective, n_trials=trials, timeout=remaining(study_deadline),
                   callbacks=[_patience_cb(study_patience)])
    complete = study.get_trials(deepcopy=False, states=(optuna.trial.TrialState.COMPLETE,))
    _log("tuning exit %.1fs trials=%d complete=%d" %
         (time.perf_counter() - started, len(study.trials), len(complete)))
    if not complete:
        return 2
    best = study.best_trial
    with open(os.path.join(directory, "best.json"), "w", encoding="utf-8") as file:
        json.dump(dict(trial=best.number, val_mse=best.value, params=best.params,
                       **best.user_attrs), file, indent=2, allow_nan=False)
    _log("BEST trial=%d val_mse=%.5f artifacts=%s" %
         (best.number, best.value, best.user_attrs["artifacts"]))
    return 0


def main(args=None):
    parser = argparse.ArgumentParser(description="Tune the graph encoder and sequence reward predictor")
    parser.add_argument("--trials", type=int, default=MAX_TRIALS)
    parser.add_argument("--budget", type=float, default=TRIAL_BUDGET_S)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--window", type=int, default=TUNE_WINDOW)
    a = parser.parse_args(args)
    return run(a.trials, a.budget, a.timeout, window=a.window)


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
