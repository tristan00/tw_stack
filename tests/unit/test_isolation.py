import socket
import sqlite3
import subprocess
import sys

import pytest


@pytest.mark.parametrize("operation", [
    lambda: socket.create_connection(("127.0.0.1", 55433)),
    lambda: sqlite3.connect(":memory:"),
    lambda: subprocess.run([sys.executable, "-c", "pass"]),
])
def test_external_operations_are_blocked(operation, request):
    if any(request.config.getoption(option) for option in ("--run-adhoc", "--run-integration", "--run-gpu-tests")):
        pytest.skip("Default isolation is disabled for explicit opt-in runs")
    with pytest.raises(RuntimeError, match="Default tests"):
        operation()


def test_application_writes_are_blocked(request):
    if any(request.config.getoption(option) for option in ("--run-adhoc", "--run-integration", "--run-gpu-tests")):
        pytest.skip("Default isolation is disabled for explicit opt-in runs")
    with pytest.raises(RuntimeError, match="test-owned"):
        (request.config.rootpath / "forbidden-test-write.txt").write_text("blocked")


def test_fixture_writes_are_isolated(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("small fixture", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "small fixture"
