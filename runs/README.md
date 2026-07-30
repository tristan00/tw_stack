# runs

Derive the valid recorded runs and their identity from each run's own data, on demand. No stored
catalog (nothing to go stale or be faked); the filesystem is re-read each call.

## API
- `list_valid_runs(roots=None) -> [record]` — runs that pass the v5 validity gate.
- `list_runs(roots=None) -> [record]` — every run with its record (valid + invalid + reasons).
- `get_run_data(run_id, roots=None) -> record | None`.

`record = {run, path, recorder_version, player_faction, campaigns[], max_turn, streams{}, valid, reasons[]}`

## Valid v5 run
`recorder_version == "v5"` AND `events.jsonl` + `ui_components.jsonl` + a `logs/script_log*.tail`
present AND a player faction derivable from the log's `{"is_human":true}` rows. Identity is never
guessed — a run with no derivable faction is UNKNOWN and excluded with a reason. Multi-campaign runs
are segmented; `player_faction` is set only when there is exactly one campaign (else see `campaigns`).

## Runs root
Default `D:/twdata/runs/human`. Override via `$TW_RUNS_ROOT` (os.pathsep-separated) or `roots=`.

## Test (offline, no game)
`python test_runs.py` — runs against the real corpus. Asserts: `20260719_223310` (unversioned) and
`20260725_043045` (`dev`) are excluded with reasons; every valid run is v5 + has all streams + a
derivable faction; no UNKNOWN-identity run is ever valid. Prints the full valid/invalid partition.
