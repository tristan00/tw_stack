from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from catboost import CatBoostRegressor, Pool


from advisor import model
from advisor import optimize_catboost
from advisor.training_rows import TrainingRows


class TrainingInputTest(unittest.TestCase):

    def test_sparse_schema_and_late_categorical_values(self):
        rows = [{"opt_cost": i / 7, "opt_type": "move" if i % 2 else "攻撃",
                 "camp_turn": i % 17, "lord_acted": None,
                 "opt_is_active": i % 3} for i in range(1300)]
        rows[600]["opt_is_active"] = True
        rows[1100]["camp_turn"] = "late"
        rows[1200]["opt_cost"] = None
        rows[1220]["prov_income"] = 19.4
        rows[1290].pop("opt_type")
        y = [i % 11 for i in range(len(rows))]
        num, cat = model.F.split_columns(rows)
        expected = Pool(model.F.matrix(rows, num, cat), y,
                        cat_features=list(range(len(num), len(num) + len(cat))))
        with TrainingRows(model.F) as packed:
            for row in rows:
                packed.append(row)
                self.assertLess(len(packed.pending), 512)
            actual, got_num, got_cat = packed.pool(y)
        self.assertTrue(packed.file.closed)
        self.assertEqual((got_num, got_cat), (num, cat))
        self.assertEqual(actual.get_cat_feature_indices(), expected.get_cat_feature_indices())
        fitted = CatBoostRegressor(iterations=12, depth=3, verbose=False,
                                   allow_writing_files=False, thread_count=2)
        fitted.fit(expected)
        np.testing.assert_array_equal(fitted.predict(actual), fitted.predict(expected))
        fitted_actual = CatBoostRegressor(iterations=12, depth=3, verbose=False,
                                          allow_writing_files=False, thread_count=2)
        fitted_actual.fit(actual)
        np.testing.assert_array_equal(fitted_actual.predict(actual), fitted.predict(expected))

    def test_empty_and_failed_load_close_spool(self):
        with TrainingRows(model.F) as rows:
            self.assertEqual(rows.pool([]), (None, [], []))
        self.assertTrue(rows.file.closed)
        with self.assertRaisesRegex(RuntimeError, "load failed"):
            with TrainingRows(model.F) as rows:
                raise RuntimeError("load failed")
        self.assertTrue(rows.file.closed)

    def test_empty_entity_keeps_requested_base_features(self):
        record = {"campaign": {}, "world": {}, "entities": []}
        entity = {"context_kind": "campaign", "context_id": "faction", "offers": []}
        with patch.object(model.F, "state_row", return_value={"camp_turn": 2}) as state:
            self.assertEqual(model.F.offer_rows(record, entity), [])
            state.assert_not_called()
            sink = {}
            self.assertEqual(model.F.offer_rows(record, entity, base_sink=sink), [])
            self.assertEqual(sink, {"faction": {"camp_turn": 2}})

    def test_train_uses_pool_and_saves_compatible_model(self):
        rows = [{"opt_cost": float(i), "opt_type": str(i % 3)} for i in range(60)]
        y = [float(i % 9) for i in range(60)]
        groups = [str(i // 10) for i in range(60)]
        with TrainingRows(model.F) as packed:
            for row in rows:
                packed.append(row)
            pool, num, cat = packed.pool(y)
        data = dict(pool=pool, num=num, cat=cat, y=y, groups=groups,
                    n_decisions=60, skipped_unlabelled=0, runs=1, campaigns=6)
        fit = model.fit_es

        def short_fit(*a, **kw):
            return fit(*a, **kw, iterations=5, base={"depth": 3, "thread_count": 2,
                                                    "learning_rate": 0.1})

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(model, "gather", return_value=data) as gather, \
                    patch.object(model, "MODEL_DIR", str(Path(directory) / "model")), \
                    patch.object(model, "fit_es", side_effect=short_fit):
                result = model.train(window=4000)
                gather.assert_called_once_with(model.RUNS_ROOT, window=4000, as_pool=True)
                self.assertTrue(result["trained"])
                ranker = model.Ranker(str(Path(directory) / "model"))
                self.assertTrue(ranker.ready)
                with patch.object(model.F, "decision_rows", return_value=[
                        ({"context_kind": "campaign", "context_id": "faction"},
                         {"action_type": "move", "key": "xy:0,0"}, rows[0])]):
                    self.assertEqual(len(ranker.score({})), 1)

    def test_legacy_matrix_and_tuning_pools(self):
        matrix = [[float(i), str(i % 3)] for i in range(60)]
        y = [float(i % 7) for i in range(60)]
        params = {"depth": 3, "thread_count": 2, "learning_rate": 0.1}
        report = {}
        fitted = model.fit_es(matrix, y, [1], ["one"] * 60, "few", report,
                              iterations=4, base=params)
        self.assertFalse(report["few"]["early_stopping"])
        self.assertEqual(len(fitted.predict(matrix)), 60)
        groups = [str(i // 10) for i in range(60)]
        val, trn = model.grouped_split(60, groups)
        pool = Pool(matrix, y, cat_features=[1])
        pools = pool.slice(trn), pool.slice(val)
        with patch.object(optimize_catboost, "CB_ITERATIONS", 4):
            first = optimize_catboost._fit_val(pools, y, [1], val, trn, params, 60)
            second = optimize_catboost._fit_val(pools, y, [1], val, trn, params, 60)
        self.assertEqual(first[0], second[0])
        self.assertFalse(first[1]["hit_cap"])


if __name__ == "__main__":
    unittest.main()
