import pytest

from advisor.mapgraph import trial_budget


def test_preparation_and_later_stages_share_one_deadline(monkeypatch):
    clock = iter([100.0, 145.0, 199.0, 200.0])
    monkeypatch.setattr(trial_budget.time, "perf_counter", lambda: next(clock))
    assert trial_budget.remaining(200.0) == 100.0
    assert trial_budget.remaining(200.0) == 55.0
    assert trial_budget.remaining(200.0) == 1.0
    with pytest.raises(TimeoutError):
        trial_budget.remaining(200.0)


def test_expired_deadline_stops_before_more_work(monkeypatch):
    monkeypatch.setattr(trial_budget.time, "perf_counter", lambda: 201.0)
    with pytest.raises(TimeoutError):
        trial_budget.remaining(200.0)
