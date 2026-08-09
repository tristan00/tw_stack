# tw_stack — running it yourself

The exact commands to run a full session (e.g. 40x40) and watch it, with no agent
involved. See `ARCHITECTURE.md` for what the pieces are.

## Prerequisites

- **Python env**: everything runs on the venv interpreter
  `D:\totalwar_runner\.venv\Scripts\python.exe` — no activation needed, call it by
  full path. No environment variables are required: every path (game dir, `D:\twdata`
  artifacts, bus files) is hard-coded in the code.
- **Steam must be running** (game auth). The session kills and launches Warhammer 3
  itself — close nothing by hand, and don't play manually while a session runs.
- **Working directory matters**: run everything from `D:\tw_stack`.
- The mod pack (`bus\dist\tw.pack`) deploys to the game automatically at launch when
  stale. After a mod change: `D:\totalwar_runner\.venv\Scripts\python.exe bus\pack_multi.py build`.

## 1. Start the recorder (always first)

Resets the bus files and opens the run dir:

```powershell
cd D:\tw_stack
D:\totalwar_runner\.venv\Scripts\python.exe -u manager\manager.py --shots 60
```

Leave it running (Ctrl-C stops recording). `--shots 60` = a screenshot every 60s.

**The run dir is fixed at `D:\twdata\runs\human\run`.** It is not timestamped and it is not
rotated — `_new_run_dir` returns that one path every time, so every run appends into the same
`decisions.sqlite` and the same stream files. Campaigns stay separable by `campaign_id`, but do
not expect one run to have its own directory. The `CURRENT_RUN` pointer is **dead**:
`write_current_pointer` is a no-op and `journal.current_run_dir()` ignores the file and returns
the fixed path. Anything still reading `D:\twdata\runs\human\CURRENT_RUN` is reading a file that
does not exist.

### Every recorder flag, and what each one actually captures

| flag | stream | captures | writes to |
|---|---|---|---|
| `--dev` | `logs` | tails the game's `script_log_*.txt` — **this is the `twstate.lua` firehose**, see below | `D:\twdata\stream\logs\*.tail` |
| *(on unless `--no-decisions`)* | `decisions` | services snapshot/target/turn/hash/pick requests. **Request-driven: idle unless something writes to `decisions_requests.jsonl`** | `decisions.sqlite`, `decisions_stream.jsonl` |
| `--shots [N]` | `shots` | full-desktop JPEG every `N`s **plus one ~0.18s after every click** (`trigger: interval\|click`) | `D:\twdata\stream\shots\NNNNN.jpg` + rows in `events.jsonl` |
| `--input` | `input` | desktop-wide mouse/keyboard/focus at 20Hz | `events.jsonl` |
| `--ui` | `ui-capture` | on each `PanelOpenedCampaign`, scrapes that panel's **full option list** over the bus (14 panels configured) | `ui_components.jsonl` |
| `--v6-actions` | `actions` | one cco sweep per turn of every lord + settlement's available actions | `actions.sqlite`, `actions_stream.jsonl` |
| `--swap-dirs` | — | at campaign boundaries: **re-truncates both bus files**, bumps `campaign_index` in `meta.json`, reopens the writers | — |

Traps, all of which cost real time to rediscover:

- **The firehose is NOT always on.** `logs` is gated behind `--dev` (`manager.py:336,351`,
  `logs_enabled = dev_on`). Without `--dev` you get no `TWSTATE` rows at all — nothing under
  `D:\twdata\stream\logs\`
  — and the primary record of what happened simply does not exist. Check the startup line: it must
  print `streams: ['logs', ...]`.
- **Flags are exact-token tests** (`manager.py:331-336`). `--shots=15` silently records **no**
  screenshots; the value must be the next argv token: `--shots 15`. Same for `--input=1`, `--ui=1`.
- **Startup truncates the global bus files unconditionally** (`manager.py:347` → `reset_bus_files`),
  with no game check. That is safe against a live game — `twcontrol.lua:1047-1049` resets its read
  cursor when the file shrinks, and `:1067-1069` resets `last_seq` when the file's max seq goes
  backwards — but any bus call in flight in another process is lost across it. **Start the recorder
  before the campaign loads; do not restart it mid-play.**
- **Never run two recorders.** They no longer fight over a pointer (there isn't one) — they both
  write the *same* fixed run dir, interleaving rows into one `events.jsonl` and both truncating the
  bus files out from under each other. Kill the old recorder first.
- `--swap-dirs` no longer rotates anything, because `_new_run_dir` returns the same fixed path. What
  survives of it is the **bus truncation at every campaign boundary**. Stream files are reopened in
  append mode, so nothing is lost, but the flag's name is a lie.
- `--ui` polls the `construction` and `army_stances` panels over the bus every 1.5s for the whole
  session, and one big panel scrape can hold the shared bus for tens of seconds.
- `--input` is desktop-wide, not game-scoped: it logs keystrokes and foreground window titles from
  every application, in plaintext.
- `--ui` / `--v6-actions` are the v5/v6 capture path. Nothing in the v7 pipeline reads
  `actions.sqlite`, and `ui_components.jsonl` is only consumed by `campaigns/splitter.py`. The v7
  action space lives in `decisions/collect.py`. Turn them on to reverse-engineer UI paths and option
  sets, not to feed models.

### Verify it is actually recording

The run's own `meta.json` records which streams were enabled — check it rather than trusting the
command line:

```powershell
Get-Content D:\twdata\runs\human\run\meta.json
```

`logs_enabled / shots_enabled / ui_enabled / actions_enabled / decisions_enabled / input_enabled`
must read `true` for what you asked for. Then confirm rows are flowing.

**The two bulk streams do not write into the run dir.** `logs` and `shots` both write under
`BULK_ROOT = D:\twdata\stream`, not `<run>\`:

| stream | actually writes to |
|---|---|
| `logs` | `D:\twdata\stream\logs\<source-filename>.tail` |
| `shots` | `D:\twdata\stream\shots\NNNNN.jpg` |

An empty `D:\twdata\runs\human\run\logs\` therefore proves nothing — that directory is never
created. Check `D:\twdata\stream\logs\script_log_*.tail` instead; it should be growing by tens of
MB per minute during play. Everything else (`decisions.sqlite`, `events.jsonl`,
`ui_components.jsonl`, `errors.log`, `meta.json`) does live in the run dir.

If `actions_stream.jsonl` / `errors.log` repeat

```
lua error: attempt to index global 'cm' (a nil value)
```

then **no campaign is loaded** — `cm` only exists in the campaign environment. The game is sitting
at the frontend. This is the fastest engine-side check for "am I actually in a campaign", and it is
not an error to fix.

## 2. Start everything — `runctl.py`

`runctl.py` is the only supported way to start or stop a run. It owns the log paths, the
`CURRENT_SESSION_LOG.txt` pointer and the recorder/session start order.

> **BANNED: launching `advisor/session.py` or `manager/manager.py` directly, or redirecting
> their output. No exceptions.** Copying the command line off a running process is not research
> into how runs are started.
>
> `runctl.start_session()` is the only writer of `CURRENT_SESSION_LOG.txt`; `session.py` never
> writes it. `_live_log()` -> `_live_session()` -> `live_trial()` reads only that pointer and
> hardcodes `running: True`, with no liveness check. A direct launch therefore leaves the pointer
> on the **previous** session: the UI and `train_events` report that dead run as RUNNING and the
> real one is invisible. Nothing errors.

```powershell
cd D:\tw_stack
D:\totalwar_runner\.venv\Scripts\python.exe runctl.py up 40 40 --model catboost --retrain-every 10
```

- `up N T` = N campaigns x up to T turns, and brings up recorder + UI + session in the
  right order. `T` may be a range (`10-40`).
- `--model` picks the backend; `--cfg KEY=VALUE` (repeatable) passes `--nn-KEY` tuning.
- retrain is **on by default** (log line: `retrained before run 1:`); pass `--no-retrain`
  to play on the current models, `--retrain-every N` to refit every N campaigns.
- `--cold` ignores the fitted model entirely (and disables retrain).
- `runctl.py session N T ...` starts only the session; `down` stops everything;
  `status` prints the running services and the pointer age.

**Killing a session banks its trials.** `kill_session()` runs `bank_trials()` → `session.rescore()`
after the kill, so `up` and `down` both flush the outgoing session's generations into the
experiment ledger. This is not optional bookkeeping: a session that is force-killed without
banking loses every trial row it had not already flushed at a retrain boundary. Any new way to
start or stop a run **must** bank on kill — see [The experiment ledger](#the-experiment-ledger).

## 3. Watch it — the UI

`up` already started it on :8777. To run one standalone:

```powershell
cd D:\tw_stack
D:\totalwar_runner\.venv\Scripts\python.exe advisor_ui\ui.py 8779
```

Browse **http://127.0.0.1:8779**. The `live` tab shows session/game/recorder health,
current campaign/turn, and the log tail; `overview`/`timing`/`diplomacy` cover results;
`infrastructure` has the service controls — including **kill session + game** and
**restart** buttons, so after the UI is up you can drive everything from the browser.

## Recording a session you play by hand (no advisor)

Use this to capture human gameplay — e.g. to reverse-engineer an action the advisor cannot yet
offer. Steps 2 and 3 above do not apply: there is no session, and **you** launch the game.

**Order matters.** The recorder truncates the bus files at startup, so it goes first, before the
campaign is loaded.

1. **Kill any running recorder**, so two of them do not write the same run dir and truncate the bus
   out from under each other:

   ```powershell
   Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
     ? { $_.CommandLine -like '*manager.py*' } | % { Stop-Process -Id $_.ProcessId -Force }
   ```

2. **Start the recorder with the human-capture streams on**, from `D:\tw_stack`. `--dev` is what
   turns the firehose on and is the flag you will regret forgetting:

   ```powershell
   D:\totalwar_runner\.venv\Scripts\python.exe -u manager\manager.py --shots 15 --ui --dev
   ```

   To leave it running detached with a log:

   ```powershell
   $log = "D:\twdata\logs\services\manager_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
   Start-Process -FilePath D:\totalwar_runner\.venv\Scripts\python.exe `
     -ArgumentList "-u","manager\manager.py","--shots","15","--ui","--dev" `
     -WorkingDirectory D:\tw_stack -RedirectStandardOutput $log `
     -RedirectStandardError "$log.err" -WindowStyle Hidden
   ```

   It prints the run dir and the live stream list — **check that `logs` is in it**:
   `RECORDING -> D:/twdata/runs/human\run  (streams: ['logs', 'decisions', 'shots', 'ui-capture'])`

   Add `--input` only if you need click paths: it is a desktop-wide plaintext keylogger, not
   game-scoped.

3. **Start `human_poller.py`.** Without it a hand-played campaign records **zero decision points**.
   The `decisions` stream is request-driven — with no advisor writing to `decisions_requests.jsonl`
   it sits idle, so you get the firehose and screenshots but nothing in `decisions.sqlite`: no
   `decision_points`, no `entity_snapshots`, and no `action_offers` (the offer set the model
   scores). The poller supplies those requests: turn/3s, hash/5s, target/10s, snapshot/20s.

   ```powershell
   $log = "D:\twdata\logs\services\human_poller_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
   Start-Process -FilePath D:\totalwar_runner\.venv\Scripts\python.exe `
     -ArgumentList "-u","decisions\human_poller.py","D:/twdata/runs/human/run" `
     -WorkingDirectory D:\tw_stack -RedirectStandardOutput $log `
     -RedirectStandardError "$log.err" -WindowStyle Hidden
   ```

   Pass the run dir explicitly. Start it before the game: until a campaign is loaded it logs
   `WH3 process gone` / `bus timeout` to its `.err` file once per poll, which is expected and
   harmless, and it latches automatically on campaign load — the line to look for is
   `human_poller: turn -> 1.0`. Its error counter freezes at whatever it reached in the frontend;
   a **rising** count once you are in a campaign is the real problem signal.

4. **Launch Warhammer 3 yourself** (Steam) and sit at the main menu. Confirm the mod loaded — the
   game dir's `lua_mod_log.txt` must list **both**:

   ```
   Mod [script\campaign\mod\twcontrol.lua] loaded successfully
   Mod [script\campaign\mod\twstate.lua] loaded successfully
   ```

   `twcontrol` alone means you are still in the frontend; `twstate` is the campaign-environment mod
   and is the one that produces the event firehose. The pack is only auto-deployed by a *session*
   launch, so when you launch by hand check that
   `D:\SteamLibrary\...\Total War WARHAMMER III\data\tw.pack` is newer than everything in
   `bus\mod\`; if it is not, rebuild with `bus\pack_multi.py build`.

5. **Load the campaign.** The `cm is nil` errors stop and the poller starts reporting turns.

6. **Play.** Leave a few seconds between distinct actions so they segment cleanly by timestamp, and
   do not let anything else drive the game (no `advisor_driver`, no `nav.py`, no `cco_actions.py` —
   several of those click and mutate the campaign).

7. **Stop** with Ctrl-C, or the kill command in step 1 — plus the same for `human_poller.py`. Do
   **not** use the UI's kill button; see [Stopping things](#stopping-things).

### Confirming a hand-played session is actually being captured

All three must be true. Any one of them failing is silent:

```powershell
Get-ChildItem D:\twdata\stream\logs\script_log_*.tail | Sort LastWriteTime -Desc | Select -First 1
Get-ChildItem D:\twdata\logs\services\human_poller_*.log.err | Sort LastWriteTime -Desc |
  Select -First 1 | % { Get-Content $_.FullName -Tail 3 }
D:\totalwar_runner\.venv\Scripts\python.exe -c "import sqlite3,time; c=sqlite3.connect('file:D:/twdata/runs/human/run/decisions.sqlite?mode=ro',uri=True); print(c.execute('select count(*) from decision_points where ts>?',(time.time()-300,)).fetchone()[0], 'decision points in the last 5 min')"
```

### What the firehose gives you

`bus\mod\twstate.lua` arms **~200 campaign event listeners with no filtering** and emits one
`TWSTATE {...}` JSON row per event into the game's script log, which the `logs` stream tails into
`D:\twdata\stream\logs\`. Two properties make it the primary record of what happened:

- It is **not player-filtered**. AI factions fire the same listeners, so the log contains examples of
  actions the advisor has never performed. Each row carries `"in_player_turn":true|false` to tell
  them apart.
- On a `SIGNIFICANT` event during the player's turn it also writes a full `dump_player` state
  scrape, so you get before/after state around the action, not just the event.

Measured volume: **4.17 GB / 5.63M rows / 1.58M events over 167 distinct event types in 1h41m of
play** — roughly **50-55 GB/day**. Budget disk accordingly, and mine it with a streaming tally rather
than loading files (individual `.tail` files reach 1.2 GB).

To find what an action emits, grep the tails for its event name, e.g. `UnitDisbanded`,
`CharacterCharacterTargetAction`, `UnitUpgraded`, `FactionJoinsConfederation`. The event proves the
action exists and hands you its context fields; the **offer set** comes from `ui_components.jsonl`
(the panel scrape at `PanelOpenedCampaign`) and the **execution path** from the `ComponentLClickUp`
row plus the `[ui]` path-from-root trace in the same log.

## Stopping things

- Session: `runctl.py down`, or the UI's kill button. **Both bank the session's trials** — Ctrl-C
  in the session's console does not, and silently loses every generation not already flushed at a
  retrain boundary.
- Recorder: Ctrl-C, or the `Stop-Process` one-liner in step 1 of the hand-play section.
- Game: the session owns it. `runctl.py down` and the UI kill button both take it down with the
  session; the next session start kills and relaunches it anyway.
- The UI's **kill session** button runs `Get-Process -Name Warhammer3 | Stop-Process -Force`
  unconditionally — it will force-kill a campaign you are playing by hand. Do not use it to stop a
  human recording session.

## The experiment ledger

`D:\twdata\metrics\experiments.jsonl`, one row per generation (a retrain-to-retrain stretch of
campaigns). A run that plays campaigns without landing a row here is not a tracked run.

Rows are written two ways, and both matter:

- **Live**, by the session itself at each retrain boundary.
- **On kill**, by `session.rescore()` — the UI kill path and `runctl.kill_session()` both call it,
  which is how the final, unflushed generation gets banked.

**The ledger is derived, not source.** `session.py --rescore` rebuilds every row from scratch out
of the sessions' own `session_*.json` reports plus the campaigns' decision rows in
`decisions.sqlite`:

```powershell
D:\totalwar_runner\.venv\Scripts\python.exe advisor\session.py --rescore
```

It is idempotent, takes about a second, and is safe to run while a session is live (it opens the
DB read-only). It archive-moves the old ledger to `D:\twdata\archive\experiments_<ts>\` before
replacing it, and refuses to write an empty ledger. Trials whose campaigns cannot be tied to any
decision rows are dropped with a log line naming them — those are aborted runs that recorded
nothing, not data loss.

So if trial rows go missing — a session force-killed outside `runctl`/the UI, a crash, a new
launcher path that forgot to bank — **rerun `--rescore` and they come back**, as long as the
session reports and `decisions.sqlite` survive. What cannot be recovered is a campaign that never
wrote decision rows at all.

Growth metric, for reading the rows: gain = campaign START (its first `decision_points.campaign`
snapshot) → PEAK (`target_rows`), per `campaign_uuid`. Rows carry `baseline: "pre_decision"`;
anything without that field is an old wrong-baseline row and must not be compared against current
ones. A campaign with no baseline is UNMEASURED, never zero.

## Where results land

- Run data: `D:\twdata\runs\human\run\` — one fixed directory, not per-run (see section 1):

  | file | written by | holds |
  |---|---|---|
  | `decisions.sqlite` | `decisions` | snapshots, offers, picks — the model's training data |
  | `events.jsonl` | all streams | input rows, shot rows, per-stream status rows |
  | `ui_components.jsonl` | `ui-capture` | per-panel option sets at `PanelOpenedCampaign` |
  | `actions.sqlite` | `actions` | per-entity available actions, one sweep per turn |
  | `decisions_requests.jsonl` / `_responses.jsonl` | advisor / poller ↔ recorder | the request bus the `decisions` stream services |
  | `loop_report.jsonl`, `turn_trail.jsonl`, `trace.jsonl` | advisor | per-turn loop record, action trace |
  | `errors.log` | all streams | every stream failure, with traceback |
  | `meta.json` | manager | which streams were enabled, screen size, `t0_epoch` |

- Bulk streams, **outside** the run dir: `D:\twdata\stream\logs\*.tail` (the `TWSTATE` firehose plus
  the game's own logs — the overwhelming bulk of the bytes) and `D:\twdata\stream\shots\NNNNN.jpg`.
- Per-campaign end state: `D:\twdata\runs\human\postmortems.jsonl` — at the runs **root**, not
  inside the run dir (advisor sessions only).
- Experiment ledger: `D:\twdata\metrics\experiments.jsonl` (see above).
- Session logs: `D:\twdata\logs\advisor\session_*.log` · service logs: `D:\twdata\logs\services\`
- Models: `D:\twdata\models\{global,local,interrupt}`
- Regression baseline to compare against: `D:\twdata\baseline_20260731\baseline.json`
- Bus files (global, truncated at every recorder start):
  `D:\totalwar_runner\data\{commands.txt,twcontrol.jsonl}`
