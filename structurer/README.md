# structurer — FIRST PASS (exploratory data views → schema discovery)

Turns a captured WH3 run's raw logs into structured **data views** on request. This is a
**discovery tool, not the final structurer/DB** — each view we build reveals what the eventual
schema and the *capture* must record. Gaps found this way are logged in `TODO.md`.

**Workflow:** you request a data view / table → a script here generates it from the captured logs
→ we learn what's missing or opaque. We are deliberately **not** committing to the final
structurer schema yet.

## Core (`structurer.py`)
- `player_faction(run_dir)` — the player faction from the `is_human:true` row (never guessed).
- `parse_events(run_dir, turn=, player_only=)` — structured TWSTATE `event` records in
  chronological order, each tagged with `t` (game-time seconds); player-attributed by default.

## Views
- `semantic_actions(run_dir, turn)` — the player's deliberate semantic actions for a turn, in order.
  CLI: `python structurer.py <run_dir> [turn]`

  Two capture-artefact filters (confirmed by an adversarial audit) keep only real decisions:
  rank-up auto-allocated skills are dropped, and events after the player's end-turn are dropped.

## Adding a view
Write a function over `parse_events(...)` (and/or the `ui_components.jsonl` scrape). Keep raw/opaque
values **raw** — no interpretation. Whenever a view can't be produced cleanly from the current
capture, add the gap to `TODO.md` (that's the whole point).

## Data sources in a run dir
| file | holds |
|------|-------|
| `logs/script_log_*.tail` | the TWSTATE event stream (game-time prefixed) — the semantic signal |
| `ui_components.jsonl` | menu scrapes: panels → options with keys / source (local/global) / positions |
| `events.jsonl` | input (mouse/keyboard/focus) + screenshots — needed to correlate clicks to UI |
