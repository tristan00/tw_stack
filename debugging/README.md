# debugging — post-hoc tools for reading a run

Tools here answer "what actually happened, in what order". They read only; nothing here
touches the game, the bus, or the corpus.

## timeline.py

Merges every stamped source of a run into one millisecond-ordered file and prints its path.

```
python debugging/timeline.py [--start T] [--end T] [--out PATH] [--run DIR]
                             [--only k,k] [--exclude k,k] [--limit N]
```

- `--start` / `--end` — an epoch, `YYYY-MM-DDTHH:MM:SS[.mmm]`, `HH:MM:SS[.mmm]` (today), or
  a relative `-90s` / `-5m` / `-2h`. Default start is the last `CAMPAIGN n/N` marker in the
  session log, so the bare command gives you the campaign in flight; default end is now.
- `--out` — default `<TWDATA>/debug/timeline_<stamp>.txt`. The path is printed on stdout and
  returned by `build()`, so `$(python debugging/timeline.py)` is a usable file name.
- `--only` / `--exclude` — filter on the kind column. The header prints a count per kind;
  `log_tail` (the dev log tailer) and `wait` usually dominate, and dropping `log_tail` is the
  common first move.
- `--limit` — cap the rows. A cap is never silent: the header says how many were dropped.

Each row is `HH:MM:SS.mmm  +delta  source  kind  detail`.

Sources merged, all reduced to one epoch clock:

| source | comes from |
| --- | --- |
| `session` | `<TWDATA>/logs/advisor/session_*.log` and `.err` — turns, actions, WAIT/TRY/PHASE, launches, ucb tables |
| `manager` `ui` `analytics` `harness` | the service logs under `<TWDATA>/logs/services` |
| `store` | `decisions.sqlite`: decisions and their timings, picks and scores, taken, interrupts, rpc, postmortems, diplomacy, ucb picks |
| `action` | the per-action stderr captured into `taken.diagnostics`, one row per stamped line |
| `trace` `turn_trail` `loop` `locomotion` `clear_screen` `post_attack` `dstream` `events` | the run-dir jsonl streams |
| `dev_events` `dev_actions` | the dev streams under `<TWDATA>/logs/dev` |
| `screens` | panel dumps in `runs/human/screens` and `unhandled_screens.jsonl` |
| `errors` | the manager's `errors.log` |

## The timestamp contract

A source is only as useful here as its clock, so:

- Every service line is stamped by `common.install_stamped_logs()` and rendered by
  `common.stamp()` — one definition, ISO local to the millisecond, rounded not truncated.
- Every manager stream row carries an absolute `ts` (added by the writer when the producer
  does not set one). The older relative `t` stays for the campaign splitter; the timeline
  falls back to `t` plus `meta.json`'s `t0_epoch` only when `ts` is absent.
- Action stderr captured into `taken.diagnostics` is stamped per line as it is captured, so
  the waits inside an action can be placed against the run's other sources.

Anything that logs without a millisecond stamp cannot be read here. Fix the source rather
than teaching this tool to guess.
