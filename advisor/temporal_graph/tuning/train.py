import json
import logging
from pathlib import Path
import time

import numpy as np

from ..config import GraphConfig, GraphLimits
from ..graph import build_temporal_graph
from advisor.reward_data import open_live, remaining, score
from .source import CampaignEncoder, query_at
from .packing import GraphCache, batches, collate, graph_arrays


LOG = logging.getLogger(__name__)


def prepare_graphs(corpus, config, cache, deadline):
    started = time.perf_counter()
    LOG.info("prepare_graphs enter rows=%d", len(corpus.rows))
    graph_config, limits = GraphConfig(**config["graph"]), GraphLimits(**config["limits"])
    metrics = dict(graphs=0, nodes=0, edges=0, graph_seconds=0.0)
    for campaign, records in corpus.campaigns():
        remaining(deadline)
        encoder, rows = CampaignEncoder(campaign), []
        for row, record in records:
            remaining(deadline)
            encoder.append(record, row["action"])
            if row["y"] is not None:
                rows.append(row)
        history, queries = encoder.finish()
        del encoder
        for row in rows:
            remaining(deadline)
            query = query_at(queries, row["step"])
            graph = build_temporal_graph(history, query, graph_config,
                limits=limits, cutoff=row["step"], receivers="all")
            cache.append(graph_arrays(graph, row["step"], config["model"]["time_features"]))
            metrics["graphs"] += 1
            metrics["nodes"] += len(graph.nodes.kind)
            metrics["edges"] += len(graph.edge_score)
            metrics["graph_seconds"] += graph.metrics["seconds"]
        LOG.info("prepare_graphs campaign=%s graphs=%d seconds=%.2f", campaign, metrics["graphs"], time.perf_counter() - started)
    cache.finish()
    remaining(deadline)
    metrics.update(seconds=time.perf_counter() - started, cache_bytes=cache.size)
    LOG.info("prepare_graphs exit seconds=%.2f", metrics["seconds"])
    return metrics


def fit(window, config, directory, deadline, device="cuda", seed=0, max_cache_bytes=8 * 2**30):
    import torch
    from .model import TemporalRewardNet

    started = time.perf_counter()
    LOG.info("fit enter device=%s", device)
    remaining(deadline)
    torch.set_num_threads(4)
    torch.manual_seed(seed)
    torch.set_float32_matmul_precision("high")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable; select --device cpu explicitly")
    directory = Path(directory)
    cfg = config["model"]
    cache = GraphCache(directory / "graph_cache.bin", max_cache_bytes)
    try:
        with open_live(window, deadline) as corpus:
            graph_metrics = prepare_graphs(corpus, config, cache, deadline)
            rows, split = corpus.rows, corpus.split
        model = TemporalRewardNet(cache.rows[0]["x"][2][1], cfg).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
        ys = np.asarray([r["y"] for r in rows], dtype=np.float32)
        normalized = (ys - np.float32(split["reward_mean"])) / np.float32(split["reward_sd"])
        targets = torch.as_tensor(normalized, device=device)
        rng = np.random.default_rng(seed)
        best, best_epoch, stale, curve = float("inf"), 0, 0, []
        best_state, predictions = None, None
        longest_epoch = 0.0
        stopped = "epoch_limit"
        for epoch in range(cfg["epochs"]):
            remaining(deadline)
            epoch_started = time.perf_counter()
            if best_state is not None and remaining(deadline) <= longest_epoch + 5:
                stopped = "time_budget"
                break
            totals, outputs = {}, []
            for training, indices in ((True, rng.permutation(split["train_indices"])), (False, split["validation_indices"])):
                model.train(training)
                total, count = 0.0, 0
                with torch.set_grad_enabled(training):
                    for batch_indices in batches(cache, indices, cfg["batch"]):
                        remaining(deadline)
                        batch = {key: torch.as_tensor(value, device=device) for key, value in collate(cache, batch_indices).items()}
                        predicted = model(batch)
                        loss = (predicted - targets[batch_indices]).square().mean()
                        if not torch.isfinite(loss):
                            raise FloatingPointError("nonfinite temporal reward loss")
                        if training:
                            optimizer.zero_grad(set_to_none=True)
                            loss.backward()
                            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg["grad_clip"])
                            optimizer.step()
                        else:
                            outputs.append(predicted.detach().cpu().numpy())
                        total += float(loss.detach()) * len(batch_indices)
                        count += len(batch_indices)
                        remaining(deadline)
                totals["train_mse" if training else "val_mse"] = total / count
            epoch_score = totals["val_mse"]
            curve.append(dict(epoch=epoch + 1, **totals))
            stale = 0 if epoch_score < best - 1e-5 else stale + 1
            if epoch_score < best:
                best, best_epoch = epoch_score, epoch + 1
                best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
                predictions = np.concatenate(outputs) * split["reward_sd"] + split["reward_mean"]
            LOG.info("epoch=%d normalized_val_mse=%.6f best=%.6f seconds=%.2f", epoch + 1, epoch_score, best, time.perf_counter() - started)
            longest_epoch = max(longest_epoch, time.perf_counter() - epoch_started)
            if stale >= cfg["patience"]:
                stopped = "patience"
                break
        remaining(deadline)
        if best_state is None:
            raise TimeoutError("no complete validation pass within the trial budget")
        torch.save(dict(model=best_state, config=config, feature_width=cache.rows[0]["x"][2][1],
            reward_mean=split["reward_mean"], reward_sd=split["reward_sd"],
            campaigns=[r["campaign"] for r in rows], decisions=[r["decision"] for r in rows],
            train_indices=split["train_indices"], validation_indices=split["validation_indices"],
            validation_predictions=predictions, seed=seed), directory / "checkpoint.pt")
        result = dict(score([rows[i]["y"] for i in split["validation_indices"]], predictions), normalized_val_mse=best,
            epochs_run=len(curve), best_epoch=best_epoch, stopped_by=stopped, curve=curve,
            train_rows=len(split["train_indices"]), val_rows=len(split["validation_indices"]),
            graph_metrics=graph_metrics, seconds=time.perf_counter() - started,
            peak_cuda_bytes=torch.cuda.max_memory_allocated() if device == "cuda" else 0)
        (directory / "fit.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        remaining(deadline)
        LOG.info("fit exit seconds=%.2f val_mse=%.6f", result["seconds"], result["val_mse"])
        return result
    finally:
        cache.close()
