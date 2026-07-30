# logs — capture stream: byte-exact tails of the game's own logs

Ported faithfully from `record.py:t_logs`, including THE rule that decides whether a game
restart mid-recording is captured or silently gutted:

- a log that **already existed** when we started → skip its history, tail only new appends
- a log **created after** we started (WH3 opens a fresh `script_log_*.txt` per session) → capture
  it whole, from byte 0

**Game-independent to build/test** — the source dirs, poll interval, and "just-before-us slack"
are parameters, so the whole rule is exercised offline with synthetic files.

## API
```python
logs_stream.run(ctx, log_dirs, poll_every=3.0, own_slack=2.0)
```
`log_dirs` = directories to scan (manager passes GAME_DIR + APPDATA logs). Tails →
`<out_dir>/logs/<basename>.tail`; opens/tails announced in `events.jsonl`.

## Test (offline, no game)
```
python test_logs.py      # PASS: history skipped, new logs from byte 0, byte-exact, rotation
```
This is the exact code path that used to silently gut restarted runs — verified in full offline.

## Blocked (needs the live game — deferred)
Validation against REAL WH3 logs (the mod's TWSTATE output) happens via the manager's live run.
