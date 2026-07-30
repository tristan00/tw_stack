# bus

The command-bus client + the in-game mod. A single owner of the `commands.txt` → `twcontrol.jsonl`
channel to a running WH3: `send(channel, payload)` appends `<seq> <channel> <payload>` and blocks
for the mod's matching reply. Channels: `find` / `tree` / `roots` / `eval` / `act`.

Ported from the monolith's `twapi/bus.py` (incl. the `_ProcLock` cross-process seq-lock) + the mod
(`mod/twcontrol.lua`, `mod/twstate.lua`) + `pack_multi.py` (builds `dist/tw.pack`). Only local import
is `errors.py`.

## API
- `Bus(cmd_path=CMD_PATH, out_path=OUT_PATH).send(channel, payload="", timeout=30) -> reply dict`
- `_alloc_and_append(channel, payload) -> (seq, offset)` — the cross-process-atomic critical section
  (allocate a globally-monotonic seq + append), factored out so seq-safety is testable without a game.

`CMD_PATH`/`OUT_PATH` are hard-coded and MUST match the mod compiled into `tw.pack` (currently the
monolith's `data/` dir). Rebuild the mod with `pack_multi.py` to move them.

## Call stats / junk-call measurement (`bus_stats.py`)
Optional, low-overhead instrumentation on `Bus.send`: it records every call as
`(channel, key, outcome, elapsed_ms)` where `outcome ∈ {hit, empty, timeout, error}` and
`key` is the payload (for `find`/`tree` this is the path). Aggregated in memory per
`(channel, key)`, flushed to sqlite in batches (every 200 calls / 10 s + on exit — never
per-call). Purpose: surface **junk calls** — `find`s for components that don't exist on the
current faction (e.g. `great_game_rituals` on Wood Elves) → the mod full-tree-searches and
returns nothing. `hit` = the reply carried something (`result.found` / non-empty `child_ids`
/ non-null result); `empty` = reply arrived but found=false / null.

- **Enable/disable:** ON by default. `set BUS_STATS=0` to turn off. DB path override:
  `set BUS_STATS_DB=<path>` (default `D:/totalwar_runner/data/bus_stats.sqlite`).
- **Read the report:** `python bus_stats.py` — prints total calls, %hit/empty/timeout, and the
  **JUNK list** (keys with `calls>=5` and `hits==0`, i.e. never returned anything), sorted by
  call count, with wasted ms. Flags: `--db <path>`, `--min <n>` (junk threshold), `--top <n>`.
- **Schema** (single table, upsert-accumulates so multiple bus clients sum correctly, WAL):
  `call_stats(channel, key, calls, hits, empties, timeouts, errors, total_ms, last_ts,
  PRIMARY KEY(channel,key))`.
- Instrumentation failure is always swallowed (1-line stderr) — it can never break the bus.

## Tests
- `python test_bus_stats.py` — **offline, no game/bus.** Verifies the classifier on real
  find/eval reply shapes, the sqlite accumulate+flush, the report's junk list, and that
  `Bus.send` records the right outcome (hit/empty/timeout/CTD-error) with `_send_impl` stubbed.
- `python test_bus.py` — **offline, no game.** 5 concurrent processes each append 40 commands to one
  shared file; asserts all 200 seqs are unique and strictly increasing (no collision). This is the
  m0 "bus contention" guarantee: without the `_ProcLock`, concurrent clients collide and the mod
  drops the duplicate → 30s timeout.
- `python test_bus_live.py` — **live (needs a running campaign, e.g. via `launcher`).** Round-trips
  `eval`/faction/turn and checks reply shape + strictly-increasing seqs.
