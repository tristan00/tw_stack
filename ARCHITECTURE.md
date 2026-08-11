# tw_stack — architecture (v7, live)

```
                 ┌────────────── the GAME (WH3 + bus/dist/tw.pack mod) ──────────────┐
                 │                        ▲                                          │
                 │            bus (file command bus — SOLE transport)                │
                 │                        │                                          │
   manager (RECORDER: sole sqlite writer)│              launcher (DRIVER: launch,    │
   streams: logs · input · shots ·       │              executor registry, nav,      │
   ui-capture · actions · decisions ─────┘              interrupts, screen/ps) ──────┘
                 │                                              ▲
   run dirs  D:\twdata\runs\human\<ts>\  + CURRENT_RUN          │ execute picked action
                 │                                              │
   advisor (BACKEND: session → loop → policy/model)  ───────────┘
   strategy portfolio: exploit_tree (E1/E2 CatBoost) · gnn · ruleset · random
   interrupt model · watchdog
   features ← reference/features_db ← D:\twdata\reference\reference.sqlite
                 │
   advisor_ui (FRONTEND: ui.py :8779 — dashboard over decisions.sqlite,
   service control: start/kill session, health)
```

## Projects
- **bus** — file command bus to the in-game lua mod (`bus/mod/`), packed by `pack_multi.py` into
  `bus/dist/tw.pack` (deployed to the game by the launcher). Sole game I/O.
- **manager** — the recorder. Owns every bus READ and all sqlite writes. Runs the streams
  (`logs` always-on — drives campaign-swap detection; `input`, `shots`, `ui-capture`, `actions`
  opt-in; `decisions` on by default) into the run dir; writes the `CURRENT_RUN` pointer.
- **advisor** — the backend. `session.py N T` plays N campaigns × T turns: `loop.py` per-turn
  decision loop, `policy.py` selection, `model.py` E1/E2 global+local ranking,
  `interrupt_model.py` for blocking screens, `watchdog.py`, `analyze.py`/`efficiency.py` reports.
  `reference/` is the offline game-data lookup layer (`features_db.py`, rebuilt by
  `build_reference.py` from the WH3 packs). `mapgraph/` is the graph ranker — one model, no
  version suffix; it lives here because it is an advisor model, not a peer of the recorder.
- **advisor_ui** — the frontend. `ui.py [port]` (:8779 in production): decision browser, run
  history, timeline, blocking menus, infrastructure health, session start/kill.
- **launcher** — the driver. Game lifecycle (`bus_launcher.py`, `launcher.py`), the executor
  registry (`executor.py`, 18 action types incl. diplomacy), navigation, interrupt handling,
  PowerShell capture/input bridge (`ps/`). `config.py` is the only file allowed absolute paths.
- **decisions** — the data layer: `store.py` (decisions.sqlite schema: decision points, offers,
  taken+confirmation law, interrupts, target rows), `collect.py`, `journal.py`.
- **campaigns** — the campaign-boundary splitter kernel (used by manager and the logs stream).
- **input / shots / logs / ui-capture** — recorder stream packages (code only).

## Rules
1. Only the **bus** talks to the game.
2. The **manager** owns bus reads and sqlite writes; the **launcher** executes; the **advisor**
   decides; the **UI** reads.
3. An action is taken only when `executed AND confirmed` — unverified clicks are voided.
4. **No logs, data, DBs, or models in this repo.** Everything lives under `D:\twdata`:
   `runs\` `models\{global,local,interrupt}\` `reference\` `logs\{advisor,launcher,services}\`
   `scratch\` `tmp\catboost\` `repo_archive\`.
