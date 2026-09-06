CatBoost training input verification, 2026-09-06

The main trainer and tuner now stream selected decisions through a temporary row spool into a native CatBoost Pool. Database reads hold at most 500 decision headers per batch; the spool holds at most 512 feature dictionaries before writing them to disk. Numeric input uses float32, and categorical strings share encoded values. The temporary file closes on success or failure.

Feature extraction skips entities without offers when no state-feature sink is requested. Taken-row hydration omits the unused offer table. Campaign keys restrict decisions, targets, and battle attribution to the requested window. Training slices the native Pool for validation, and tuning prepares both split Pools once for reuse across trials.

Full corpus check:

| Measurement | Result |
|---|---:|
| Requested campaign window | 4,000 |
| Campaigns with eligible decisions | 3,999 |
| Decisions read | 186,523 |
| Training rows | 186,419 |
| Numeric / categorical features | 246 / 65 |
| Load including Pool construction | 415.18 seconds |
| Peak process memory during load | 890.56 MiB |
| Smoke training | 5 trees, 1.2 seconds |
| Training / validation rows | 159,924 / 26,495 |
| Peak process memory including smoke training | 894.39 MiB |

The full load was unprofiled. Concurrent machine activity can affect wall time. This verifies loading, splitting, fitting, and prediction over the actual window; the five-tree smoke fit does not measure a complete production training run.

A separately profiled 1,000-decision comparison measured 7.92 seconds / 245.99 MiB for the original dictionary loader and 6.27 seconds / 124.55 MiB for the new native-Pool loader. The new measurement includes Pool construction; the original stops at dictionaries. These small-sample measurements do not establish a full-window speedup percentage.

The baseline comparison against commit `3fa3235e7e9d1abd711724e90c032fbcd77b517d` matched every feature dictionary, label, campaign group, confirmation flag, and prediction on 1,500 real decisions. Five unit tests cover missing and late categorical values across spool batches, categorical encoding, cleanup after errors, optional state-feature sinks, saved-model inference, legacy matrix input, and tuning Pool reuse. `git diff --check` passed.

Reproduce from the repository root:

```powershell
.venv/Scripts/python.exe -m unittest advisor.test_catboost_input -v
.venv/Scripts/python.exe bench/catboost_input_check.py 1500
.venv/Scripts/python.exe bench/catboost_input_profile.py --pool --fit --window 4000 --output bench/catboost-window4000
.venv/Scripts/python.exe advisor/model.py report --window 4000
```

Production training accepts `advisor/model.py train --window 4000`. Tuning accepts `advisor/optimize_catboost.py main --window 4000 --trials N`. The shared campaign-window default remains unchanged.
