from __future__ import annotations

import pytest

collect_ignore = ["bus/test_bus_live.py"]


@pytest.fixture
def fail():
    problems = []
    yield problems.append
    if problems:
        pytest.fail("\n  ".join([""] + problems))
