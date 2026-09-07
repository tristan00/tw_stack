# Project tests

Install requirements-test.txt into the project virtual environment. Run from the repository root:

```powershell
.venv/Scripts/python.exe -m tests
```

This launches pytest with a 20-second deadline covering the entire pytest process, including startup, collection, fixtures, tests, and reporting. Timeout terminates only the test process and returns exit code 124. Automatic third-party plugin loading is disabled and numerical library threads are limited to one. The supervisor writes tests/.results/process.json with measured process wall time and exit code.

Direct pytest also works:

```powershell
.venv/Scripts/python.exe -m pytest
```

Direct pytest has a 20-second watchdog starting at project configuration; use python -m tests to enforce the complete process budget. Both commands collect only tests/unit by default. The default suite can run alongside tuning or runctl: it uses small synthetic inputs, has no model fitting, and does not query the database or GPU.

Default tests reject network connections, SQLite connections, database/model framework imports, and subprocess launches. File mutations are restricted to a unique test temporary directory and tests/.results. Bytecode writes are disabled. These process-local guards prevent accidental application interactions; they are not a security sandbox for untrusted native code. Application processes and their settings are untouched.

Pytest prints slowest phases and writes tests/.results/timings.json with collection time and every test's setup, call, teardown, and total durations. tests/.results/junit.xml provides CI-compatible results. Reports are overwritten on each run and ignored by Git. A forced timeout may interrupt these two reports; process.json is authoritative for the supervised run's outcome.

## Adding tests

Put pure logic checks under tests/unit/test_*.py, using pytest assertions and fixtures. Keep synthetic inputs small and use tmp_path for files. Prefer tests that finish in milliseconds. No live services, model imports, subprocesses, application state changes, or large datasets belong here. A check that pushes the suite over 20 seconds belongs outside the default suite until its cost is reduced. Collection/import time counts too.

## Explicit checks

Framework-heavy checks remain under tests/adhoc; real database and GPU checks remain under tests/integration. Default discovery excludes these directories before importing them, including pytest tests. Explicit paths require the corresponding opt-in flag.

```powershell
.venv/Scripts/python.exe -m pytest --run-adhoc tests/adhoc
.venv/Scripts/python.exe -m pytest --run-integration tests/integration/test_gnn_source.py tests/integration/test_gnn_projection.py
.venv/Scripts/python.exe -m pytest --run-gpu-tests tests/integration/test_gnn_gpu.py
```

These opt-in commands disable default isolation and its watchdog. Run them sequentially, with tuning and runctl stopped. Existing unittest-style classes are executed by pytest.

Full-window tools remain under bench/, outside pytest discovery: gnn_first_step_check.py, gnn_edge_profile.py, gnn_input_profile.py, and gnn_profile.py. They require explicit invocation and may access real data, GPU resources, and application outputs. The first-step tool uses the cancelled 12-edge study's trial-0 parameters as its baseline.
