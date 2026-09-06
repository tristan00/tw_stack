import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisor.mapgraph import optimize_greedy as O


def main():
    out = os.path.join(os.path.dirname(__file__), "gnn-tuner-check")
    model = dict(O.GT.CFG, hidden=32, entity_layers=1, action_rounds=1)
    model = {k: v for k, v in model.items() if k not in O.FIXED and k != "time_budget_s"}
    with patch.object(O, "OUT_DIR", out), \
            patch.object(O, "TRIALS_JSONL", os.path.join(out, "trials.jsonl")), \
            patch.object(O, "_storage", return_value=None), \
            patch.object(O, "_space", return_value=model), \
            patch.object(O, "_graph_space", return_value=O.GC.DEFAULT):
        with patch.object(O.GT, "prepare", side_effect=AssertionError("oversized trial reached prepare")):
            assert O.run(trials=1, budget_s=2, window=2000,
                         graph_probe=30, max_corpus_gib=0.001) == 2
        assert O.run(trials=2, budget_s=2, window=2000, limit=1000, graph_probe=30) == 0
    print("PASS: GPU-budget rejection and two accepted trials")


if __name__ == "__main__":
    main()
