from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

NO_MODEL_DIR = common.MODEL_COLD_START

# One entry. `nn_model` -- an MLP over the same flat CatBoost feature columns -- was
# deleted: nothing selected it, and it consumed exactly the engineered feature block the
# graph model exists to replace. This registry is now an indirection over a single value
# and should be collapsed into a direct `import model` at its three call sites
# (session.py, advisor_ui/ui.py, runctl.py) once the UI form that reads it is reworked.
BACKENDS = {
    "catboost": {"module": "model", "label": "CatBoost E1/E2 impact"},
}
DEFAULT = "catboost"


def names():
    return sorted(BACKENDS)


def resolve(name):
    key = str(name or DEFAULT).strip().lower()
    if key not in BACKENDS:
        raise SystemExit("unknown --model %r -- known backends: %s"
                         % (name, ", ".join(names())))
    return __import__(BACKENDS[key]["module"])


def label(name):
    return BACKENDS.get(str(name or DEFAULT).strip().lower(), {}).get("label", "?")


def parse_cfg(argv):
    """No backend takes hyperparameters from the command line any more.

    This parsed `--nn-KEY VALUE` for the deleted MLP backend. It stays as an empty dict
    so `backend_cfg` keeps its shape in the trial ledger, where historical rows carry
    real values and a KeyError would break the metrics tab.
    """
    return {}
