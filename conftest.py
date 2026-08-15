from __future__ import annotations

import pytest

collect_ignore = [
    "bus/test_bus.py",
    "bus/test_bus_live.py",
    "bus/test_bus_stats.py",
]


@pytest.fixture
def fail():
    problems = []
    yield problems.append
    if problems:
        pytest.fail("\n  ".join([""] + problems))
