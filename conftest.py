from __future__ import annotations

import pytest

# These execute live bus tests at import time (incl. sys.exit); they belong
# to check.py, and must never run during pytest collection even when someone
# passes their paths explicitly.
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
