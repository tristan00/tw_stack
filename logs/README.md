# logs — capture stream: byte-exact tails of the game's own logs

Tails the game's logs into `<out_dir>/logs/<basename>.tail`. THE rule, which decides whether a
game restart mid-recording is captured or silently gutted:

- a log that **already existed** when we started → skip its history, tail only new appends
- a log **created after** we started (WH3 opens a fresh `script_log_*.txt` per session) → capture
  it whole, from byte 0

## API

```python
logs_stream.run(ctx, log_dirs, poll_every=3.0, own_slack=2.0)
```

`log_dirs` are the directories to scan; the manager passes GAME_DIR plus the APPDATA logs.
`own_slack` is the grace, in seconds, for deciding a log was created just before us rather than by
us. Opens and tails are announced in `events.jsonl`.

Started by the manager under `--dev`.
