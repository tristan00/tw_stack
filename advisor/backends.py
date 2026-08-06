from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

NO_MODEL_DIR = r"D:\twdata\models\__cold_start__"

BACKENDS = {
    "catboost": {"module": "model", "label": "CatBoost E1/E2 + isolation-forest explore"},
    "nn": {"module": "nn_model", "label": "MLP on quantile-normalised inputs, random explore"},
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
