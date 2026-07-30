# input — capture stream: system-wide keyboard / mouse / focus

Ported faithfully from `record.py:t_input` (working v5 recorder). Polls Win32 for the whole
desktop and emits `focus / move / key_down / key_up / mouse_down / mouse_up` rows. Sets
`ctx.shot_req` on every click (couples input → shots). **Game-independent** — never touches WH3.

SCOPE (unchanged): this is a SYSTEM-WIDE recorder (every key/click in every window). Not narrowed
at write time — that is the user's explicit call, not the recorder's.

## API
```python
input_stream.run(ctx, probes=None)
```
`ctx` (supplied by the manager) duck-types: `emit(row)`, `now()`, `is_running()`, `shot_req`,
`on_error(where, exc)`. `probes` are the three injectable OS reads (cursor / foreground /
keystate); default = real Win32. Tests inject fakes for deterministic input.

## Test (offline, no game)
```
python test_input.py     # PASS: focus/move/key/mouse rows, monotonic clock, shot_req on click
```

## Blocked (needs the live game — deferred)
Nothing in this stream needs the game; live validation happens via the manager's integrated run.
