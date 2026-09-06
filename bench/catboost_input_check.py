import itertools
import json
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch

import numpy as np
from catboost import CatBoostRegressor, Pool

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "3fa3235e7e9d1abd711724e90c032fbcd77b517d"
sys.path.insert(0, str(ROOT))

from advisor import model


def baseline(path):
    module = types.ModuleType("baseline_" + Path(path).stem)
    module.__file__ = str(ROOT / path)
    source = subprocess.check_output(["git", "show", BASELINE + ":" + path], cwd=ROOT, text=True)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def limited(method, n):
    def rows(self, *a, **kw):
        iterator = method(self, *a, **kw)
        try:
            yield from itertools.islice(iterator, n)
        finally:
            iterator.close()
    return rows


def main():
    old = baseline("advisor/model.py")
    old.F = baseline("advisor/features.py")
    old.DecisionStore = baseline("decisions/store.py").DecisionStore
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    with patch.object(old.DecisionStore, "taken_rows", limited(old.DecisionStore.taken_rows, n)):
        expected = old.gather(window=4000)
    with patch.object(model.DecisionStore, "taken_rows", limited(model.DecisionStore.taken_rows, n)):
        actual = model.gather(window=4000)
        packed = model.gather(window=4000, as_pool=True)
    for key in expected:
        assert expected[key] == actual[key], key
        if key != "full":
            assert expected[key] == packed[key], key
    num, cat = old.F.split_columns(expected["full"])
    assert (num, cat) == (packed["num"], packed["cat"])
    pool = Pool(old.F.matrix(expected["full"], num, cat), expected["y"],
                cat_features=list(range(len(num), len(num) + len(cat))))
    fit = CatBoostRegressor(iterations=8, depth=4, thread_count=2, verbose=False,
                            allow_writing_files=False)
    fit.fit(pool)
    np.testing.assert_array_equal(fit.predict(pool), fit.predict(packed["pool"]))
    print(json.dumps({"compared_rows": len(expected["y"]), "features": len(num) + len(cat),
                      "rows_labels_groups_confirmed": "identical", "predictions": "identical"}),
          flush=True)


if __name__ == "__main__":
    main()
