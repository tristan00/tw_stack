r"""Every machine-dependent path in this project, defined once.

WHY THIS EXISTS -- the sys.path half of it is not decoration.

Dozens of modules used to open with `sys.path.insert(0, r"D:\tw_stack\<subdir>")`.
Hardcoding the checkout means a module imports from the MAIN checkout no matter which
checkout it is itself running from, and because the insert lands at position 0 it wins
over whatever the caller had already arranged. Two diagnosed bugs, both from exactly
that:

  * advisor/base_model.py inserted r"D:\tw_stack\decisions" at position 0, so a worktree
    silently imported the main checkout's store.py underneath its own edits, overriding
    what the caller had set up.
  * advisor/policy.py and advisor/session.py inserted r"D:\tw_stack" before importing
    mapgraph, so a worktree loaded the main checkout's model code while training wrote
    a model from its own. It surfaced as a state_dict key mismatch, and a key mismatch
    silently drops the gnn arm to random -- a wrong number, not a crash.

So ROOT is discovered from THIS FILE's own location and every code path derives from it.
A worktree is then self-consistent, and in the live checkout ROOT is D:\tw_stack, so
every derived value is byte for byte what used to be hardcoded.

BOOTSTRAP -- the chicken and egg. Most modules live in a subdirectory and are run as
scripts, so the repo root is not on sys.path when they start: they cannot `import common`
until they have found the root, which is the one thing common exists to tell them. The
circle is broken with two lines at the top of each module, the smallest possible version
of the block they replace:

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import common

Deliberately there is no `except ImportError: <hardcoded fallback>` around that. If
common.py is not next to the module's parent directory the module is not in a checkout,
and a loud ImportError beats silently importing another checkout -- which is the whole
failure mode this file exists to prevent.

THREE KINDS OF PATH, kept apart on purpose:

  (a) CODE, under ROOT. Self-locating, never hardcoded, never overridable: a checkout
      has to run its own code.
  (b) DATA, under D:\twdata -- runs, models, reference, metrics, archive, logs. Genuinely
      external and shared by every checkout. Override with the TWDATA env var; the
      default is today's value.
  (c) THIRD PARTY -- the runner venv and the bus files it owns, and the game install.
      Override with TW_RUNNER and TW_GAME_DIR; defaults are today's values.

SPELLING. This is a move, not a retune. Every constant is byte for byte the string it
replaced, including whether it uses / or \, because these strings land in logs, run
directory names and session reports. A few directories were spelled both ways by
different modules; the constant carries the majority spelling and the odd caller asks for
the other one explicitly via native() or posix(). Do not "tidy" those away without
checking what reads the resulting string.

This module imports nothing from the project, and must stay that way: everything imports
it, so any project import here is a cycle.
"""

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
ADVISOR_UI = os.path.join(ROOT, "advisor_ui")
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
    """The first bare token in argv that is not an option's VALUE.

    `next(a for a in argv if not a.startswith("--"))` is the obvious way to find a
    positional path and it is wrong: an option's value is also a bare token, so
    `--n 40` yields "40" and the tool then tries to open a run directory called 40.
    Three separate tools had their own copy of that line -- coverage, mapgraph.wl and
    mapgraph.test_build -- and all three failed the same way, reporting a missing corpus
    rather than a command line they had mis-read.
    """
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
    """The decision databases a trainer reads. There is exactly ONE run dir.

    Every trainer used to glob `RUNS_ROOT/*/decisions.sqlite`. That silently widened the
    corpus to whatever happened to be sitting in the runs root -- an archived 15GB v1
    corpus sat beside the live one and was opened by model.gather, mapgraph.train.walk,
    batch_metrics and interrupt_model alike. An incompatible store raises and is skipped,
    so that failure reads as "no data" rather than as a misconfiguration -- and a
    COMPATIBLE stale one would have been trained on without a word.

    Returns a list because the callers iterate, not because there can be more than one.
    """
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
    """Refuse to run under any interpreter but the project venv.

    This is the ONE python that has torch, catboost, numpy and lupa; the README has said
    so since the beginning and nothing enforced it. A check run under a bare system python
    does not fail -- it degrades, skips the half of itself that needed the missing import,
    and prints a summary that reads green. That happened: `mapgraph.invariants` was run
    under C:\\Python314, reported that the network invariants "need torch", and the
    conclusion "torch is not on this machine" was written into a handoff. Both were false.
    A wrong interpreter is now a hard stop with the right command in the message.

    TW_ALLOW_ANY_PYTHON=1 escapes it, for the case where someone means it.
    """
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
