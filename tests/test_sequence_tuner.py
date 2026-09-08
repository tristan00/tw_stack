import json

import optuna
import pytest
import torch

from advisor.mapgraph import optimize_greedy as O
from tests.test_sequence_train import configuration, examples


@pytest.fixture
def isolated_tuner(monkeypatch, tmp_path):
    study = optuna.create_study(direction="minimize")
    monkeypatch.setattr(optuna, "create_study", lambda **kwargs: study)
    monkeypatch.setattr(O, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(O, "_storage", lambda: None)
    monkeypatch.setattr(O.T, "load_walk_source", lambda **kwargs: {})
    monkeypatch.setattr(torch.cuda, "reset_peak_memory_stats", lambda: None)
    monkeypatch.setattr(torch.cuda, "max_memory_allocated", lambda: 0)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)
    return study


def test_unexpected_training_error_stops_study(isolated_tuner, monkeypatch):
    def fail(*args):
        raise RuntimeError("broken tensor operation")

    monkeypatch.setattr(O, "_fit_trial", fail)
    with pytest.raises(RuntimeError, match="broken tensor operation"):
        O.run(trials=5)
    assert len(isolated_tuner.trials) == 1
    assert isolated_tuner.trials[0].state == optuna.trial.TrialState.FAIL


@pytest.mark.parametrize("error", [TimeoutError("deadline"), torch.cuda.OutOfMemoryError("GPU capacity")])
def test_expected_resource_limits_prune_without_switching_models(isolated_tuner, monkeypatch, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(O, "_fit_trial", fail)
    assert O.run(trials=2) == 2
    assert len(isolated_tuner.trials) == 2
    assert all(t.state == optuna.trial.TrialState.PRUNED for t in isolated_tuner.trials)
    assert all(t.user_attrs["reason"] == str(error) for t in isolated_tuner.trials)


def test_preparation_error_stops_study(isolated_tuner, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("invalid decision order")

    monkeypatch.setattr(O.T, "walk_source", fail)
    with pytest.raises(ValueError, match="invalid decision order"):
        O.run(trials=5)
    assert len(isolated_tuner.trials) == 1


def test_synthetic_trial_prepares_and_saves_matching_best(isolated_tuner, monkeypatch):
    from advisor.mapgraph.source import _pack, GraphView
    datas, ys, groups = examples()
    block = _pack(datas)
    datas = [GraphView(block, i) for i in range(len(datas))]
    rows = [dict(data=data, y=y, campaign_id=group, decision_id=i)
            for i, (data, y, group) in enumerate(zip(datas, ys, groups))]
    monkeypatch.setattr(O.GT, "MIN_ROWS", 1)
    monkeypatch.setattr(O.T, "walk_source", lambda *args, **kwargs: dict(examples=list(reversed(rows)), metrics={}))
    monkeypatch.setattr(O.SC, "suggest", lambda trial: configuration())
    assert O.run(trials=1, budget_s=30) == 0
    trial = isolated_tuner.best_trial
    with open(trial.user_attrs["artifacts"] + "/fit.json", encoding="utf-8") as file:
        fit = json.load(file)
    assert trial.value == fit["val_mse"]
    assert trial.user_attrs["r2"] == fit["val_r2"]
    checkpoint = torch.load(trial.user_attrs["artifacts"] + "/checkpoint.pt", weights_only=False)
    assert checkpoint["decisions"] == list(range(30))
    assert not block


def test_study_deadline_limits_trial(isolated_tuner, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(O.time, "perf_counter", lambda: now[0])

    def load(**kwargs):
        now[0] = 104.0
        return {}

    def fit(trial, source, cfg, graph_config, directory, deadline, log):
        assert deadline == 110.0
        raise TimeoutError("deadline")

    monkeypatch.setattr(O.T, "load_walk_source", load)
    monkeypatch.setattr(O, "_fit_trial", fit)
    assert O.run(trials=1, budget_s=600, timeout_s=10) == 2


def test_graph_worker_receives_trial_deadline(monkeypatch):
    from advisor.mapgraph import source as source_module

    def walk(source, graph_config, **kwargs):
        assert kwargs["deadline"] == 123
        return dict(examples=[])

    monkeypatch.setattr(O.T, "walk_source", walk)
    assert source_module._graphs(("unused", [], {}, 123)) == dict(examples=[], block={})
