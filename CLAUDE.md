# CLAUDE.md — operating rules for agents working on tw_stack

The README is for humans reading about the project. This file is the contract for agents
changing or operating it. These rules were set explicitly by the owner; do not relax them.


## Models

- Trainable arms (`greedy_catboost`, `marwil_gnn`, `greedy_gnn` on actions;
  `greedy_catboost` on blocking screens) are hard dependencies. Missing/unloadable models
  refuse to start; a retrain that raises or reports `trained: false` kills the session;
  predict failures raise `ModelUnavailable`. Never let a run silently play random in a
  trainable arm's place. Only `--cold` runs modelless.
- Two mixes, not one: `--strategies` draws the action arm per decision,
  `--interrupt-strategies` draws the blocking-screen arm. The graph arms have no interrupt
  model, so the interrupt mix is `greedy_catboost` / `random` / `ruleset` only.
- The GNN trains on CUDA. A CPU-only torch wheel is a broken environment to fix, never a
  fallback to accept.
- **Never retrain the owner's models unrequested.** runctl has NO parameter defaults:
  training happens only when the command line explicitly passes `--retrain-every N`
  (with an explicit `--retrain-first`/`--no-retrain-first` when N > 0). Harness
  relaunches force retrain-first off and replay only the recorded launch params.
- Never delete corpus data without an explicit order, and back up first when ordered.

## Campaign lifecycle

- A campaign kills the game itself the moment its fate is sealed; the kill confirms the
  process is dead. The next campaign always boots fresh and never references, recovers,
  or cleans up after the previous one — not even a game sitting at the main menu.
- No dual handling: one kill at end, one boot at start. Do not add code that handles
  "game might be up or might be down".
- Stalls are verdicts, not things to wait out: end-turn that cannot advance within the
  settle budget, controlless overlays persisting across sweeps, unclaimed clickable
  screens — each ends the campaign in seconds.
- Restart the stack only at campaign boundaries unless the run is already down or the
  owner says otherwise.

## Waits and tries

- **Never add waits or retries.** Budgets only go down, and only with the owner's
  approval. Never extend or pause a stall/settle budget to accommodate slow interrupt
  handling — slow click-through is the thing to fix.
- Every wait and try logs its use and outcome: `common.wait` / `waitlog` / `trylog` /
  `phaselog`, never bare `time.sleep`. All service output is ISO-timestamped
  (`common.install_stamped_logs()`); "where did the seconds go" must always be one grep.
- Fixed settles become condition-polls capped at the old duration. The canonical
  screen-reset before every action (deselect + CloseAllPanels) always fires — stay on
  the player path; only diagnostic machinery is skipped, and only when the probe shows
  nothing to handle.
- Never remove or skip action classes for speed. The wins live inside actions.

## Screens

- Every `unhandled_screen` is a panel-handling bug, never environmental noise. Diagnose
  from the screen dumps (`<TWDATA>/runs/human/screens`) and shots; fix with a HARDCODED
  per-screen rule — exact title text and node paths (see the White Tiger dismisser in
  `nav.find_dismiss_buttons`). Collect cases before generalising; never generalise early
  by layout or parent type.

## Verification

- Verify, don't assume: after changes run `check.py` (22 offline gates) and `pytest -q`
  (all green is the bar), and verify behavior in the live run, not just syntax.
- The API test suite assumes a quiet system; single-row mismatches while the run is
  writing are races — re-run the test before treating it as a failure.
- `runctl.py` is the entrypoint. Every run parameter must be stated explicitly on its
  command line — there are no defaults and `run_config.RUN` no longer feeds any. Each
  launch's full param set is recorded to `logs/services/last_launch.json`, the only
  source a harness relaunch may use. The model surface is `--strategies` /
  `--interrupt-strategies`.

## Code style

- Comments and docstrings are forbidden. Match the file's existing style.
- Self-documentation: `python ui_docshots.py` screenshots the run screen and the
  experiment ledger into `docs/ui/` (needs the API up).
