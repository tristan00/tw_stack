import time
from types import SimpleNamespace

import optuna
import pytest

from advisor.mapgraph import optimize_greedy as O


def test_sequence_trial_uses_live_window_and_returns_raw_reward_metric(monkeypatch, tmp_path):
    result = dict(val_mse=10.0, normalized_val_mse=2.5, val_r2=-1.5)

    def worker(family, window, config, directory, budget, device, seed, max_cache_bytes):
        assert (family, window, device, seed) == ("sequence", 2500, "cpu", 0)
        assert 0 < budget <= 600
        assert config["graph"] == {"graph": "settings"}
        return dict(status="complete", result=result)

    monkeypatch.setattr(O, "run_trial", worker)
    graph = SimpleNamespace(as_dict=lambda: {"graph": "settings"})
    fit = O._fit_trial(None, 2500, dict(device="cpu", seed=0), graph,
                       tmp_path / "trial", time.perf_counter() + 600, lambda _: None)
    assert fit is result


@pytest.mark.parametrize("status,exception", [("pruned", optuna.TrialPruned), ("failed", RuntimeError)])
def test_worker_failure_is_not_scored(monkeypatch, tmp_path, status, exception):
    monkeypatch.setattr(O, "run_trial", lambda *args: dict(status=status, error="worker stopped"))
    with pytest.raises(exception, match="worker stopped"):
        O._fit_trial(None, 2500, dict(device="cpu", seed=0), SimpleNamespace(as_dict=lambda: {}),
                     tmp_path / "trial", time.perf_counter() + 600, lambda _: None)


def test_sequence_budget_cannot_exceed_600():
    with pytest.raises(ValueError, match="600"):
        O.run(budget_s=601)
