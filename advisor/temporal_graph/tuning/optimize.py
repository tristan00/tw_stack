import argparse
import json
import logging
from pathlib import Path
import time

from .config import CONDITIONS, RANGES, SPACE, InvalidConfiguration, suggest, validate_budget
from advisor.reward_data import METRIC
from advisor.reward_worker import run_trial


LOG = logging.getLogger(__name__)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def run(args):
    import optuna

    budget = validate_budget(args.budget)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    storage = args.storage or "sqlite:///" + (output / "study.sqlite3").as_posix()
    study = optuna.create_study(direction="minimize", storage=storage,
        study_name="temporal_graph_" + time.strftime("%Y%m%d_%H%M%S"),
        sampler=optuna.samplers.TPESampler(seed=args.seed, multivariate=True, group=True, n_startup_trials=11),
        pruner=optuna.pruners.NopPruner())
    started = time.perf_counter()
    deadline = started + args.timeout if args.timeout is not None else None
    metadata = dict(model_family="temporal_graph", source="live corpus; advisor.reward_data", window=args.window,
        budget=budget, seed=args.seed, device=args.device, max_cache_gib=args.max_cache_gib,
        objective=METRIC,
        space=SPACE, ranges=RANGES, conditions=CONDITIONS)
    study.set_user_attr("setup", metadata)
    study.set_user_attr("metric", METRIC)
    write_json(output / "setup.json", metadata)

    def objective(trial):
        try:
            config = suggest(trial)
        except InvalidConfiguration as error:
            raise optuna.TrialPruned(str(error)) from error
        trial.set_user_attr("config", config)
        directory = output / ("trial_%06d" % trial.number)
        directory.mkdir()
        write_json(directory / "config.json", config)
        allowance = min(budget, deadline - time.perf_counter()) if deadline is not None else budget
        if allowance <= 0:
            raise optuna.TrialPruned("study deadline reached")
        trial_started = time.perf_counter()
        try:
            response = run_trial("temporal_graph", args.window, config, directory, allowance, args.device,
                                 args.seed, int(args.max_cache_gib * 2**30))
            if response["status"] == "pruned":
                raise optuna.TrialPruned(response["error"])
            if response["status"] != "complete":
                raise RuntimeError(response["error"])
            fit = response["result"]
            for key, value in fit.items():
                if key != "curve":
                    trial.set_user_attr(key, value)
            return fit["val_mse"]
        except TimeoutError as error:
            raise optuna.TrialPruned(str(error)) from error
        finally:
            trial.set_user_attr("seconds", time.perf_counter() - trial_started)

    def callback(study, trial):
        trials = study.get_trials(deepcopy=False)
        if any(t.state == optuna.trial.TrialState.COMPLETE for t in trials):
            best = study.best_trial
            write_json(output / "best.json", dict(trial=best.number, value=best.value,
                params=best.params, attrs=best.user_attrs,
                checkpoint=str(output / ("trial_%06d" % best.number) / "checkpoint.pt")))
            stale = sum(t.state.is_finished() and t.number > best.number for t in trials)
            if stale >= args.patience:
                LOG.info("study patience reached after %d finished trials without improvement", stale)
                study.stop()

    try:
        study.optimize(objective, n_trials=args.trials, timeout=args.timeout, callbacks=[callback])
    finally:
        LOG.info("tuning exit seconds=%.2f trials=%d", time.perf_counter() - started, len(study.trials))
    return 0 if any(t.state == optuna.trial.TrialState.COMPLETE for t in study.trials) else 2


def parser():
    result = argparse.ArgumentParser(description="Tune the independent temporal graph reward model.")
    result.add_argument("--window", type=int, default=2500, help="Current corpus campaign window, shared with sequence tuning")
    result.add_argument("--output", help="New study directory")
    result.add_argument("--space", action="store_true", help="Print the search space without reading data or training")
    result.add_argument("--trials", type=int, default=1000)
    result.add_argument("--budget", type=float, default=600)
    result.add_argument("--timeout", type=float)
    result.add_argument("--patience", type=int, default=100)
    result.add_argument("--seed", type=int, default=0)
    result.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    result.add_argument("--max-cache-gib", type=float, default=8)
    result.add_argument("--storage", help="Optuna storage URL; default is SQLite inside the new study directory")
    return result


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    validate_budget(args.budget)
    if args.window <= 0 or args.trials <= 0 or args.patience <= 0 or not 0 < args.max_cache_gib < float("inf") or not 0 <= args.seed < 2**31:
        cli.error("trials, patience and cache size must be positive; seed must be in [0, 2**31)")
    if args.timeout is not None and not 0 < args.timeout < float("inf"):
        cli.error("timeout must be positive and finite")
    if args.space:
        print(json.dumps(dict(categorical=SPACE, ranges=RANGES, conditional=CONDITIONS), indent=2))
        return 0
    if not args.output:
        cli.error("--output is required to start tuning")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
