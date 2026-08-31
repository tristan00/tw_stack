# CLAUDE.md — operating rules for agents working on tw_stack

The README is for humans reading about the project. This file is the contract for agents
changing or operating it. These rules were set explicitly by the owner; do not relax them.


## Models

- Trainable arms (`greedy_catboost`, `greedy_gnn` on actions;
  `greedy_catboost` on blocking screens) are hard dependencies. `marwil_gnn` is retired:
  no trainer, no ranker, no mix membership — only `arms.RETIRED` recognition so historical
  corpus rows stay readable. Do not analyse, retrain, or resurrect it. Missing/unloadable models
  refuse to start; a retrain that raises or reports `trained: false` kills the session;
  predict failures raise `ModelUnavailable`. Never let a run silently play random in a
  trainable arm's place. Only `--cold` runs modelless.
- Two mixes, not one: `--strategies` draws the action arm per decision,
  `--interrupt-strategies` draws the blocking-screen arm. The graph arm has no interrupt
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
- `runctl.py` is the entrypoint. Every run parameter must be stated explicitly on its
 command line — there are no defaults and `run_config.RUN` no longer feeds any. Each
 launch's full param set is recorded to `logs/services/last_launch.json`, the only
 source a harness relaunch may use. The model surface is `--strategies` /
 `--interrupt-strategies`.
- Every launch carries a code version stamp derived from `VERSION` + the git sha. A
 dirty tree refuses to launch unless `--dev-version LABEL` is stated; commit first so
 runs land on an official version. The stamp is recorded in `last_launch.json`,
 `TW_CODE_VERSION`, `collector_versions` (joining `decisions.version_id`),
 postmortems and the trial ledger.
- **Bump `VERSION` in the same commit that brings any change.** An official launch
 refuses when the git sha moved from the last recorded launch but `VERSION` did not
 (`runctl.version_unbumped`, enforced in both `runctl` and the UI launch path) — no
 two different codebases may ever share a version stamp.

## UI design

- **Design first, code second.** Settle a page's information architecture before writing
  code: the one question the page answers, the tabs that separate kinds of information,
  and what each view shows — grounded in what the corpus actually supports (measure the
  data before designing around it). When the design is contested, mock it visually with
  real data and get the owner's read; dumb code with UX polish is worthless.
- **A page owns one question.** Content that answers a different screen's question does
  not belong on it, no matter how available the data is (the start page carries no
  selector/UCB scoring — that lives on the selector screen; one plain link is enough).
- **Minimal prose, low clutter.** Short scope lines, no explainer sentences where a
  column header can say it, no walls of equal-weight tiles. If a section needs a
  paragraph to be understood, redesign the section.
- **Tables: simple self-explanatory cells.** One fact per cell — no chart-in-cell, no
  color-plus-sign stacking, no two-line cells. Color only where the scale explains
  itself (a signed Δ-vs-mean column), never on raw values; if something complex needs
  highlighting, add the simple column that shows it instead. Raw game keys never render
  alone: pair them with reference-schema localized names (`advisor_api/labels.py`), the
  raw key in a tooltip or an optional column.
- **Entities are links.** Lord/campaign names are real anchors (`EntityLink`, one linked
  text column per table, row-click kept as the secondary target); chips, metrics and
  timestamps never link.
- **Inspect in the browser before it lands.** Every UI change is loaded in the running
  dashboard and looked at — real pages, worst-case data (the largest start, a stuck
  campaign), screenshots taken. Building clean and typechecking is not done.
- **Performance is part of the design.** Every view loads and interacts fast on the
  worst case: per-view API slices fetched lazily, charts decimated server-side, heavy
  payloads never refetched on every corpus tick. Measure in the browser (LCP, payload,
  DOM nodes) before and after; the trace is the proof, not the vibe.

## Code style

- Comments and docstrings are forbidden. Match the file's existing style.
- Self-documentation: `python ui_docshots.py` screenshots the run screen and the
  experiment ledger into `docs/ui/` (needs the API up).
