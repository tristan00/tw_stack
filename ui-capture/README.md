# ui-capture — capture stream: bus-based panel scraping

The only capture stream that touches the command bus. It tails the newest `script_log` for
`PanelOpenedCampaign` and, on each fire, enumerates the opened panel's options (label, cost,
reward, state, key) over the bus into `ui_components.jsonl`. It self-degrades the moment the bus
goes silent (`bus_unavailable` plus backoff), so it never blocks the bus-free streams.

The panels it knows are the `PANELS` table in `ui_component_recorder.py`; `POLL_PANELS` are the
ones re-read on a timer rather than only on open.

## API

```python
ui_capture_stream.run(ctx, bus=None)     # bus defaults to Bus()
```

Rows carry the shared-clock `t`; the manager routes them to `ui_components.jsonl`. Started under
`--ui` or `--dev`.

## Depends on

`../bus` at runtime. `config` is optional — without it a hardcoded GAME_DIR locates the script log.
