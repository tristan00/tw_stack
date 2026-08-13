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

## Why the record is the hard part

An action counts as taken only when it is **executed AND confirmed**. A click that the game
silently ignored is void, and is stored as void. This matters more than it sounds: a corpus
where refused actions look identical to successful ones teaches a model that they work.

The same principle runs through the rest:

- Unhandled screens end the session rather than being clicked past. A campaign recorded
  against a screen the agent could not read is worse than no campaign.
- Every count names the population it counted; every rate carries its denominator.
- "Not measurable" is a distinct state, never a zero.
- Growth has exactly one definition, shared by the training ledger and the dashboard, so the
  two can never disagree about whether a campaign grew.

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

## Requirements

- Total War: WARHAMMER III, installed and run once so its settings file exists
- Python 3.11+ (3.13 here; `tomllib` is used to read the config)
- Node LTS, for the dashboard client
- An NVIDIA GPU is used if present; the graph model trains on CPU otherwise

## Setup

    git clone <repo> tw_stack && cd tw_stack

    python -m venv ../totalwar_runner/.venv          # the venv also owns the file bus
    ../totalwar_runner/.venv/Scripts/python -m pip install -r requirements.txt
    cd ui && npm install && cd ..

    python doctor.py

`doctor.py` prints every machine-dependent path, where each one came from, and what to do
about any it could not find. If it says `all paths resolve`, there is nothing to configure.

It usually does. Three paths are machine-dependent and each is resolved as
**environment variable → `config.toml` → autodetection → default**:

| | env | found by |
|---|---|---|
| the game install | `TW_GAME_DIR` | scanning the usual Steam library locations |
| everything a run produces | `TWDATA` | a `twdata` folder beside the checkout |
| the venv and file bus | `TW_RUNNER` | the interpreter you are running, if it is a venv |

Only if one is missing: `cp config.example.toml config.toml` and set it. `config.toml` is
not in git, so each machine keeps its own.

The lua mod is compiled into the game's pack format and cannot read any of this at
runtime, so `bus/pack_multi.py` bakes the bus paths in when it builds the pack — and fails
rather than shipping a pack pointing at the machine that last built it.

## Running it

    python check.py                      # every gate; do this first on a new machine

    python runctl.py up 500 20 --campaign "Realm of Chaos" --model catboost \
        --strategies marwil_gnn=0.3,greedy_catboost=0.3,random=0.3,ruleset=0.1 \
        --ruleset probe_gaps --factions all --dev

That plays 500 campaigns of up to 20 turns and starts four processes: the recorder, the
API on `:8777`, the analytics roller, and the play session. It builds and installs the mod
pack, launches the game, and drives it from there — nothing else to click.

    open http://127.0.0.1:8777          # watch it
    python babysit.py --loop 300        # supervise: relaunch a dead session, same config
    python runctl.py down               # stop everything

`babysit.RUN` is the authoritative description of the run. A relaunch reproduces it
exactly; a supervisor that quietly brings the run back in a different shape is worse than
none, because the corpus keeps growing either way.

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
