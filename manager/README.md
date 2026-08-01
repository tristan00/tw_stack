# manager — the capture orchestrator

Ported from `record.py:main` + `write_meta`, decomposed. Owns the run directory, the single
shared **T0 clock**, `meta.json`, the thread-safe writers, and the input→shots `shot_req`
coupling. Runs each capture stream (`input` / `shots` / `logs` / `ui-capture` / `actions` / `decisions`) as an
independent daemon thread: record.py's proven "independent failsafe threads" design, so one
stream dying never stops the others and the shared clock is trivially consistent.

Streams are passed **in**, not hardcoded, so the orchestrator has no game/bus dependency and is
fully testable offline. Improvement over the monolith: an unhandled stream crash is **logged to
errors.log** instead of silently killing a daemon thread.

## API
```python
rec = manager.start(out_root, streams, recorder_version="v5", meta_overrides={...})
# ... capture runs in threads ...
rec.stop()          # writes the terminal 'stop' row + joins
```
A stream is `{"run": callable(ctx, **kwargs), "out_file": "events.jsonl", "name": str,
"kwargs": {...}}`. `Ctx` gives each stream: `emit(row)` / `out_dir` / `now()` / `is_running()` /
`shot_req` / `on_error(where, exc)`.

`main()` is a thin CLI that imports the real `../input`, `../shots`, `../logs` repos + `config`
and records until Ctrl-C — the only place with a game/config dependency.

## Test (offline, no game)
```
python test_manager.py
#   UNIT (stubs): run dir + meta + start..stop + shared clock; a crashing stream is logged, siblings survive
#   INTEGRATION: real input+shots+logs vs synthetic inputs -> populated run (events + shots/*.jpg + logs/*.tail)
```

## Live wiring
`main()` runs all streams against the real game; the bus-based `ui-capture` and `actions`
streams are wired in (opt-in via `--ui` / `--v6-actions`), `decisions` is on by default.
