# ui-enum

Enumerate a game UI panel into its options with stable ids, over the bus `find` channel. Turns the
raw `{child_ids, child_contexts}` a `find` returns into `[{id, key, context}]`, where `key` is the
bare game key recovered by `idmatch` (a `Cco…` context's key, or an id with row/card wrappers
stripped). The shared READ capability the recorder's menu-capture consumes.

## API
- `parse_find(result) -> {panel, found, count, options:[{id, key, context}]}` — **pure**, offline.
- `enumerate_panel(panel, bus_client=None, timeout=15) -> {...}` — sends `find` over the bus.

Depends on the sibling `bus` repo (library import for now; a service boundary later) and local
`idmatch.py`.

## Tests
- `python test_ui_enum.py` — **offline, no game.** Parses a canned `find` result and checks stable-key
  extraction (context key, `_recruitable`/row wrappers stripped, plain ids pass through) and that a
  result with no child list reports `found=False`.
- `python test_ui_enum_live.py` — **live (needs a running campaign).** Enumerates `hud_campaign` and
  asserts real options incl. known children (`faction_buttons_docker`, `radar_things`).
