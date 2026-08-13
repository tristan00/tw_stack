"""Every machine-dependent path in this project, defined once."""

from __future__ import annotations

import os
import sys


def native(p):
    r"""The same path spelled with the OS separator: "a/b" -> r"a\b" on Windows."""
    return p.replace("/", os.sep)


def posix(p):
    r"""The same path spelled with forward slashes: r"a\b" -> "a/b"."""
    return p.replace("\\", "/")


def _env(name, default):
    """Environment override, ignoring an empty value."""
    return os.environ.get(name) or default


# --------------------------------------------------------------------------------------
# (a) CODE -- under the repo root, discovered from this file. Not overridable.
# --------------------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))
if not os.path.isdir(os.path.join(ROOT, "decisions")):
    # common.py has been copied out of a checkout. Same fallback the four hand-fixed
    # sites carried, kept so that case behaves as it did before.
    ROOT = r"D:\tw_stack"

ADVISOR = os.path.join(ROOT, "advisor")
ADVISOR_API = os.path.join(ROOT, "advisor_api")
BUS = os.path.join(ROOT, "bus")
DECISIONS = os.path.join(ROOT, "decisions")
LAUNCHER = os.path.join(ROOT, "launcher")
MANAGER = os.path.join(ROOT, "manager")
MAPGRAPH = os.path.join(ROOT, "mapgraph")
REFERENCE = os.path.join(ADVISOR, "reference")
UI_CAPTURE = os.path.join(ROOT, "ui-capture")

# --------------------------------------------------------------------------------------
# (b) DATA -- external, shared by every checkout.
# --------------------------------------------------------------------------------------

TWDATA = _env("TWDATA", r"D:\twdata")
_TD = posix(TWDATA)

# runs. RUNS_ROOT is forward-slash because eleven modules spell it that way and the
# string reaches session reports; babysit.py and advisor/efficiency.py want the native
# spelling and call native() on it.
RUNS_ROOT = _TD + "/runs/human"
RUN_DIR = RUNS_ROOT + "/run"
DECISIONS_DB = "decisions.sqlite"


def cli_path(argv, takes_value=()):
    """The first bare token in argv that is not an option's VALUE."""
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in takes_value:
            skip = True
            continue
        if not a.startswith("--"):
            return a
    return None


def run_dbs(runs_root=None):
    """The decision databases a trainer reads. There is exactly ONE run dir."""
    root = native(runs_root) if runs_root else native(RUNS_ROOT)
    p = os.path.join(root, "run", DECISIONS_DB)
    return [p] if os.path.exists(p) else []
SCREEN_DUMP_DIR = RUNS_ROOT + "/screens"
UNHANDLED_LOG = RUNS_ROOT + "/unhandled_screens.jsonl"
CLEAR_SCREEN_TRACE = os.path.join(native(RUN_DIR), "clear_screen_trace.jsonl")
DECOMP_LIVETEST_ROOT = _TD + "/runs/_decomp_livetest"
SPLIT_PREVIEW = _TD + "/_split_preview"
STREAM_ROOT = _TD + "/stream"

# logs
LOGS_ADVISOR = os.path.join(TWDATA, "logs", "advisor")
LOGS_SERVICES = os.path.join(TWDATA, "logs", "services")
LOGS_LAUNCHER = _TD + "/logs/launcher"
LOGS_DEV = os.path.join(TWDATA, "logs", "dev")
CURRENT_SESSION_LOG = os.path.join(LOGS_ADVISOR, "CURRENT_SESSION_LOG.txt")
TRACE_PRERUN = os.path.join(native(LOGS_LAUNCHER), "trace_prerun.jsonl")
V7_SHOTS_DIR = os.path.join(native(LOGS_LAUNCHER), "v7_shots")
BABYSIT_LOG = os.path.join(LOGS_SERVICES, "babysitter.log")
BABYSIT_STAMP = os.path.join(LOGS_SERVICES, "babysitter_last_relaunch.txt")
BABYSIT_OFF = os.path.join(TWDATA, "BABYSIT_OFF")

# models
MODELS = os.path.join(TWDATA, "models")
MODEL_GLOBAL = os.path.join(MODELS, "global")
MODEL_LOCAL = os.path.join(MODELS, "local")
MODEL_INTERRUPT = os.path.join(MODELS, "interrupt")
MODEL_MAPGRAPH = os.path.join(MODELS, "mapgraph")
# The graph model for BLOCKING SCREENS. Its own weights, not the action model's: same
# architecture and same ontology, trained on a different decision family -- which is
# exactly how interrupt_model.py relates to model.py on the CatBoost side.
MODEL_MAPGRAPH_INTERRUPT = os.path.join(MODELS, "mapgraph_interrupt")
# not a real directory: the sentinel a run points at to force cold start.
MODEL_COLD_START = os.path.join(MODELS, "__cold_start__")

# reference
REFERENCE_DIR = os.path.join(TWDATA, "reference")
REFERENCE_DB = os.path.join(REFERENCE_DIR, "reference.sqlite")
UNLOCK_DB = os.path.join(REFERENCE_DIR, "agent_action_unlocks.sqlite")
# CA's own dump of every CCO context and property. decisions/cco_audit.py checks the
# collector's routes against it; a property read off the wrong context is silent.
CCO_TSV = os.path.join(REFERENCE_DIR, "ui3_extraction", "CCO.tsv")
WIKI_ROOT = _TD + "/wiki"

# scratch, metrics, archive
RULES_DIR = os.path.join(TWDATA, "rules")
METRICS_DIR = os.path.join(TWDATA, "metrics")
OPTUNA_DIR = os.path.join(METRICS_DIR, "optuna")
TMP_CATBOOST = os.path.join(TWDATA, "tmp", "catboost")
TMP_CATBOOST_TUNE = os.path.join(TWDATA, "tmp", "catboost_tune")
ARCHIVE_DIR = os.path.join(TWDATA, "archive")
ARCHIVE_SCRIPT_LOGS = os.path.join(ARCHIVE_DIR, "script_logs")
ARCHIVE_BUS = os.path.join(ARCHIVE_DIR, "bus")

# --------------------------------------------------------------------------------------
# (c) THIRD PARTY -- the runner venv that owns the bus files, and the game install.
# --------------------------------------------------------------------------------------

RUNNER = _env("TW_RUNNER", r"D:\totalwar_runner")
_RN = posix(RUNNER)

VENV_PY = os.path.join(RUNNER, ".venv", "Scripts", "python.exe")


def require_venv(what=None):
    """Refuse to run under any interpreter but the project venv."""
    if os.environ.get("TW_ALLOW_ANY_PYTHON") == "1":
        return
    want = os.path.normcase(os.path.abspath(VENV_PY))
    have = os.path.normcase(os.path.abspath(sys.executable))
    if want == have:
        return
    argv = " ".join(sys.argv) if sys.argv else (what or "")
    raise SystemExit(
        "wrong interpreter.\n"
        "  running : %s\n"
        "  required: %s\n"
        "This project's dependencies (torch, catboost, numpy, lupa) live only in the venv,\n"
        "so running elsewhere silently skips checks instead of failing them. Re-run as:\n"
        "  %s %s\n"
        "(set TW_ALLOW_ANY_PYTHON=1 to override)" % (sys.executable, VENV_PY, VENV_PY, argv))
RUNNER_DATA = _RN + "/data"
# The lua mod inside the game writes these two; bus/mod/twcontrol.lua carries its own
# copies of the literals and cannot import python -- see the note there.
BUS_CMD_PATH = RUNNER_DATA + "/commands.txt"
BUS_OUT_PATH = RUNNER_DATA + "/twcontrol.jsonl"
BUS_SEND_LOG = RUNNER_DATA + "/bus_send.jsonl"
BUS_STATS_DB = RUNNER_DATA + "/bus_stats.sqlite"

GAME_DIR = _env("TW_GAME_DIR", r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III")
GAME_DATA_DIR = posix(GAME_DIR) + "/data"
