from pathlib import Path

import numpy as np
import pytest
from catboost import CatBoostRegressor, Pool

from advisor import model
from advisor import optimize_catboost
from advisor.training_rows import TrainingRows

pytestmark = pytest.mark.skip("not part of the suite")


def test_sparse_schema_and_late_categorical_values():
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
            assert len(packed.pending) < 512
        actual, got_num, got_cat = packed.pool(y)
    assert packed.file.closed
    assert (got_num, got_cat) == (num, cat)
    assert actual.get_cat_feature_indices() == expected.get_cat_feature_indices()
    fitted = CatBoostRegressor(iterations=12, depth=3, verbose=False,
                               allow_writing_files=False, thread_count=2)
    fitted.fit(expected)
    np.testing.assert_array_equal(fitted.predict(actual), fitted.predict(expected))
    fitted_actual = CatBoostRegressor(iterations=12, depth=3, verbose=False,
                                      allow_writing_files=False, thread_count=2)
    fitted_actual.fit(actual)
    np.testing.assert_array_equal(fitted_actual.predict(actual), fitted.predict(expected))


def test_empty_and_failed_load_close_spool():
    with TrainingRows(model.F) as rows:
        assert rows.pool([]) == (None, [], [])
    assert rows.file.closed
    with pytest.raises(RuntimeError, match="load failed"):
        with TrainingRows(model.F) as rows:
            raise RuntimeError("load failed")
    assert rows.file.closed


def test_empty_entity_keeps_requested_base_features(monkeypatch):
    record = {"campaign": {}, "world": {}, "entities": []}
    entity = {"context_kind": "campaign", "context_id": "faction", "offers": []}
    calls = []
    monkeypatch.setattr(model.F, "state_row",
                        lambda *a, **kw: calls.append(1) or {"camp_turn": 2})
    assert model.F.offer_rows(record, entity) == []
    assert calls == []
    sink = {}
    assert model.F.offer_rows(record, entity, base_sink=sink) == []
    assert sink == {"faction": {"camp_turn": 2}}


def test_train_uses_pool_and_saves_compatible_model(monkeypatch, tmp_path):
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

    gathered = []

    def gather(*a, **kw):
        gathered.append((a, kw))
        return data

    directory = str(tmp_path / "model")
    monkeypatch.setattr(model, "gather", gather)
    monkeypatch.setattr(model, "MODEL_DIR", directory)
    monkeypatch.setattr(model, "fit_es", short_fit)
    result = model.train(window=4000)
    assert gathered == [((model.RUNS_ROOT,), {"window": 4000, "as_pool": True})]
    assert result["trained"]
    ranker = model.Ranker(directory)
    assert ranker.ready
    monkeypatch.setattr(model.F, "decision_rows", lambda *a, **kw: [
        ({"context_kind": "campaign", "context_id": "faction"},
         {"action_type": "move", "key": "xy:0,0"}, rows[0])])
    assert len(ranker.score({})) == 1


def test_legacy_matrix_and_tuning_pools(monkeypatch):
    matrix = [[float(i), str(i % 3)] for i in range(60)]
    y = [float(i % 7) for i in range(60)]
    params = {"depth": 3, "thread_count": 2, "learning_rate": 0.1}
    report = {}
    fitted = model.fit_es(matrix, y, [1], ["one"] * 60, "few", report,
                          iterations=4, base=params)
    assert not report["few"]["early_stopping"]
    assert len(fitted.predict(matrix)) == 60
    groups = [str(i // 10) for i in range(60)]
    val, trn = model.grouped_split(60, groups)
    pool = Pool(matrix, y, cat_features=[1])
    pools = pool.slice(trn), pool.slice(val)
    monkeypatch.setattr(optimize_catboost, "CB_ITERATIONS", 4)
    first = optimize_catboost._fit_val(pools, y, [1], val, trn, params, 60)
    second = optimize_catboost._fit_val(pools, y, [1], val, trn, params, 60)
    assert first[0] == second[0]
    assert not first[1]["hit_cap"]
