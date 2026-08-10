from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

NO_MODEL_DIR = common.MODEL_COLD_START

BACKENDS = {
    "catboost": {"module": "model", "label": "CatBoost E1/E2 impact"},
    "nn": {"module": "nn_model", "label": "MLP on quantile-normalised inputs"},
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
    out = {}
    for i, tok in enumerate(argv):
        if not tok.startswith("--nn-") or i + 1 >= len(argv):
            continue
        key = tok[len("--nn-"):].replace("-", "_")
        raw = argv[i + 1]
        try:
            out[key] = int(raw)
        except ValueError:
            try:
                out[key] = float(raw)
            except ValueError:
                out[key] = raw
    return out
