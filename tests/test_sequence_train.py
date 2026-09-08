import time

import numpy as np
import pytest
import torch

from advisor.mapgraph import build as B, greedy_train as GT, net as N, schema as S
from advisor.mapgraph import sequence_train as ST, train as T
from advisor.mapgraph.representation import GraphRepresentation
from advisor.mapgraph.sequence_config import history_indices
from advisor.mapgraph.temporal import TemporalReward


def examples():
    datas, ys, groups = [], [], []
    for i in range(30):
        graph = B.Graph()
        entity = graph.add("entity", "lord")
        region = graph.add("region", "region")
        first = graph.add("first", "action")
        second = graph.add("second", "action")
        if i % 3:
            graph.edge(entity, region, S.RELATIONS[0])
            graph.edge(first, entity, S.RELATIONS[0])
            graph.edge(second, region, S.RELATIONS[0])
        graph.g_ctx = [i / 30] + [0.0] * (S.G_CTX_DIM - 1)
        graph.finalize()
        ys.append(float(i % 7 - 3))
        groups.append("campaign%d" % (i // 3))
        datas.append(N.to_data(graph, y=ys[-1], taken=[i % 2, 1 - i % 2]))
    return datas, ys, groups


def configuration(objective="denoise", kind="gru", device="cpu"):
    return dict(T.CFG, hidden=4, entity_layers=1, action_rounds=1, attn="none",
        map_aggr="mean", act_aggr="mean", dropout=0.0, representation_hidden=4,
        latent=4, representation_objective=objective, mask=0.0 if objective == "reconstruct" else 0.2,
        batch=8, threads=1, device=device, bf16=device == "cuda", ssl_epochs=1,
        ssl_patience=1, encoder_fraction=0.7, sequence_kind=kind, sequence_hidden=4,
        sequence_layers=1, sequence_dropout=0.0, history_length=3,
        sequence_lr=1e-3, sequence_weight_decay=1e-4, sequence_epochs=2,
        sequence_patience=2, sequence_batch=8)


@pytest.mark.parametrize("objective,kind", [
    ("reconstruct", "gru"), ("denoise", "gru"), ("contrastive", "gru"),
    ("hybrid", "gru"), ("denoise", "lstm"), ("denoise", "transformer")])
def test_two_stage_checkpoint_reproduces_validation(objective, kind, tmp_path):
    cfg = configuration(objective, kind)
    datas, ys, groups = examples()
    prep = GT.prepare(datas, ys, groups, cfg, free_datas=True, log=lambda _: None)
    assert not datas
    directory = tmp_path / "trial"
    fit = ST.fit_net(ys, groups, list(range(30)), cfg, prep, str(directory),
                     time.perf_counter() + 30, log=lambda _: None)
    assert not prep["loader"] and not prep["vloader"]
    assert {p.name for p in directory.iterdir()} == {"checkpoint.pt", "fit.json"}
    checkpoint = torch.load(directory / "checkpoint.pt", weights_only=False)
    encoder = GraphRepresentation(cfg).eval()
    encoder.load_state_dict(checkpoint["encoder"])
    sequence = TemporalReward(cfg["latent"], **checkpoint["sequence_config"]).eval()
    sequence.load_state_dict(checkpoint["sequence"])
    datas, _, _ = examples()
    with torch.no_grad():
        embeddings = encoder.encode(T._batch(datas)).numpy()
        normalized = (embeddings - checkpoint["embedding_mean"]) / checkpoint["embedding_sd"]
        tokens = torch.tensor(np.concatenate((normalized, np.zeros((1, cfg["latent"]), dtype=np.float32))))
        histories = history_indices(groups, cfg["history_length"])
        indices = torch.full((len(groups), cfg["history_length"]), len(groups), dtype=torch.long)
        lengths = torch.tensor([len(h) for h in histories])
        for i, history in enumerate(histories):
            indices[i, :len(history)] = torch.tensor(history, dtype=torch.long)
        validation = checkpoint["validation_indices"]
        actual = sequence(tokens[validation], tokens[indices[validation]], lengths[validation]).numpy()
    reward_predictions = actual * checkpoint["reward_sd"] + checkpoint["reward_mean"]
    np.testing.assert_allclose(reward_predictions, checkpoint["validation_predictions"], atol=1e-5)
    targets = np.asarray(ys)[validation]
    assert np.mean((reward_predictions - targets) ** 2) == pytest.approx(fit["val_mse"], abs=1e-5)
    assert fit["val_r2"] == pytest.approx(1 - fit["val_mse"] / targets.var())
    assert fit["encoder"]["epochs"] == 1


def test_embeddings_restore_shuffled_loader_order_and_ignore_rewards():
    datas, ys, groups = examples()
    cfg = configuration()
    prep = GT.prepare(datas, ys, groups, cfg, log=lambda _: None)
    assert prep["train_loader_indices"] != prep["train_indices"]
    model = GraphRepresentation(cfg).eval()
    model.encoder.type_enc.load_norm(prep["norm"])
    actual, _ = ST._embeddings(model, prep, cfg, time.perf_counter() + 30, lambda _: None)
    with torch.no_grad():
        expected = model.encode(T._batch(datas)).numpy()
    np.testing.assert_allclose(actual, expected, atol=1e-5)
    batch = prep["loader"][0]
    original = batch.is_taken.clone()
    clean = ST._input(batch, "cpu")
    assert "y" not in clean and "y_z" not in clean
    assert "y" in batch and "y_z" in batch
    ST.sample_candidate(clean)
    assert torch.equal(batch.is_taken, original)


def test_preparation_checks_deadline_during_normalization_and_collation():
    datas, ys, groups = examples()
    cfg = configuration()
    with pytest.raises(TimeoutError):
        GT.prepare(datas, ys, groups, cfg, free_datas=True, deadline=0, log=lambda _: None)
    norm = N.norm_stats(datas)
    with pytest.raises(TimeoutError):
        GT.prepare(datas, ys, groups, cfg, norm=norm, free_datas=True, deadline=0, log=lambda _: None)


@pytest.mark.parametrize("kind", ["gru", "lstm", "transformer"])
def test_padding_and_empty_history_do_not_affect_predictions(kind):
    torch.manual_seed(2)
    model = TemporalReward(4, 4, kind, 1, 0.0, 3).eval()
    current, past = torch.randn(2, 4), torch.randn(2, 3, 4)
    lengths = torch.tensor([0, 1])
    with torch.no_grad():
        expected = model(current, past, lengths)
        past[0] = 1000
        past[1, 1:] = -1000
        actual = model(current, past, lengths)
    torch.testing.assert_close(actual, expected)


def test_representation_width_controls_heads_and_unused_heads_are_absent():
    small = GraphRepresentation(configuration("contrastive"))
    large = GraphRepresentation(dict(configuration("contrastive"), representation_hidden=64))
    assert sum(p.numel() for p in small.parameters()) < sum(p.numel() for p in large.parameters())
    assert not hasattr(small, "node_decoder")
    reconstruction = GraphRepresentation(configuration("reconstruct"))
    assert not hasattr(reconstruction, "projector")


def test_reconstruction_accepts_a_batch_with_no_edges():
    datas, _, _ = examples()
    batch = T._batch(datas[::3])
    model = GraphRepresentation(configuration("reconstruct"))
    loss, _ = model.reconstruction(model.encode(batch), batch)
    assert torch.isfinite(loss)
    loss.backward()


def test_two_stage_cuda_bfloat16(tmp_path):
    if not torch.cuda.is_available():
        pytest.skip("CUDA is unavailable")
    cfg = configuration("hybrid", "transformer", "cuda")
    datas, ys, groups = examples()
    prep = GT.prepare(datas, ys, groups, cfg, free_datas=True, log=lambda _: None)
    directory = tmp_path / "cuda"
    fit = ST.fit_net(ys, groups, list(range(30)), cfg, prep, str(directory),
                     time.perf_counter() + 30, log=lambda _: None)
    checkpoint = torch.load(directory / "checkpoint.pt", weights_only=False)
    validation = checkpoint["validation_indices"]
    residual = checkpoint["validation_predictions"] - np.asarray(ys)[validation]
    assert np.mean(residual ** 2) == pytest.approx(fit["val_mse"], abs=1e-5)
    assert fit["encoder"]["epochs"] == 1 and fit["sequence"]["epochs"] == 2


@pytest.mark.parametrize("deadline,expires", [(3.0, False), (1.5, True)])
def test_epoch_budget_keeps_only_complete_validation(monkeypatch, deadline, expires):
    now = [0.0]
    monkeypatch.setattr(time, "perf_counter", lambda: now[0])
    model = torch.nn.Linear(1, 1)

    def step(batch):
        now[0] += 1
        predicted = model(batch)
        return predicted.square().mean(), len(batch), predicted

    loaders = ([torch.ones(1, 1)], [torch.ones(1, 1)])
    cfg = dict(lr=0.01, weight_decay=0, grad_clip=1, epochs=5, patience=2, seed=0)
    if expires:
        with pytest.raises(TimeoutError):
            ST._fit("test", model, step, loaders, cfg, deadline, lambda _: None)
    else:
        fit, predicted = ST._fit("test", model, step, loaders, cfg, deadline, lambda _: None)
        assert fit["epochs"] == 1 and fit["stopped_by"] == "time_budget"
        assert fit["val_loss"] == pytest.approx(float(predicted.square().mean()))
