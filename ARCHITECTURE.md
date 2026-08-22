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
   run dir  <TWDATA>/runs/human/run/  + CURRENT_RUN             │ execute picked action
                 │                                              │
   advisor (BACKEND: session → loop → policy/model)  ───────────┘
   action mix: greedy_catboost (argmax of the E1-E2 advantage) · marwil_gnn (MARWIL/AWR
   on the graph encoder) · greedy_gnn (argmax of one reward regression on the same
   encoder) · ruleset · random
   interrupt mix: greedy_catboost (the catboost interrupt model) · ruleset · random
   watchdog
   features ← reference/features_db ← <TWDATA>/reference/reference.sqlite
                 │
   advisor_api (FRONTEND: :8777 — typed JSON API over decisions.sqlite + SSE,
   serving the built client in ui/; service control: start/kill session, health)
```

## Projects
- **bus** — file command bus to the in-game lua mod (`bus/mod/`), packed by `pack_multi.py` into
  `bus/dist/tw.pack` (deployed to the game by the launcher). Sole game I/O.
- **manager** — the recorder. Owns every bus READ and all sqlite writes. Runs the streams
  (`logs` always-on — drives campaign-swap detection; `input`, `shots`, `ui-capture`, `actions`
  opt-in; `decisions` on by default) into the run dir; writes the `CURRENT_RUN` pointer.
- **advisor** — the backend. `session.py N T` plays N campaigns × T turns: `loop.py` per-turn
  decision loop, `policy.py` selection, `model.py` E1/E2 global+local ranking,
  `interrupt_model.py` for blocking screens, `watchdog.py`.
  `reference/` is the offline game-data lookup layer (`features_db.py`, rebuilt by
  `build_reference.py` from the WH3 packs). `mapgraph/` holds the graph models: `net.py`/`train.py`/`rank.py` are
  `marwil_gnn`, `greedy_net.py`/`greedy_train.py`/`greedy_rank.py` are `greedy_gnn` on the
  same encoder and corpus cache; no version suffix. They live here because they are advisor
  models, not peers of the recorder.
- **advisor_api** — the frontend's server half. `python -m advisor_api.app [port]` (:8777):
  a typed JSON API over the corpus, an SSE channel that emits when the corpus grows, and
  the built client served from `ui/dist`. Pydantic models are the contract; the client's
  TypeScript types are generated from the OpenAPI document it publishes, so the two cannot
  drift silently. Every endpoint is bounded and memoized on a corpus stamp.
- **ui** — the frontend's client half. React + Vite + TypeScript + TanStack Table, built to
  `ui/dist` and served by `advisor_api`. Five destinations — run, campaigns, decisions,
  models, infra — each answering one question, with drill-down to a campaign or a decision.
  Every view is a URL. `npm run check` typechecks it, verifies every colour token against
  WCAG in both themes, and builds it; `check.py` runs that as the `client` check.
- **launcher** — the driver. Game lifecycle (`bus_launcher.py`, `launcher.py`), the executor
  registry (`executor.py`, 18 action types incl. diplomacy), navigation, interrupt handling,
  PowerShell capture/input bridge (`ps/`). `config.py` is the only file allowed absolute paths.
- **decisions** — the data layer: `store.py` (decisions.sqlite schema: decision points, offers,
  taken+confirmation law, interrupts, target rows), `collect.py`, `journal.py`.
- **campaigns** — the campaign-boundary splitter kernel (used by manager and the logs stream).
- **debugging** — read-only tools for reading a run after the fact. `timeline.py` merges
  every stamped source (service logs, the store, the run-dir streams, panel dumps) into one
  millisecond-ordered file and prints its path. See `debugging/README.md` for the
  timestamp contract every producer is held to.
- **input / shots / logs / ui-capture** — recorder stream packages (code only).

## Rules
1. Only the **bus** talks to the game.
2. The **manager** owns bus reads and sqlite writes; the **launcher** executes; the **advisor**
   decides; the **UI** reads.
3. An action is taken only when `executed AND confirmed` — unverified clicks are voided.
4. **No logs, data, DBs, or models in this repo.** Everything a run produces lives under
   `TWDATA` (see README): `runs/` `models/{global,local,interrupt,mapgraph,mapgraph_greedy}/`
   `reference/` `logs/{advisor,launcher,services}/` `scratch/` `tmp/catboost/` `repo_archive/`.
   The exception is `rules/` — a rule set is an INPUT that decides how the agent plays, so
   a run is not reproducible elsewhere if it lives outside the checkout. `<TWDATA>/rules/`
   is still searched first, as a local override for trying a variant.

## Lifecycle & guardrails
- **A campaign kills the game itself** the moment its fate is sealed — defeat, growth gate,
  stall, or any error. The single kill site is `run_campaign`'s `finally`, and the kill
  confirms the process is gone before it returns. **The next campaign always boots fresh**
  (presave restore → `load_save`, or `start_game`) and never references, recovers, or
  cleans up after the previous one — not even a game sitting at the main menu.
- **Trainable arms are hard dependencies.** A model gate at session start refuses to run if
  any trainable arm in either mix (`greedy_catboost`, `marwil_gnn`, `greedy_gnn` on
  actions; `greedy_catboost` on blocking screens -- the graph arms have no interrupt model)
  cannot load a usable model; a retrain that raises or reports `trained: false` kills the session;
  a predict failure raises `ModelUnavailable` and ends the run. Only `--cold` runs
  modelless. Retraining is opt-in (`--retrain-every N`); a launch never refits by default.
- **Stalls die fast.** A turn whose end-turn cannot advance within the settle budget is a
  verdict (`turn_stalled`), a controlless overlay persisting across resolve sweeps is a
  verdict, an unclaimed clickable screen across two passes is a verdict — each ends the
  campaign immediately instead of waiting anything out. Screens the handlers cannot clear
  are panel-handling bugs and get hardcoded per-screen rules (exact title + node paths),
  never early generalisation.
- **Every wait and retry logs its use and outcome** — `WAIT`/`TRY`/`PHASE` lines via
  `common.wait/waitlog/trylog/phaselog`, and all service output is ISO-timestamped by
  `common.install_stamped_logs()`. Fixed sleeps are converted to condition-polls capped at
  the old duration; wait budgets only ever go down, and never to accommodate slow interrupt
  handling — slow click-through is the thing to fix.
- The exploit scorer (backend) is fixed and internal (`advisor/backends.py`); the CLI's only
  model surface is `--strategies`. `run_config.RUN` is the authoritative run description
  for both `runctl up` and `runctl harness`, the built-in supervisor that kills and
  relaunches a dead, stalled, or non-progressing session (liveness + log freshness +
  a last-real-work check on turn/retrain markers, with a relaunch cooldown; pause it
  with the `HARNESS_OFF` flag file in the twdata root).
