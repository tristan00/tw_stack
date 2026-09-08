import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from advisor.temporal_graph import GraphConfig, GraphLimits, NodeKind, build_temporal_graph
from advisor.temporal_graph.examples import campaign
from advisor.temporal_graph.tuning.config import SPACE, suggest, validate_budget
from advisor.reward_data import HEAD_SQL, partition, score
from advisor.temporal_graph.tuning.source import query_at
from advisor.temporal_graph.tuning.optimize import main
from advisor.temporal_graph.tuning.packing import GraphCache, batches, collate, graph_arrays
from advisor.temporal_graph.tuning.source import CampaignEncoder, features
from advisor.temporal_graph.tuning.train import prepare_graphs
from advisor.reward_worker import run_trial


class Trial:

    def __init__(self, **choices):
        self.choices, self.names = choices, set()

    def suggest_categorical(self, name, choices):
        self.names.add(name)
        return self.choices.get(name, choices[0])

    def suggest_float(self, name, low, high, **kwargs):
        self.names.add(name)
        return self.choices.get(name, low)

    suggest_int = suggest_float


def reward_rows():
    return [dict(campaign="campaign_%03d" % i, decision=i * 10 + step, step=step, y=float(i + step), action=action())
            for i in range(30) for step in range(2)]


def record(campaign="camp", decision=1, turn=1, treasury=10):
    return dict(campaign_id=campaign, decision_id=decision, turn=turn,
        campaign=dict(faction="player", treasury=treasury, settlements=2, lord_level=1, allies=0, vassals=0),
        world=dict(settlements={"region_a": dict(region="region_a", level=2, x=0, y=3)}),
        entities=[dict(snapshot_id=decision * 64, context_kind="lord", context_id=7,
                       state=dict(cqi=7, rank=4, region="region_a", x=1, y=2), offers=[])])


def action(**extra):
    return dict(entity_seq=0, action_type="move", key="region_a", slot_index=None, **extra)


@pytest.mark.parametrize("layout", SPACE["layout"])
@pytest.mark.parametrize("view", SPACE["node_view"])
def test_conditional_search_space_builds_real_graphs(layout, view):
    trial = Trial(node_view=view, layout=layout, history=4, degree=2, candidates=64)
    config = suggest(trial)
    assert ("resolution" in trial.names) == (view in ("bundle", "trace", "mixed"))
    assert ("arity" in trial.names) == (layout in ("hub", "incidence"))
    assert ("degree" in trial.names) == (layout not in ("forest", "empty", "dense"))
    history, _ = campaign(3, 3)
    graph = build_temporal_graph(history, config=GraphConfig(**config["graph"]), limits=GraphLimits(**config["limits"]))
    assert np.all(graph.nodes.tick[graph.edge_index[0]] <= graph.nodes.tick[graph.edge_index[1]])


@pytest.mark.parametrize("budget", [0, -1, 601, float("inf"), float("nan")])
def test_invalid_trial_budgets_fail(budget):
    with pytest.raises(ValueError):
        validate_budget(budget)


def test_sequence_split_is_shared_and_campaign_disjoint():
    from advisor.base_model import stable_split

    rows = reward_rows()
    split = partition(rows)
    expected_val, expected_train = stable_split(len(rows), [r["campaign"] for r in rows])
    assert split["validation_indices"] == expected_val
    assert split["train_indices"] == expected_train
    assert {rows[i]["campaign"] for i in expected_val}.isdisjoint(rows[i]["campaign"] for i in expected_train)
    changed = deepcopy(rows)
    for index in expected_val:
        changed[index]["y"] += 1_000_000
    other = partition(changed)
    assert other["reward_mean"] == split["reward_mean"]
    assert other["reward_sd"] == split["reward_sd"]
    assert other["train_indices"] == expected_train


def test_small_dataset_has_no_random_split_substitute():
    with pytest.raises(ValueError, match="three"):
        partition(reward_rows()[:4])


def test_reported_mse_is_in_raw_reward_units():
    fit = score([10, 14], [8, 18])
    assert fit == dict(val_mse=10.0, raw_val_mse=10.0, val_r2=-1.5)


def test_live_reader_uses_whole_campaign_window_and_existing_reward():
    from advisor.reward_data import LiveCorpus
    from advisor.base_model import decision_deltas, target

    campaigns = ["campaign_%03d" % i for i in range(30)]
    heads = [(i, campaign, 1, 0, "move", "region_a", None, None, 2, 1, 0, 0)
             for i, campaign in enumerate(campaigns)]
    series = {campaign: {2: dict(settlements=4, lord_level=2, allies=1, vassals=2)} for campaign in campaigns}

    def execute(sql, args):
        assert sql == HEAD_SQL and args == (campaigns,)
        return SimpleNamespace(fetchall=lambda: heads)

    def window_keys(window):
        assert window == 30
        return campaigns

    store = SimpleNamespace(window_keys=window_keys, con=SimpleNamespace(execute=execute), target_series=lambda _: series)
    corpus = LiveCorpus(store, 30, float("inf"))
    expected = target(decision_deltas(dict(settlements=2, lord_level=1, allies=0, vassals=0), series[campaigns[0]], 1))
    assert len(corpus.rows) == 30
    assert all(r["y"] == expected and r["step"] == 0 for r in corpus.rows)


def test_sequence_offer_adapter_preserves_selected_duplicate():
    from advisor.mapgraph.live import attach_actions

    row = dict(decision=1, action=action(offer_seq=11))
    corpus = SimpleNamespace(offers=lambda _: [(1, 10, 0, "noop", "x", None), (1, 11, 0, "noop", "x", None)])
    row["action"].update(action_type="noop", key="x")
    rec, index = attach_actions(corpus, record(), row, {})
    assert index == 1 and len(rec["entities"][0]["offers"]) == 2


def test_raw_encoding_ignores_execution_and_policy_fields_but_keeps_game_rank():
    rec = record()
    contaminated = deepcopy(rec)
    contaminated["validated"] = False
    contaminated["campaign"].update(counted=99, refusal="failure", selector="arm", read_failures={"x": 9})
    contaminated["entities"][0]["offers"] = [dict(score=999, rank=7, exploit=True)]
    first, second = CampaignEncoder("camp"), CampaignEncoder("camp")
    first.append(rec, action())
    second.append(contaminated, action(validated=True, counted=True, executed=True, score=999, rank=7))
    for name in first.arrays:
        np.testing.assert_array_equal(first.arrays[name], second.arrays[name])
    a, _ = features("lord", dict(rank=4))
    b, _ = features("lord", dict(rank=5))
    assert not np.array_equal(a, b)


def test_zero_and_missing_game_values_have_different_presence():
    zero, seen = features("campaign", dict(treasury=0))
    absent, unseen = features("campaign", dict(treasury=None))
    np.testing.assert_array_equal(zero, absent)
    assert np.count_nonzero(seen) == 1 and not unseen.any()


def test_raw_reader_sql_does_not_read_or_filter_action_outcomes():
    sql = HEAD_SQL.lower()
    for key in ("refusal", "counted", "executed", "confirmed", "score", "exploit", "policy", "rank"):
        assert key not in sql
    assert "left join corpus.offer" in sql
    assert sql.split(" where ")[1].split(" order by ")[0] == "c.campaign_key = any(%s)"


def test_encoder_preserves_causal_actions_and_local_steps():
    encoder = CampaignEncoder("camp")
    encoder.append(record(decision=10), action())
    encoder.append(record(decision=900, treasury=12), action())
    encoder.append(record(decision=910, turn=2, treasury=99999), action())
    history, queries = encoder.finish()
    graph = build_temporal_graph(history, query_at(queries, 1), GraphConfig(node_view="observation"), cutoff=1)
    assert graph.query_ids.tolist() == [900]
    assert np.count_nonzero(graph.nodes.kind == NodeKind.ACTION) == 1
    assert graph.nodes.step.max() == 1
    prior = CampaignEncoder("camp")
    prior.append(record(decision=10), action())
    prior.append(record(decision=900, treasury=12), action())
    prefix, pq = prior.finish()
    earlier = build_temporal_graph(prefix, query_at(pq, 1), GraphConfig(node_view="observation"), cutoff=1)
    np.testing.assert_array_equal(graph.nodes.values, earlier.nodes.values)
    np.testing.assert_array_equal(graph.edge_index, earlier.edge_index)


def test_disk_cache_batches_offset_edges_without_crossing_graphs(tmp_path):
    history, queries = campaign(3, 2)
    queries = type(queries).from_arrays(queries.values[:1], ids=queries.ids[:1])
    graph = build_temporal_graph(history, queries, GraphConfig(history=2))
    arrays = graph_arrays(graph, 2, "both")
    cache = GraphCache(tmp_path / "cache.bin", 1024 * 1024)
    try:
        cache.append(arrays)
        cache.append(arrays)
        cache.finish()
        combined = collate(cache, [0, 1])
        n, e = len(arrays["x"]), len(arrays["edge"])
        assert np.all(combined["edge_index"][:, :e] < n)
        assert np.all(combined["edge_index"][:, e:] >= n)
        np.testing.assert_array_equal(combined["query"], np.r_[arrays["query"], arrays["query"] + n])
        assert list(batches(cache, [0, 1], 8, max_nodes=n)) == [[0], [1]]
        assert list(batches(cache, [0, 1], 1)) == [[0], [1]]
    finally:
        cache.close()


def test_cache_limit_rejects_whole_graph_and_time_ablation_zeros_only_selected_clock(tmp_path):
    history, queries = campaign(3, 2)
    queries = type(queries).from_arrays(queries.values[:1], ids=queries.ids[:1])
    graph = build_temporal_graph(history, queries)
    arrays = graph_arrays(graph, 2, "action")
    assert not arrays["edge"][:, -1].any()
    assert not arrays["x"][:, -2].any()
    cache = GraphCache(tmp_path / "cache.bin", 1)
    try:
        with pytest.raises(MemoryError):
            cache.append(arrays)
        assert not cache.rows and cache.size == 0
    finally:
        cache.close()


class FakeCorpus:

    def __init__(self):
        self.rows = reward_rows()
        self.split = partition(self.rows)

    def campaigns(self):
        from itertools import groupby
        for campaign, rows in groupby(self.rows, key=lambda r: r["campaign"]):
            yield campaign, ((row, record(campaign, row["decision"])) for row in rows)

def test_graph_preparation_keeps_every_shared_live_reward_row(tmp_path):
    corpus = FakeCorpus()
    cfg = suggest(Trial(history=1, degree=2))
    cache = GraphCache(tmp_path / "cache.bin", 1024 * 1024)
    try:
        metrics = prepare_graphs(corpus, cfg, cache, float("inf"))
        assert metrics["graphs"] == len(corpus.rows) == len(cache.rows)
        assert all(len(cache[i]["query"]) == 1 for i in range(len(corpus.rows)))
    finally:
        cache.close()


def test_worker_watchdog_terminates_at_limit_and_cleans_own_cache(tmp_path):
    elapsed = [0.0]
    state = SimpleNamespace(alive=True, terminated=False, closed=False)
    (tmp_path / "graph_cache.bin").write_bytes(b"partial")

    class Process:
        pid, exitcode = 5, None

        def __init__(self, **kwargs):
            assert kwargs["args"][4] == 600

        def start(self):
            pass

        def is_alive(self):
            return state.alive

        def join(self, timeout):
            if state.alive:
                elapsed[0] += timeout

        def terminate(self):
            state.alive, state.terminated = False, True

        def close(self):
            state.closed = True

    with pytest.raises(TimeoutError):
        run_trial("temporal_graph", 2500, {}, tmp_path, 600, "cpu", 0, 1024,
                  context=SimpleNamespace(Process=Process), clock=lambda: elapsed[0])
    assert elapsed[0] == 600 and state.terminated and state.closed
    assert not (tmp_path / "graph_cache.bin").exists()


def test_space_command_does_not_launch_a_study(capsys, monkeypatch):
    from advisor.temporal_graph.tuning import optimize

    def forbidden(*args, **kwargs):
        pytest.fail("a read-only command tried to launch work")

    monkeypatch.setattr(optimize, "run", forbidden)
    assert main(["--space"]) == 0
    assert "time_bias" in capsys.readouterr().out


def test_tuning_requires_an_output_directory(monkeypatch):
    from advisor.temporal_graph.tuning import optimize

    def forbidden(*args, **kwargs):
        pytest.fail("missing arguments launched a study")

    monkeypatch.setattr(optimize, "run", forbidden)
    with pytest.raises(SystemExit) as error:
        main(["--window", "2500"])
    assert error.value.code == 2


def test_tuning_code_does_not_import_mapgraph_or_add_comments_or_docstrings():
    import io
    import tokenize

    root = Path(__file__).resolve().parents[1] / "advisor" / "temporal_graph" / "tuning"
    for path in root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert not [t for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.COMMENT]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                assert ast.get_docstring(node) is None
            if isinstance(node, ast.ImportFrom):
                assert "mapgraph" not in (node.module or "")
