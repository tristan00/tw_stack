# Temporal graph tuning

Both this tuner and mapgraph sequence tuning read the current corpus through `advisor.reward_data`. `--window N` selects the latest N whole campaigns using `DecisionStore.window_keys`. Both reuse `base_model.stable_split`: the existing deterministic campaign holdout with fraction 0.15 and salt `gnn3`. All decisions in a campaign belong to one partition. Given the same campaign window, assignments are the same; there are no row-matching audits or exported datasets.

Both use the existing `decision_deltas` and `target` reward functions. Every recorded chosen action with an available reward is supervised. Earlier decisions without rewards still enter temporal history. Execution validation never determines selection or features.

Both tuners report `val_mse` in **raw reward units**, computed from their validation predictions. Targets are standardized using training rewards internally; `normalized_val_mse` is saved separately. Historical studies retain their original normalized values and are labelled accordingly in the study table. The validation partition guides tuning; it is not an untouched final test set.

Print the search space without reading the database or importing a model:

```powershell
.venv/Scripts/python.exe -m advisor.temporal_graph.tuning.optimize --space
```

Start tuning when ready, using a new output directory:

```powershell
.venv/Scripts/python.exe -m advisor.temporal_graph.tuning.optimize --window 2500 --output D:/twdata/logs/services/optuna_temporal_graph/study_01 --trials 1000 --budget 600 --device cuda
```

No preparation/export step is needed. Each trial reads live data. Defaults are 1,000 trials, 11 TPE startup trials, seed 0, and stopping after 100 completed/pruned trials without improvement. `--timeout` limits total study time. Optuna defaults to SQLite in the output directory; `--storage` accepts another Optuna storage URL.

| Graph parameter | Search values |
|---|---|
| `node_view` | observation, change, bundle, trace, mixed |
| `layout` | knn, reciprocal, radius, forest, hub, incidence, dense, empty |
| `kernel` | identity, similarity, contrast, cochange, reference, geometric, mixed |
| `history` | 0, 1, 2, 4, 8, 16, 32, 64 decisions |
| `resolution` | 1, 2, 4, 8, 16, 32, 64; bundle/trace/mixed only |
| `degree` | 0, 1, 2, 4, 8, 16, 32, 64; forest fixes 1; dense/empty ignore it |
| `arity` | 2, 4, 8, 16, 32, 64; hub/incidence only |
| `time_bias` | −2 to 2 |
| `candidates` | 16, 32, 64, 128; dense enumerates all pairs |
| `query_degree` | 4, 8, 16, 32 |

Degrees cannot exceed the sampled sparse candidate pool; incompatible recipes are pruned. The constructor's optional learned projection kernel is outside this search because this model does not train a topology projection.

| Model parameter | Search values |
|---|---|
| `hidden` | 2, 4, 8, 16, 32, 64, 96, 128 |
| `layers` | 1–4 |
| `aggregation` | mean, sum, attention |
| `update` | residual, gru |
| `time_features` | action, turn, both |
| `dropout` | 0–0.5 |
| `lr` | 0.00001–0.001, logarithmic |
| `weight_decay` | 0.000001–0.1, logarithmic |
| `grad_clip` | 0.1–10, logarithmic |
| `batch` | 8, 16, 32, 64 |
| `epochs` | 5–120, step 5 |
| `patience` | 2–15 |

Raw campaign, entity and world observations use independent 64-column feature hashing with presence masks. Named objects have stable identities; anonymous list members use decision-local identities. Prior chosen actions become event nodes; the current chosen action is a query sink. This encoding is lossy, with possible hash collisions. The model uses float32 message passing with node kinds, edge flags, affinity and elapsed time; its GRU option operates within graph layers.

The shared worker enforces at most **600 seconds per trial**, including reads, graph construction, training, validation and saving. Resource/time limits prune a trial. Fixed ceilings are 20,000 nodes and 200,000 edges per graph, 2,000,000 exact candidate pairs, and 40,000 nodes/400,000 edges per batch.

Temporal graphs are constructed once per trial and held in one temporary memory-mapped tensor file for batching. It is deleted when the worker exits and cannot be loaded as a reusable dataset. The default size ceiling is 8 GiB (`--max-cache-gib`). Graph preparation processes one campaign at a time.

Outputs are Optuna history, settings, logs, fit metrics, model checkpoints, and `best.json`. There is no exported source dataset, manifest, checkpoint comparison command, or production arm registration. No tuning or GPU training was launched during this cleanup.
