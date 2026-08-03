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

## Stopping things

- Session: Ctrl-C in its console, or the UI's kill button (also kills the game).
- Recorder: Ctrl-C. Game: the session owns it; killing the session leaves the game
  up (harmless), the next session start kills/relaunches it.

## Where results land

- Run data: `D:\twdata\runs\human\<ts>\` (`decisions.sqlite`, `postmortems.jsonl`, shots)
- Session logs: `D:\twdata\logs\advisor\session_*.log`
- Models: `D:\twdata\models\{global,local,interrupt}`
- Regression baseline to compare against: `D:\twdata\baseline_20260731\baseline.json`
