import copy
import json
import os
import time

import numpy as np
import torch

from advisor.mapgraph.trial_budget import remaining
from advisor.mapgraph.representation import GraphRepresentation, corrupt, sample_candidate
from advisor.mapgraph.temporal import TemporalReward
from advisor.mapgraph.sequence_config import history_indices


def _state(model):
    return {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}


def _input(batch, device):
    batch = copy.copy(batch).to(device)
    for key in ("y", "y_z"):
        if key in batch:
            del batch[key]
    return batch


def _fit(name, model, step, loaders, cfg, deadline, log):
    started = time.perf_counter()
    log("%s enter" % name)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    best, state, stale, curve, predictions = float("inf"), None, 0, [], None
    stopped = "epoch_limit"
    longest_epoch = 0.0
    for epoch in range(cfg["epochs"]):
        epoch_started = time.perf_counter()
        totals, outputs = {}, []
        for training, loader in zip((True, False), loaders):
            model.train(training)
            torch.manual_seed(cfg["seed"] + epoch + 17 if training else 101)
            order = torch.randperm(len(loader)).tolist() if training else range(len(loader))
            total, count = 0.0, 0
            with torch.set_grad_enabled(training):
                for index in order:
                    remaining(deadline)
                    loss, weight, predicted = step(loader[index])
                    if not torch.isfinite(loss):
                        raise RuntimeError("Nonfinite %s loss" % name)
                    if training:
                        optimizer.zero_grad(set_to_none=True)
                        loss.backward()
                        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg["grad_clip"])
                        optimizer.step()
                    elif predicted is not None:
                        outputs.append(predicted.detach().cpu())
                    total += float(loss.detach()) * weight
                    count += weight
                    remaining(deadline)
            totals["train_loss" if training else "val_loss"] = total / count
        score = totals["val_loss"]
        curve.append(dict(epoch=epoch + 1, seconds=time.perf_counter() - started, **totals))
        stale = 0 if score < best - 1e-5 else stale + 1
        if score < best:
            best, state, best_epoch = score, _state(model), epoch + 1
            predictions = torch.cat(outputs) if outputs else None
        log("%s epoch=%d val=%.5f best=%.5f %.1fs" %
            (name, epoch + 1, score, best, time.perf_counter() - started))
        remaining(deadline)
        longest_epoch = max(longest_epoch, time.perf_counter() - epoch_started)
        if stale >= cfg["patience"]:
            stopped = "patience"
            break
        if epoch + 1 < cfg["epochs"] and remaining(deadline) <= longest_epoch:
            stopped = "time_budget"
            break
    model.load_state_dict(state)
    model.eval().requires_grad_(False)
    fit = dict(val_loss=best, epochs=len(curve), best_epoch=best_epoch, curve=curve,
        seconds=time.perf_counter() - started, stopped_by=stopped)
    log("%s exit %.1fs stopped=%s" % (name, fit["seconds"], stopped))
    return fit, predictions


def _pretrain(prep, cfg, deadline, log):
    started = time.perf_counter()
    model = GraphRepresentation(cfg).to(cfg["device"])
    model.encoder.type_enc.load_norm(prep["norm"])
    amp = cfg["device"] == "cuda" and cfg["bf16"]

    def step(data):
        batch, selected, alternative = sample_candidate(_input(data, cfg["device"]))
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp):
            all_z, _ = model.encode_all(corrupt(batch, cfg["mask"]))
            z = all_z[selected]
            if cfg["representation_objective"] != "contrastive":
                loss, _ = model.reconstruction(z, batch)
            if cfg["representation_objective"] in ("contrastive", "hybrid"):
                other = model.encode(corrupt(batch, cfg["mask"]))
                contrast, _ = model.contrastive(z, other, all_z[alternative], batch.n_actions > 1)
                loss = contrast if cfg["representation_objective"] == "contrastive" else loss + contrast
        return loss, len(batch.n_actions), None

    fit, _ = _fit("encoder", model, step, (prep["loader"], prep["vloader"]),
        dict(lr=cfg["lr"], weight_decay=cfg["weight_decay"], grad_clip=cfg["grad_clip"],
             epochs=cfg["ssl_epochs"], patience=cfg["ssl_patience"], seed=cfg["seed"]), deadline, log)
    fit["seconds"] = time.perf_counter() - started
    return model, fit


def _embeddings(model, prep, cfg, deadline, log):
    started = time.perf_counter()
    log("embedding extraction enter")
    embeddings = np.empty((prep["train_rows"] + prep["val_rows"], cfg["latent"]), dtype=np.float32)
    with torch.no_grad():
        for loader, indices in ((prep["loader"], prep["train_loader_indices"]),
                                (prep["vloader"], prep["validation_indices"])):
            offset = 0
            for data in loader:
                remaining(deadline)
                batch = _input(data, cfg["device"])
                size = len(batch.n_actions)
                embeddings[indices[offset:offset + size]] = model.encode(batch).cpu().numpy()
                offset += size
            if offset != len(indices):
                raise ValueError("Prepared batches do not match their row indices")
    remaining(deadline)
    if not np.isfinite(embeddings).all():
        raise RuntimeError("Nonfinite embeddings")
    seconds = time.perf_counter() - started
    log("embedding extraction exit %.1fs" % seconds)
    return embeddings, seconds


def _head(embeddings, ys, groups, prep, cfg, deadline, log):
    started = time.perf_counter()
    train, validation = prep["train_indices"], prep["validation_indices"]
    mean = embeddings[train].mean(0)
    sd = np.maximum(embeddings[train].std(0, ddof=1), 0.01)
    normalized = (embeddings - mean) / sd
    tokens = torch.tensor(np.concatenate((normalized, np.zeros((1, cfg["latent"]), dtype=np.float32))), device=cfg["device"])
    history = history_indices(groups, cfg["history_length"])
    indices = np.full((len(groups), cfg["history_length"]), len(groups), dtype=np.int64)
    for i, previous in enumerate(history):
        indices[i, :len(previous)] = previous
    histories = torch.tensor(indices, device=cfg["device"])
    lengths = torch.tensor([len(h) for h in history], device=cfg["device"])
    targets = torch.tensor((np.asarray(ys, dtype=np.float32) - np.float32(prep["y_mean"])) /
        np.float32(prep["y_sd"]), device=cfg["device"])
    torch.manual_seed(cfg["seed"])
    model_config = dict(hidden=cfg["sequence_hidden"], kind=cfg["sequence_kind"],
        layers=cfg["sequence_layers"], dropout=cfg["sequence_dropout"], history_length=cfg["history_length"])
    model = TemporalReward(cfg["latent"], **model_config).to(cfg["device"])
    rng = np.random.default_rng(cfg["seed"])
    loaders = [[torch.as_tensor(rows[start:start + cfg["sequence_batch"]], device=cfg["device"])
                for start in range(0, len(rows), cfg["sequence_batch"])]
               for rows in (rng.permutation(train), validation)]

    def step(ix):
        predicted = model(tokens[ix], tokens[histories[ix]], lengths[ix])
        return (predicted - targets[ix]).square().mean(), len(ix), predicted

    fit, predicted = _fit("sequence", model, step, loaders,
        dict(lr=cfg["sequence_lr"], weight_decay=cfg["sequence_weight_decay"], grad_clip=1.0,
             epochs=cfg["sequence_epochs"], patience=cfg["sequence_patience"], seed=cfg["seed"]), deadline, log)
    fit.update(val_mse=fit["val_loss"], val_r2=1 - fit["val_loss"] / prep["val_var"])
    checkpoint = dict(sequence=_state(model), sequence_config=model_config,
        embedding_mean=mean, embedding_sd=sd, reward_mean=prep["y_mean"], reward_sd=prep["y_sd"],
        validation_predictions=predicted.numpy() * prep["y_sd"] + prep["y_mean"])
    fit["seconds"] = time.perf_counter() - started
    return fit, checkpoint


def fit_net(ys, groups, decisions, cfg, prep, directory, deadline, log=print):
    started = time.perf_counter()
    log("two-stage fit enter")
    if not prep["train_rows"] or not prep["val_rows"]:
        raise ValueError("Two-stage training requires both prepared partitions")
    torch.manual_seed(cfg["seed"])
    torch.set_float32_matmul_precision("high")
    encoder_deadline = time.perf_counter() + remaining(deadline) * cfg["encoder_fraction"]
    model, encoder_fit = _pretrain(prep, cfg, encoder_deadline, log)
    embeddings, embedding_seconds = _embeddings(model, prep, cfg, deadline, log)
    encoder_state = _state(model)
    del model
    prep["loader"].clear()
    prep["vloader"].clear()
    sequence_fit, checkpoint = _head(embeddings, ys, groups, prep, cfg, deadline, log)
    checkpoint.update(encoder=encoder_state, config=cfg, decisions=decisions, campaigns=groups,
        train_indices=prep["train_indices"], validation_indices=prep["validation_indices"])
    fit = dict(val_mse=sequence_fit["val_mse"], val_r2=sequence_fit["val_r2"],
        epochs_run=sequence_fit["epochs"], stopped_by=sequence_fit["stopped_by"],
        encoder=encoder_fit, sequence=sequence_fit, embedding_seconds=embedding_seconds,
        train_rows=prep["train_rows"], val_rows=prep["val_rows"], val_var=prep["val_var"])
    remaining(deadline)
    os.makedirs(directory, exist_ok=False)
    torch.save(checkpoint, os.path.join(directory, "checkpoint.pt"))
    fit["seconds"] = time.perf_counter() - started
    with open(os.path.join(directory, "fit.json"), "w", encoding="utf-8") as file:
        json.dump(fit, file, indent=2, allow_nan=False)
    remaining(deadline)
    log("two-stage fit exit %.1fs R2=%.5f" % (fit["seconds"], fit["val_r2"]))
    return fit
