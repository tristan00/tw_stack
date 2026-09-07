# manager — the capture orchestrator

Owns the run directory, the shared T0 clock, `meta.json`, the thread-safe writers, and the
input→shots `shot_req` coupling. Each capture stream runs as an independent daemon thread, so one
stream dying never stops the others and every row shares one clock. An unhandled stream crash is
written to `errors.log` rather than silently killing the thread.

Streams are passed in rather than hardcoded, so the orchestrator itself has no game or bus
dependency.

## API

```python
rec = manager.start(out_root, streams, recorder_version="v7", meta_overrides={...})
rec.stop()          # writes the terminal 'stop' row, then joins the threads
```

A stream is `{"run": callable(ctx, **kwargs), "out_file": "events.jsonl", "name": str,
"kwargs": {...}}`. `Ctx` gives each stream `emit(row)`, `out_dir`, `now()`, `is_running()`,
`shot_req` and `on_error(where, exc)`.

## CLI

```
python manager/manager.py [--dev] [--ui] [--v6-actions] [--shots N] [--input] [--no-decisions]
```

Records until Ctrl-C. `decisions` is on by default; `--dev` additionally turns on the log tailer,
`ui-capture` and `actions`. This is the only part of the package with a game/config dependency.
`runctl up` starts it.
