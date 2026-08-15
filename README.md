# tw_stack

An agent that plays Total War: Warhammer III campaigns on its own, records every decision it
makes in a form that can be learned from, and trains ranking models on the result.

The game has no API. Everything here is built on one narrow channel: a Lua mod compiled into
the game's own pack, talking to Python over a file command bus. That constraint shapes the
whole design — the interesting problems are not "what move is best" but "what can be read
from the game reliably, what can be executed and verified, and how do you know the record
you kept is true".

## What it does

A run plays N campaigns of up to T turns each. For every turn the agent:

1. reads the campaign state through the bus,
2. generates the legal actions available right now — recruit, move, attack, build, research,
   diplomacy, rites, stances, hero actions, and a dozen more,
3. ranks them with a model and picks one,
4. executes it through the UI and **verifies it landed**,
5. handles whatever the game interrupts with — battles, dilemmas, occupation choices,
   diplomatic offers, event popups.

Everything above is recorded: the state, the full option set, the score each option got, the
one taken, and whether the game confirmed it. That record is the product. The models are
downstream of it.


## Structure

Six components, one direction of data flow. `ARCHITECTURE.md` has the detail.

| | |
|---|---|
| **bus** | the Lua mod and the file command bus. The only thing that talks to the game. |
| **manager** | the recorder. Owns every bus read and every database write. |
| **launcher** | the driver. Game lifecycle, the executor registry, navigation, interrupts. |
| **advisor** | the backend. Plays the campaign, ranks actions, trains the models. |
| **advisor_api** | a typed JSON API over the corpus, and the server for the dashboard. |
| **ui** | the dashboard. React + Vite, types generated from the API's OpenAPI document. |

Supporting: `decisions` (schema and collection), `campaigns` (campaign-boundary splitting),
`analytics` (materialised aggregates), and the `input` / `shots` / `logs` / `ui-capture`
recorder streams.

## The models

Two learned rankers compete inside one strategy portfolio, alongside a hand-written ruleset
and a random arm, so the corpus keeps a coverage tail while the models take over:

- **greedy_catboost** — CatBoost on an E1/E2 advantage formulation, global and local.
- **marwil_gnn** — MARWIL/AWR over a graph encoder that sees the campaign as a graph of
  factions, regions, armies and the relations between them.

A separate interrupt model answers blocking screens (pre-battle, occupation, dilemmas,
diplomacy) as its own ranking problem.

Campaigns are abandoned early when they stop growing, so the run spends its time on
trajectories that are still going somewhere rather than on 20 turns of nothing.

The trainable arms are hard dependencies: if a model in the mix is missing, fails to load,
fails to train, or fails to predict, the session refuses to start or crashes rather than
silently playing random in its place. Only `--cold` runs modelless, deliberately.
Retraining never happens unless asked for (`--retrain-every N`, optionally
`--retrain-first`); a launch plays on the models already on disk. The graph models train
on CUDA.

## Requirements

- Total War: WARHAMMER III, installed and run once so its settings file exists
- Python 3.11+ (3.13 here; `tomllib` is used to read the config)
- Node LTS, for the dashboard client
- An NVIDIA GPU with a CUDA build of torch — the graph models train on CUDA, and a CPU-only
  torch wheel leaves `marwil_gnn` unable to retrain (the session refuses rather than
  degrading silently)

## Setup

    git clone <repo> tw_stack && cd tw_stack

    py -3.13 -m venv .venv
    .venv/Scripts/python -m pip install -r requirements.txt
    cd ui && npm install && cd ..

    python doctor.py

`doctor.py` prints every machine-dependent path, where each one came from, and what to do
about any it could not find. If it says `all paths resolve`, there is nothing to configure.

It usually does. Two paths are machine-dependent and each is resolved as
**environment variable → `config.toml` → autodetection → default**:

| | env | found by |
|---|---|---|
| the game install | `TW_GAME_DIR` | scanning the usual Steam library locations |
| everything a run produces | `TWDATA` | a `twdata` folder beside the checkout |

The checkout always owns `.venv`. Generated data, including the live file bus, belongs
under `TWDATA` outside the checkout; the bus uses `<TWDATA>/bus`.

Only if one is missing: `cp config.example.toml config.toml` and set it. `config.toml` is
not in git, so each machine keeps its own.

The lua mod is compiled into the game's pack format and cannot read any of this at
runtime, so `bus/pack_multi.py` bakes the bus paths in when it builds the pack — and fails
rather than shipping a pack pointing at the machine that last built it.

## Running it

    python check.py                      # every gate; do this first on a new machine

    python runctl.py up                  # the configured run: run_config.RUN

`run_config.RUN` is the authoritative description of the run — campaigns, turns, the
strategy mix, ruleset, campaign-map mix, presave radius, retrain cadence. `runctl up`
starts four processes: the recorder, the API on `:8777`, the analytics roller, and the
play session. It builds and installs the mod pack, boots the game, and drives it from
there — nothing else to click. Any RUN value can be overridden per launch, e.g.:

    python runctl.py up 500 20 --campaign "Realm of Chaos=0.5,Immortal Empires=0.5" \
        --strategies marwil_gnn=0.4,greedy_catboost=0.4,random=0.1,ruleset=0.1 \
        --ruleset probe_gaps --factions all --presave-radius 150

Models are chosen only through `--strategies`; there is no model flag (the exploit scorer
that ranks every offer is fixed — `advisor/backends.py` is its registry). `--epsilon`,
`--retrain`, `--cfg`/`--nn-*` were removed and are rejected with pointers to their
replacements.

    open http://127.0.0.1:8777          # watch it
    python babysit.py --loop 300        # supervise: relaunch a dead session, same config
    python runctl.py down               # stop everything

The run is engineered for throughput measured in turns per hour, wall-clock honest. Each
campaign kills the game when its fate is sealed (defeat, growth gate, stall) and the next
one boots fresh — no state is ever inherited across campaigns, and no action starts on an
uncleared screen. A turn whose end-turn cannot advance within the settle budget ends the
campaign rather than waiting. Blocking screens the handlers do not recognise are treated
as panel-handling bugs: each gets a hardcoded per-screen rule (exact title and node
paths), never an early generalisation.

Every wait and retry in the stack logs its use and outcome — greppable `WAIT`, `TRY` and
`PHASE` lines in the service `.err` files, every line ISO-timestamped. "Where did the
seconds go" is always one grep.

`check.py` marks which gates need a live game or a populated corpus. The rest run on a
fresh clone.

## Where things live

Code is here. Everything a run produces lives under `TWDATA` — run directories and the
`CURRENT_RUN` pointer, fitted models, the offline game-data reference, service logs and
archives. Nothing in this repository is generated by a run, and nothing generated is
written into it.

`rules/` is the exception, and it is here on purpose: a rule set decides how the agent
plays, so it is an input, not an output, and a run is not reproducible on another machine
if it lives elsewhere. `<TWDATA>/rules/` is searched first if you want to try a variant
without editing the checkout.
