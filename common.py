
from __future__ import annotations

import os
import sys


def native(p):
    return p.replace("/", os.sep)


def posix(p):
    return p.replace("\\", "/")


def _load_config():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")
    if not os.path.exists(path):
        return {}
    try:
        import tomllib
    except ImportError:
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh).get("paths", {})
    except Exception as e:
        raise SystemExit("config.toml is present but unreadable: %s\n  %s" % (path, e))


CONFIG = _load_config()
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")


def _setting(env_name, key, autodetect=None, default=None):
    v = os.environ.get(env_name)
    if v:
        return os.path.expandvars(os.path.expanduser(v))
    v = CONFIG.get(key)
    if v:
        return os.path.expandvars(os.path.expanduser(str(v)))
    if autodetect:
        found = autodetect()
        if found:
            return found
    return default


def _find_game():
    name = os.path.join("steamapps", "common", "Total War WARHAMMER III")
    roots = []
    for drive in "CDEFGH":
        roots += ["%s:\\SteamLibrary" % drive, "%s:\\Program Files (x86)\\Steam" % drive,
                  "%s:\\Steam" % drive, "%s:\\Games\\Steam" % drive]
    roots += [os.path.expanduser("~/.steam/steam"),
              os.path.expanduser("~/.local/share/Steam"),
              os.path.expanduser("~/Library/Application Support/Steam")]
    for r in roots:
        p = os.path.join(r, name)
        if os.path.isdir(p):
            return p
    return None


ROOT = os.path.dirname(os.path.abspath(__file__))
if not os.path.isdir(os.path.join(ROOT, "decisions")):
    raise SystemExit("common.py is not inside a tw_stack checkout: %s" % ROOT)

ADVISOR = os.path.join(ROOT, "advisor")
ADVISOR_API = os.path.join(ROOT, "advisor_api")
BUS = os.path.join(ROOT, "bus")
DECISIONS = os.path.join(ROOT, "decisions")
LAUNCHER = os.path.join(ROOT, "launcher")
MANAGER = os.path.join(ROOT, "manager")
MAPGRAPH = os.path.join(ROOT, "mapgraph")
REFERENCE = os.path.join(ADVISOR, "reference")
UI_CAPTURE = os.path.join(ROOT, "ui-capture")


TWDATA = _setting("TWDATA", "twdata",
                  default=os.path.join(os.path.dirname(ROOT), "twdata"))
_TD = posix(TWDATA)

RUNS_ROOT = _TD + "/runs/human"
RUN_DIR = RUNS_ROOT + "/run"
DECISIONS_DB = "decisions.sqlite"


def cli_path(argv, takes_value=()):
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
    root = native(runs_root) if runs_root else native(RUNS_ROOT)
    p = os.path.join(root, "run", DECISIONS_DB)
    return [p] if os.path.exists(p) else []
SCREEN_DUMP_DIR = RUNS_ROOT + "/screens"
UNHANDLED_LOG = RUNS_ROOT + "/unhandled_screens.jsonl"
CLEAR_SCREEN_TRACE = os.path.join(native(RUN_DIR), "clear_screen_trace.jsonl")
DECOMP_LIVETEST_ROOT = _TD + "/runs/_decomp_livetest"
SPLIT_PREVIEW = _TD + "/_split_preview"
STREAM_ROOT = _TD + "/stream"

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

MODELS = os.path.join(TWDATA, "models")
MODEL_GLOBAL = os.path.join(MODELS, "global")
MODEL_LOCAL = os.path.join(MODELS, "local")
MODEL_INTERRUPT = os.path.join(MODELS, "interrupt")
MODEL_MAPGRAPH = os.path.join(MODELS, "mapgraph")
MODEL_MAPGRAPH_INTERRUPT = os.path.join(MODELS, "mapgraph_interrupt")
MODEL_COLD_START = os.path.join(MODELS, "__cold_start__")

REFERENCE_DIR = os.path.join(TWDATA, "reference")
REFERENCE_DB = os.path.join(REFERENCE_DIR, "reference.sqlite")
UNLOCK_DB = os.path.join(REFERENCE_DIR, "agent_action_unlocks.sqlite")
CCO_TSV = os.path.join(REFERENCE_DIR, "ui3_extraction", "CCO.tsv")
WIKI_ROOT = _TD + "/wiki"

RULES_DIR_REPO = os.path.join(ROOT, "rules")
RULES_DIR = os.path.join(TWDATA, "rules")
METRICS_DIR = os.path.join(TWDATA, "metrics")
OPTUNA_DIR = os.path.join(METRICS_DIR, "optuna")
TMP_CATBOOST = os.path.join(TWDATA, "tmp", "catboost")
TMP_CATBOOST_TUNE = os.path.join(TWDATA, "tmp", "catboost_tune")
ARCHIVE_DIR = os.path.join(TWDATA, "archive")
ARCHIVE_SCRIPT_LOGS = os.path.join(ARCHIVE_DIR, "script_logs")
ARCHIVE_BUS = os.path.join(ARCHIVE_DIR, "bus")


RUNNER = ROOT

VENV_PY = os.path.join(RUNNER, ".venv",
                       "Scripts" if os.name == "nt" else "bin",
                       "python.exe" if os.name == "nt" else "python")


def require_venv(what=None):
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
RUNNER_DATA = posix(os.path.join(TWDATA, "bus"))
BUS_CMD_PATH = RUNNER_DATA + "/commands.txt"
BUS_OUT_PATH = RUNNER_DATA + "/twcontrol.jsonl"
BUS_SEND_LOG = RUNNER_DATA + "/bus_send.jsonl"
BUS_STATS_DB = RUNNER_DATA + "/bus_stats.sqlite"

GAME_DIR = _setting("TW_GAME_DIR", "game_dir", _find_game, default="")
GAME_DATA_DIR = posix(GAME_DIR) + "/data"
