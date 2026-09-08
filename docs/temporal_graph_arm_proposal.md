# Independent temporal graph arm

The implemented experiment lives in `advisor/temporal_graph`. Mapgraph retains its own graph-to-sequence design. The temporal constructor shares no mapgraph schema, node definitions or edge defaults.

The temporal model predicts the existing reward from current/past game states and the chosen action. Both tuners read the latest N whole campaigns and call the existing deterministic campaign split. They report reward MSE in the same units. Frozen datasets and cross-run row-matching machinery are excluded.

The constructor explores different node meanings: observations, changes, groups of simultaneous observations, stream traces, and a mixture of recent detail with compressed older history. Its eight main controls are node view, resolution, history, affinity kernel, layout, degree, arity and time bias. Candidate-pool size and query degree additionally control retrieval coverage and readout connectivity.

Layouts cover nearest neighbors, reciprocal links, adaptive radius, forests, hubs, incidence groups, dense graphs and empty observation graphs. Affinities cover identity, references, similarity, contrast, correlated changes, geometry and mixtures. Explicit learned projection matrices are supported by the constructor but are not trained by the current tuner.

Both clocks remain available. A settlement observation can link to its previous decision observation and its previous-turn observation. When these identify the same predecessor, one edge carries both flags. Edges only carry information forward in time; the action being scored is a query sink rather than an observed historical action.

Construction uses columnar NumPy arrays, indexed candidate retrieval, receiver blocks, batched scoring and grouped reductions. Sparse pair work scales with nodes × candidate slots × feature width. Exact dense construction remains quadratic. A small cache reuses node packing and retrieval indices when trying several topologies on the same prefix. Advancing the cutoff rebuilds that prefix.

| Challenge | Current approach |
|---|---|
| Broad graph exploration | Tune node meaning, topology, affinity and temporal resolution independently; measure actual topology diversity. |
| Fast construction | Bounded indexed proposals and vectorized scoring; retain an exact mode for small reference comparisons. |
| Missing observations | Presence masks distinguish zero from absence; anonymous objects do not acquire invented persistent identities. |
| Leakage | History stops at the decision; current-action queries cannot send messages; raw inputs exclude execution outcomes. |
| Large graphs | Batch by node/edge counts and store trial tensors in one temporary memory-mapped file. |
| Comparable trials | Shared live campaign selection, existing split/reward functions and raw reward MSE. |
| Bounded tuning | A spawned worker caps each trial at 600 seconds; Optuna searches graph and message-passing parameters. |

The graph is a finite historical computation, not a persistent online neural memory. Production arm registration and loading are future work. Model quality and real-corpus throughput remain unmeasured until tuning is explicitly started.

See `advisor/temporal_graph/README.md` for the graph API, `BENCHMARK.md` for construction measurements, and `tuning/README.md` for runnable commands and parameter ranges.
