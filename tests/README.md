# Central test suite

Run from the repository root:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -t . -v
```

The default suite runs the GNN and CatBoost unit tests. Database and CUDA checks are skipped unless explicitly enabled. Discovery does not launch tuning studies or full-window benchmarks.

Run only the GNN unit tests:

```powershell
.venv/Scripts/python.exe -m unittest tests.unit.test_gnn_input tests.unit.test_gnn_edges -v
```

Database integration checks compare projected inputs with gameplay hydration and serial graph construction with parallel construction:

```powershell
$env:TW_RUN_INTEGRATION = '1'
.venv/Scripts/python.exe -m unittest tests.integration.test_gnn_projection tests.integration.test_gnn_source -v
Remove-Item Env:TW_RUN_INTEGRATION
```

The GPU check validates tensor batching and source-storage release:

```powershell
$env:TW_RUN_GPU_TESTS = '1'
.venv/Scripts/python.exe -m unittest tests.integration.test_gnn_gpu -v
Remove-Item Env:TW_RUN_GPU_TESTS
```

Run integration checks and benchmarks sequentially, with the tuning study stopped. The default unit suite uses CPU only.

Performance tools remain under `bench/` because they measure real workloads rather than run during test discovery:

- `gnn_first_step_check.py`: full-window startup and first optimizer-step measurement using trial 0 from `bench/gnn-study-12edges-window2500-20260906-162344/trials.json`. Requires that saved study, the database, CUDA, and a new output directory.
- `gnn_edge_profile.py`: per-relation calibration and graph-size measurements. Calibration writes `advisor/mapgraph/edge_defaults.json`.
- `gnn_input_profile.py`: input-path CPU profiling.
- `gnn_profile.py`: phase-level profiling for a selected workload.

The old `gnn_startup_check.py` was superseded by the full-window check. The standalone projection and parallel checks now live in the integration suite. The old tuner smoke script was removed; the suite checks GPU-budget calculation and tuner edge configuration without starting studies.
