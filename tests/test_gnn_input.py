from types import SimpleNamespace

import numpy as np
import pytest
import torch
from torch_geometric.data import Batch

from advisor.mapgraph import net as N
from advisor.mapgraph import schema as S
from advisor.mapgraph import train as T

pytestmark = pytest.mark.skip("not part of the suite")


def make_datas():
    generator = torch.Generator().manual_seed(91)
    datas = []
    for size in (13, 80, 173, 1024):
        types = torch.randint(len(S.NODE_TYPES), (size,), generator=generator)
        x = torch.randn(size, S.MAX_FIELDS, generator=generator)
        widths = torch.tensor([len(S.TYPE_FIELDS[name]) for name in S.NODE_TYPES])
        x[torch.arange(S.MAX_FIELDS)[None, :] >= widths[types, None]] = 0
        data = N.DecisionGraph(x=x, node_type=types, g_ctx=torch.zeros(1, S.G_CTX_DIM),
                               n_actions=torch.tensor([2]), action_index=torch.tensor([0, size - 1]),
                               y=torch.randn(1, generator=generator), is_taken=torch.tensor([1., 0.]))
        for name in ("edge", "a2e", "e2a"):
            data[name + "_index"] = torch.randint(size, (2, size * 2), generator=generator)
            data[name + "_rel"] = torch.zeros(size * 2, dtype=torch.long)
            data[name + "_val"] = torch.randn(size * 2, generator=generator)
            data[name + "_dir"] = torch.randn(size * 2, 2, generator=generator)
        datas.append(data)
    return datas


@pytest.fixture
def datas():
    return make_datas()


def reference_norm(datas, cap):
    x = torch.cat([d.x for d in datas])
    nt = torch.cat([d.node_type for d in datas])
    center = torch.zeros(len(S.NODE_TYPES), S.MAX_FIELDS)
    spread = torch.ones_like(center)
    fitted = 0
    for t in range(len(S.NODE_TYPES)):
        rows = x[nt == t]
        if rows.shape[0] < 8:
            continue
        for c in range(S.MAX_FIELDS):
            values = rows[:, c]
            if len(values) > cap:
                generator = torch.Generator().manual_seed(t * S.MAX_FIELDS + c)
                values = values[torch.randint(len(values), (cap,), generator=generator)]
            q = torch.quantile(values, torch.tensor([0.25, 0.5, 0.75]))
            iqr = float(q[2] - q[0])
            scale = iqr / 1.349 if iqr > 0 else float(values.std(unbiased=False))
            if scale <= 0:
                continue
            center[t, c] = q[1]
            spread[t, c] = scale
            fitted += 1
    return center, spread, fitted


def test_item_offer_identity_accepts_name_and_rejects_ambiguity():
    from advisor.mapgraph.project_offers import offer_params
    item = {"key": "wh3_main_anc_weapon_hunters_talon", "name": "Hunter's Talon"}
    entity = {"context_id": "18", "state": {"equipped": [item, dict(item)]}}
    rec = {"world": {}, "entities": [{"context_kind": "campaign", "state": {"anc_pool": [item]}}]}
    for action in ("items", "item_unequip"):
        for identity in (item["key"], item["name"]):
            assert offer_params(rec, entity, action, identity, None) == {"item_key": item["key"]}
    entity["state"]["equipped"].append({"key": "different_item", "name": item["name"]})
    with pytest.raises(ValueError, match="2 distinct keys"):
        offer_params(rec, entity, "item_unequip", item["name"], None)
    with pytest.raises(ValueError, match="0 distinct keys"):
        offer_params(rec, entity, "item_unequip", "missing", None)
    entity["state"]["equipped"] = [{"key": None, "name": "Unknown item"}]
    assert offer_params(rec, entity, "item_unequip", "Unknown item", None) == {"item_key": None}


def test_study_patience_stops_at_40_and_resets_on_improvement():
    from advisor.mapgraph.optimize_greedy import _patience_cb
    stops = []
    state = SimpleNamespace(is_finished=lambda: True)
    study = SimpleNamespace(best_trial=SimpleNamespace(number=0), stop=lambda: stops.append(1),
                            trials=[SimpleNamespace(number=i, state=state) for i in range(40)])
    callback = _patience_cb(40)
    callback(study, study.trials[-1])
    assert stops == []
    study.trials.append(SimpleNamespace(number=40, state=state))
    callback(study, study.trials[-1])
    assert len(stops) == 1
    stops.clear()
    study.best_trial.number = 20
    callback(study, study.trials[-1])
    assert stops == []


@pytest.mark.parametrize("cap", (2_000_000, 17))
def test_normalization_matches_original_with_and_without_sampling(datas, monkeypatch, cap):
    monkeypatch.setattr(N, "_NORM_SAMPLE", cap)
    actual = N.norm_stats(datas)
    expected = reference_norm(datas, cap)
    assert actual[2] == expected[2]
    torch.testing.assert_close(actual[0], expected[0], rtol=1e-6, atol=1e-7)
    torch.testing.assert_close(actual[1], expected[1], rtol=1e-6, atol=1e-7)


def test_vectorized_batch_and_roundtrip_match_pyg(datas):
    actual = T._batch(datas)
    expected = Batch.from_data_list(datas)
    assert set(actual.keys()) == set(expected.keys())
    for key in actual.keys():
        assert torch.equal(actual[key], expected[key]), key
    for actual_data, original in zip(actual.to_data_list(), datas):
        for key in original.keys():
            assert torch.equal(actual_data[key], original[key]), key


def test_grouped_norm_constant_and_zero_iqr_fields():
    types = torch.tensor([0] * 32 + [1] * 8 + [2] * 7)
    x = torch.zeros(len(types), S.MAX_FIELDS)
    x[:32, 0] = 42
    x[31, 1] = 1000
    x[32:40, 0] = torch.arange(8)
    datas = [N.DecisionGraph(x=x, node_type=types)]
    actual, expected = N.norm_stats(datas), reference_norm(datas, N._NORM_SAMPLE)
    assert actual[2] == expected[2]
    torch.testing.assert_close(actual[0], expected[0])
    torch.testing.assert_close(actual[1], expected[1])


def test_vectorized_labels_match_tensor_arithmetic(datas):
    ys = [float(d.y) for d in datas]
    mean, sd = 0.123456789, 0.987654321
    actual = (np.asarray(ys, dtype=np.float32) - np.float32(mean)) / np.float32(sd)
    expected = torch.cat([(d.y - mean) / sd for d in datas])
    assert torch.equal(torch.from_numpy(actual), expected)


def test_contiguous_transport_preserves_every_tensor(datas):
    import pickle
    from advisor.mapgraph.source import _pack, GraphView
    block = pickle.loads(pickle.dumps(_pack(datas), protocol=5))
    views = [GraphView(block, i) for i in range(len(datas))]
    for actual, original in zip(views, datas):
        for key in original.keys():
            assert torch.equal(actual[key], original[key]), key
    actual_batch, expected_batch = T._batch(views), T._batch(datas)
    for key in actual_batch.keys():
        assert torch.equal(actual_batch[key], expected_batch[key]), key
    actual_norm, expected_norm = N.norm_stats(views), N.norm_stats(datas)
    torch.testing.assert_close(actual_norm[0], expected_norm[0])
    torch.testing.assert_close(actual_norm[1], expected_norm[1])


@pytest.mark.parametrize("use_views", (False, True))
def test_consuming_partitions_preserves_shuffled_batches_and_releases_blocks(datas, use_views):
    from advisor.mapgraph.source import _pack, GraphView
    originals = [d.clone() for d in datas]
    block = _pack(originals)
    packed = [GraphView(block, i) for i in range(len(originals))] if use_views else originals
    partitions = [[packed[3], packed[0], packed[2]], [packed[1]]]
    expected = [[T._batch(part[i:i + 2]) for i in range(0, len(part), 2)] for part in partitions]
    actual = T._collate_partitions(partitions, 2, torch.device("cpu"), lambda s: None)
    assert partitions == [[], []]
    if use_views:
        assert block == {}
    for observed, reference in zip(actual, expected):
        for batch, wanted in zip(observed, reference):
            for key in wanted.keys():
                assert torch.equal(batch[key], wanted[key]), key
            for row, wanted_row in zip(batch.to_data_list(), wanted.to_data_list()):
                for key in wanted_row.keys():
                    assert torch.equal(row[key], wanted_row[key]), key
