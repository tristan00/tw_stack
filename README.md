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

Resets the bus files and opens a fresh run dir under `D:\twdata\runs\human\<ts>`:

```powershell
cd D:\tw_stack
D:\totalwar_runner\.venv\Scripts\python.exe -u manager\manager.py --shots 60
```

Leave it running (Ctrl-C stops recording). `--shots 60` = a screenshot every 60s.

### Every recorder flag, and what each one actually captures

| flag | stream | captures | writes to |
|---|---|---|---|
| *(always on)* | `logs` | tails the game's `script_log_*.txt` — **this is the `twstate.lua` firehose**, see below | `<run>\logs\*.tail` |
| *(on unless `--no-decisions`)* | `decisions` | services advisor snapshot/offer/pick requests. **Request-driven: idle unless an advisor writes to `decisions_requests.jsonl`** | `decisions.sqlite`, `decisions_stream.jsonl` |
| `--shots [N]` | `shots` | full-desktop JPEG every `N`s **plus one ~0.18s after every click** (`trigger: interval\|click`) | `<run>\shots\NNNNN.jpg` + rows in `events.jsonl` |
| `--input` | `input` | desktop-wide mouse/keyboard/focus at 20Hz | `events.jsonl` |
| `--ui` | `ui-capture` | on each `PanelOpenedCampaign`, scrapes that panel's **full option list** over the bus (14 panels configured) | `ui_components.jsonl` |
| `--v6-actions` | `actions` | one cco sweep per turn of every lord + settlement's available actions | `actions.sqlite`, `actions_stream.jsonl` |
| `--swap-dirs` | — | rotates the run dir at campaign boundaries; **re-truncates both bus files each time** | — |

Traps, all of which cost real time to rediscover:

- **Flags are exact-token tests** (`manager.py:361-365`). `--shots=15` silently records **no**
  screenshots; the value must be the next argv token: `--shots 15`. Same for `--input=1`, `--ui=1`.
- **Startup truncates the global bus files unconditionally** (`manager.py:376` → `reset_bus_files`),
  with no game check. That is safe against a live game — `twcontrol.lua:816-818` resets its read
  cursor when the file shrinks, and resets `last_seq` when the file's max seq goes backwards — but
  any bus call in flight in another process is lost across it. **Start the recorder before the
  campaign loads; do not restart it mid-play.**
- **Starting a second recorder hijacks the first.** `write_current_pointer` overwrites
  `D:\twdata\runs\human\CURRENT_RUN` with the new, empty dir; a running session then reads the wrong
  run. Kill the old recorder first.
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
Get-Content (Get-Content D:\twdata\runs\human\CURRENT_RUN).Trim().Replace('/','\')\meta.json
```

`shots_enabled / ui_enabled / actions_enabled / decisions_enabled / input_enabled` must read `true`
for what you asked for. Then confirm rows are flowing: `events.jsonl` (shots + input),
`ui_components.jsonl` (expect `{"status":"bus_available"}`), `actions_stream.jsonl`.

If `actions_stream.jsonl` / `errors.log` repeat

```
lua error: attempt to index global 'cm' (a nil value)
```

then **no campaign is loaded** — `cm` only exists in the campaign environment. The game is sitting
at the frontend. This is the fastest engine-side check for "am I actually in a campaign", and it is
not an error to fix.

## 2. Start the session (the actual 40x40)

In a second console:

```powershell
cd D:\tw_stack
D:\totalwar_runner\.venv\Scripts\python.exe -u advisor\session.py 40 40 --factions all --retrain
```

- `40 40` = 40 campaigns x up to 40 turns. Any `N T` works (the standard regression
  bar is `10 40`).
- `--factions all` samples a faction per campaign from every playable start.
- `--retrain` retrains the models once, at campaign 1 start, from all recorded runs
  (log line: `retrained before run 1:`). Omit it to play on the current models.
- Output goes to the console. If you want the UI's live tab to tail the log, redirect
  to a file and write the pointer **without a BOM** (PowerShell `>` writes a BOM which
  the readers tolerate since commit `013c15e`, but clean is better):

```powershell
$log = "D:\twdata\logs\advisor\session_40x40_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
D:\totalwar_runner\.venv\Scripts\python.exe -u advisor\session.py 40 40 --factions all --retrain *> $log
[System.IO.File]::WriteAllText('D:\twdata\logs\advisor\CURRENT_SESSION_LOG.txt', $log + "`n")
```

(Simplest alternative: start the session from the UI — its restart button handles the
log + pointer itself.)

## 3. Watch it — the UI

In a third console:

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

1. **Kill any running recorder**, so it cannot steal `CURRENT_RUN`:

   ```powershell
   Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
     ? { $_.CommandLine -like '*manager.py*' } | % { Stop-Process -Id $_.ProcessId -Force }
   ```

2. **Start the recorder with the human-capture streams on**, from `D:\tw_stack`:

   ```powershell
   D:\totalwar_runner\.venv\Scripts\python.exe -u manager\manager.py --shots 15 --input --ui --v6-actions
   ```

   To leave it running detached with a log (what the UI's own start button does, plus the flags):

   ```powershell
   $log = "D:\twdata\logs\services\manager_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
   Start-Process -FilePath D:\totalwar_runner\.venv\Scripts\python.exe `
     -ArgumentList "-u","manager\manager.py","--shots","15","--input","--ui","--v6-actions" `
     -WorkingDirectory D:\tw_stack -RedirectStandardOutput $log `
     -RedirectStandardError "$log.err" -WindowStyle Hidden
   ```

   It prints the run dir and the live stream list:
   `RECORDING -> D:/twdata/runs/human\<ts>  (streams: ['logs', 'decisions', 'input', 'shots', 'ui-capture', 'actions'])`

3. **Launch Warhammer 3 yourself** (Steam) and sit at the main menu. Confirm the mod loaded — the
   game dir's `lua_mod_log.txt` must list **both**:

   ```
   Mod [script\campaign\mod\twcontrol.lua] loaded successfully
   Mod [script\campaign\mod\twstate.lua] loaded successfully
   ```

   `twcontrol` alone means you are still in the frontend; `twstate` is the campaign-environment mod
   and is the one that produces the event firehose.

4. **Load the campaign.** The `cm is nil` errors stop, and the actions stream begins sweeping.

5. **Play.** Leave a few seconds between distinct actions so they segment cleanly by timestamp, and
   do not let anything else drive the game (no `advisor_driver`, no `nav.py`, no `cco_actions.py` —
   several of those click and mutate the campaign).

6. **Stop** with Ctrl-C, or the kill command in step 1.

### What the firehose gives you

`bus\mod\twstate.lua` arms **~200 campaign event listeners with no filtering** and emits one
`TWSTATE {...}` JSON row per event into the game's script log, which the `logs` stream tails into
`<run>\logs\`. Two properties make it the primary record of what happened:

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

- Session: Ctrl-C in its console, or the UI's kill button (also kills the game).
- Recorder: Ctrl-C. Game: the session owns it; killing the session leaves the game
  up (harmless), the next session start kills/relaunches it.
- The UI's **kill session** button runs `Get-Process -Name Warhammer3 | Stop-Process -Force`
  unconditionally — it will force-kill a campaign you are playing by hand. Do not use it to stop a
  human recording session.

## Where results land

- Run data: `D:\twdata\runs\human\<ts>\` — pointer to the newest at
  `D:\twdata\runs\human\CURRENT_RUN`:

  | file | written by | holds |
  |---|---|---|
  | `logs\*.tail` | `logs` | the `TWSTATE` event firehose + the game's own log (the bulk of the bytes) |
  | `decisions.sqlite` | `decisions` | snapshots, offers, picks — the model's training data |
  | `events.jsonl` | all streams | input rows, shot rows, per-stream status rows |
  | `shots\NNNNN.jpg` | `shots` | full-desktop frames, interval + post-click |
  | `ui_components.jsonl` | `ui-capture` | per-panel option sets at `PanelOpenedCampaign` |
  | `actions.sqlite` | `actions` | per-entity available actions, one sweep per turn |
  | `errors.log` | all streams | every stream failure, with traceback |
  | `meta.json` | manager | which streams were enabled, screen size, `t0_epoch` |
  | `postmortems.jsonl` | advisor | per-campaign end state (advisor sessions only) |

- Session logs: `D:\twdata\logs\advisor\session_*.log` · service logs: `D:\twdata\logs\services\`
- Models: `D:\twdata\models\{global,local,interrupt}`
- Regression baseline to compare against: `D:\twdata\baseline_20260731\baseline.json`
- Bus files (global, truncated at every recorder start):
  `D:\totalwar_runner\data\{commands.txt,twcontrol.jsonl}`
