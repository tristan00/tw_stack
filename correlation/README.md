# correlation

Align a run's input stream (`events.jsonl` clicks) to its game-log stream (`script_log` `[ui]`
events) on one timeline: solve the recorder→game time offset, then match each click to the component
it hit (time window AND inside the component's on-screen rect).

## API
- `correlate(run, only_files=None) -> {run, clicks, ui_events, offset, matched, match_rate, samples}`
- primitives: `solve_offset`, `match_click`, `parse_ui_events`, `load_events` (ported verbatim from
  the monolith's `extract.py`). Standalone, stdlib only.

## Notes
- `match_rate < 100%` is expected and correct: right-click map moves/attacks log **no** UI component,
  so those clicks have nothing to match.
- Multi-campaign runs: pass a session's log(s) as `only_files`; clicks are clipped to that session's
  game-time span before solving (the game clock restarts each launch).

## Test (offline, no game)
`python test_correlation.py` — runs on two real v5 session logs from `20260726_113146`. Gate: offset
deterministic; clicks resolve to *specific* components (not just `root`); at least one UI-heavy
session aligns ≥ 80%; and offsets are physically consistent (a later session's game clock restarts,
so its offset is more negative). Note: per-session `match_rate` reflects the UI-vs-map click mix
(map moves log no component) — **not** alignment correctness — so it is not gated per session.
