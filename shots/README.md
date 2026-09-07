# shots — capture stream: full-desktop screenshots

One frame every `shot_every` seconds, plus one 0.18s after every click (via `ctx.shot_req`).
Frames land in `<out_dir>/shots/NNNNN.jpg`, each announced in `events.jsonl`. Grabs the desktop,
not the game window.

## API

```python
shots_stream.run(ctx, grab=None, foreground=None, shot_every=2.5, quality=90)
```

`grab` is a zero-arg callable returning a PIL Image (default `ImageGrab.grab`); `foreground`
returns `(title, pid)` (default Win32). Started by the manager under `--shots [N]`.
