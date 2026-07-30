# shots — capture stream: full-desktop screenshots

Ported faithfully from `record.py:t_shots`. Grabs the whole desktop: one frame every
`shot_every` seconds plus one 0.18s after every click (via `ctx.shot_req`). Frames →
`<out_dir>/shots/NNNNN.jpg`, each announced in `events.jsonl`. **Game-independent** — grabs the
desktop, not the game window.

## API
```python
shots_stream.run(ctx, grab=None, foreground=None, shot_every=2.5, quality=90)
```
`grab` = zero-arg callable → PIL Image (default `ImageGrab.grab`). `foreground` = () → (title,pid)
(default Win32). Tests inject a tiny in-memory image so no real screen grab happens.

## Test (offline, no game)
```
python test_shots.py     # PASS: click + interval frames as valid JPEGs with matching rows
```

## Blocked (needs the live game — deferred)
None; live validation happens via the manager's integrated run.
