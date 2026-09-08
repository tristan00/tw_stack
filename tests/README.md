# Tests

One folder, pytest only. Run from the repository root:

```powershell
.venv/Scripts/python.exe -m pytest
```

The whole run has a 20-second budget covering startup, collection and tests. Overrunning it kills the run with exit code 124. Requirements are in `requirements-test.txt`.

## The suite

A test is in the suite unless its file carries the skip mark:

```python
pytestmark = pytest.mark.skip("not part of the suite")
```

Suite tests use small synthetic inputs, fit no models, and touch neither the database nor the GPU. Everything else is marked and reports as skipped.

## Rules

A commit runs the suite and nothing else. A marked file is never part of a commit, push, or verification, and editing one does not license running it.

Marked files are not a suite and not the user's test process. They reach the real database, real models, and the GPU, so tuning and runctl must be stopped first. An agent that wants one runs it alone, on its own responsibility, and never reports what it found: no pre-existing failure raised as a finding, no note left for later, no re-running until it passes.

A test that fails is fixed or deleted in the same change.

## Adding tests

Pure logic goes in `tests/test_*.py` unmarked. Keep inputs small, use `tmp_path` for files, prefer milliseconds, and remember import time counts against the budget. No live services, model imports, subprocesses, application state changes, or large datasets. Anything heavier gets the skip mark.
