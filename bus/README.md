# bus

The command-bus client and the in-game mod: the single owner of the `commands.txt` →
`twcontrol.jsonl` channel to a running WH3. `send(channel, payload)` appends
`<seq> <channel> <payload>` and blocks for the mod's matching reply.

Channels the mod answers: `find`, `tree`, `roots`, `click`, `dclick`, `eval`, `modeval`, `key`,
`show`, `trim`, `setts`, `ccmd`, `assist`.

## API

- `Bus(cmd_path=CMD_PATH, out_path=OUT_PATH).send(channel, payload="", timeout=30) -> reply dict`
- `_alloc_and_append(channel, payload) -> (seq, offset)` — the cross-process-atomic critical
  section (allocate a globally-monotonic seq, then append), factored out so seq-safety is
  testable without a game.

`CMD_PATH` and `OUT_PATH` come from `common.py` and MUST match the mod compiled into `tw.pack`.
Rebuild the pack with `pack_multi.py` to move them.

## Call stats — `bus_stats.py`

Low-overhead instrumentation on `Bus.send`, on by default; `set BUS_STATS=0` turns it off. Every
call is recorded as `(channel, key, outcome, elapsed_ms)` with `outcome` one of hit / empty /
timeout / error, aggregated in memory per `(channel, key)`, and flushed to the Postgres table
`ops.bus_call_stat` in batches — every 200 calls, every 10s, and on exit, never per call.

The point is to surface **junk calls**: `find`s for components that do not exist on the current
faction, which make the mod full-tree-search and return nothing. `hit` means the reply carried
something; `empty` means the reply arrived with found=false or a null result.

```
python bus/bus_stats.py [--min N] [--top N]
```

Prints total calls, the hit/empty/timeout split, and the junk list — keys with at least `--min`
calls and zero hits, sorted by call count, with wasted ms. Instrumentation failure is always
swallowed with a one-line stderr note; it can never break the bus.
