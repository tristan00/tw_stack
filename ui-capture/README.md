# ui-capture — capture stream: bus-based menu scraping (4th stream)

The only capture stream that touches the command bus. It tails the newest `script_log` for
`PanelOpenedCampaign` and, on each fire, enumerates the opened panel's options (label / cost /
reward / state / key) over the bus into `ui_components.jsonl`. Self-degrades (`bus_unavailable` +
backoff) the instant the bus goes silent, so it never blocks the bus-free streams.

`ui_component_recorder.py` here is **byte-faithful to the working v5 recorder** (the version that
produced 2700 UI rows in the good run). The ONLY edits vs the monolith:
- imports redirected: `twapi.bus`/`twapi.errors` → the `bus` sibling repo + local `errors.py`
- `_OCC_ORDER` inlined (`("occupy","loot","sack","raze")`) — drops the whole `twapi.verbs` cascade
- the `sys.path` bootstrap points at the `bus` sibling repo

14 panels are configured (recruitment, army, technology, diplomacy, construction, rites, skills,
provinces, known_factions, lords_heroes, pre_battle, post_battle_captives, occupation, equipment).

## API
```python
ui_capture_stream.run(ctx, bus=None)     # ctx.emit / now / is_running / on_error; bus defaults to Bus()
```
Rows are stamped with the shared-clock `t`. The manager routes them to `ui_components.jsonl`.

## Test
- **import**: `python -c "import ui_capture_stream"` (sets up the bus path, imports the recorder).
- **live** (needs a running campaign): the manager's `test_integrated_live.py` runs this stream
  with the other three and opens panels via the bus; ui-capture reports `bus_available` and emits
  `menu_open` rows for the scraped panels.

## Depends on
`../bus` (Bus) at runtime. `config` is optional (falls back to a hardcoded GAME_DIR to locate the
script_log).
