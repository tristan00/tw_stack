**Independent temporal graph constructor**

The core package builds causal graphs from columnar game-state observations and historical selected actions. The core has no mapgraph, model, database, PyTorch, or portfolio dependency. NumPy performs construction through array gathers, sorting, reductions, indexed retrieval, and batched affinity calculations. The constructor does not train a model or choose an action.

The optional [tuning package](tuning/README.md) adds a separate recorder adapter, supervised temporal graph reward model, and Optuna runner. It shares the reward definition and campaign split with the sequence tuner, without importing mapgraph. No tuning has been launched as part of this preparation.

```python
from advisor.temporal_graph import ActionQueries, GraphBuildState, GraphConfig, History, build_temporal_graph

history = History.from_arrays(
    values=[[100, 2], [120, 2], [115, 3], [130, 3]],
    stream=[0, 0, 0, 0],
    step=[0, 1, 2, 3],
    turn=[0, 0, 1, 1],
    names=("income", "settlement_level"),
)
queries = ActionQueries.from_arrays([[10, 1], [20, 2]], ids=[100, 101], entity=[0, 0])
state = GraphBuildState()
graph = build_temporal_graph(
    history,
    queries,
    GraphConfig(node_view="observation", kernel="identity", degree=4),
    receivers="current",
    state=state,
)
print(graph.edge_index)
print(graph.metrics)
```

Supply numeric feature vectors in the same declared column basis for observations and action queries. The example values are illustrative; action encoding and extraction from live recorder records belong to the eventual caller. The constructor does not import the current GNN's schema or invent a reward. No normalization is fitted inside graph construction; the caller owns any training-only scaling or categorical encoding.

**Input contract**

`History.from_arrays` prepares one campaign/branch episode. `stream` identifies one continuing observed record or fact; a stream can represent a settlement snapshot or an individual settlement field. `entity` optionally groups streams belonging to an observed object, and `reference` optionally identifies another object. Identity numbers are routing metadata and are not appended to the feature vectors. `xy` is optional observed position; missing positions use NaN.

`step` is a nonnegative local decision ordinal, and `turn` is the game turn. Decision ordinals must be consecutive if a gap of one is intended to mean the immediately preceding action. Do not use globally interleaved database IDs as local decision ordinals. Turns are monotonic and constant within a decision. Each stream has at most one row at a cutoff and a stable observation/action kind.

Raw rows use `NodeKind.OBSERVATION` or `NodeKind.ACTION`. An action row is the selected action event after the decision's state observation. Observation cutoff is `2 * step`; selected-action cutoff is `2 * step + 1`. Therefore scoring decision t sees its state and earlier selections, while excluding its own selected-action event and every future row. `cutoff` optionally selects an earlier decision from a larger immutable history.

Every earlier selected-action row in the configured window is retained without considering execution results. Feature selection belongs to the raw-data adapter. The numeric constructor accepts legitimate game-state fields such as rank and does not inspect names for outcome-like words.

`present` distinguishes an unavailable feature from numerical zero. Nonfinite present values are rejected. `History` owns read-only copies sorted by cutoff and stream; graph provenance indices refer to this sorted history. It precomputes stream predecessors, valid deltas, and four preceding change observations with support masks. Query IDs are unique opaque integers, and query order/chunking does not affect individual query connections.

**Eight graph controls**

| Control | Choices/effect |
|---|---|
| `node_view` | `observation`, `change`, `bundle`, `trace`, `mixed` |
| `resolution` | Maximum raw observations in a bundle/trace; also sets the recent-detail interval in mixed mode |
| `history` | Direct lookback in local decisions; current observations remain available |
| `kernel` | `identity`, `similarity`, `contrast`, `cochange`, `reference`, `geometric`, `learned`, `mixed` |
| `layout` | `knn`, `reciprocal`, `radius`, `forest`, `hub`, `incidence`, `dense`, `empty` |
| `degree` | Incoming top-k budget, group-read budget, or adaptive-radius density control |
| `arity` | Maximum incidence-group size or approximate hub spacing |
| `time_bias` | Signed coefficient on `log1p(decision_gap) + log1p(turn_gap)`; positive favors recent history |

Observation mode keeps individual records. Change mode retains changes, initial records, and latest state anchors. Bundle mode packs co-observed changes. Trace mode packs successive observations within a stream. Mixed mode keeps recent changes and packs older stream observations in logarithmic age buckets. Current state anchors and historical action events remain distinct nodes in every mode. Bundles/traces use presence-aware feature means and retain their original member indices, counts, time intervals, and change summaries. They are actual grouped nodes, not renamed copies of the observation graph.

Similarity and contrast use positive/negative cosine affinity. Cochange uses correlation over supported overlapping change-history positions and requires at least two supported positions with nonzero variation. These positions are preceding observations within each stream, not guaranteed identical game-turn intervals; actual edge decision/turn gaps remain available. Reference affinity tests shared and direct raw references. Geometric affinity uses negative log distance between observed positions.

`learned` requires explicit `LearnedMetric(source_matrix, destination_matrix)` projection weights. The constructor performs no learning itself and does not substitute random weights if they are missing. Mixed affinity initially combines similarity, cochange, identity, references, and geometry with fixed declared coefficients in `affinity`; supplied metric projections add an asymmetric term. Graph diversity measurements label the learned recipe's supplied demonstration weights explicitly.

`knn` selects incoming neighbors. Reciprocal layout requires mutual selection for same-cutoff edges while allowing forward temporal edges. Forest layout permits one parent under strict cutoff/canonical order. Hubs are observed landmarks selected by content-signature order within each cutoff. The adaptive-radius layout selects scores above a receiver-local threshold derived from candidate mean/spread and degree; it is not a globally calibrated metric-ball graph. Increasing degree lowers its threshold. Incidence layout creates explicit anonymous groups with member-to-group and group-to-receiver edges, removes singleton groups, and deduplicates identical membership sets. Dense layout includes every eligible pair under the chosen kernel and ignores degree. Empty layout performs no observation-pair scoring and retains only the separately bounded query readout.

Groups can only send to receivers whose cutoff includes every member. No edge moves information backward in time, and query nodes never send messages. `edge_kind` is a bitmask: previous action, previous turn, identity, reference, affinity mechanism, temporal, query, group input, and group output can coexist as applicable. For example, a single predecessor edge can satisfy both temporal clocks without duplicating its endpoints.

**Bounded construction and explicit exact mode**

Default sparse retrieval proposes at most 64 source slots per receiver from predecessor lookups, entity/shared/direct-reference indices, content/contrast signature buckets, logarithmic temporal positions, spatial buckets, and deterministic global samples. Indexed lookups constrain eligible cutoffs before selecting candidates. Global sampling uses stable query IDs, so candidate reordering cannot change its samples. Candidate duplication is removed before scoring.

Sparse top-k is exact over the proposed pool. It is approximate relative to all eligible source nodes. `exhaustive=True` replaces the bounded pool with all eligible pairs for small reference experiments. Dense layout also uses exact enumeration. The benchmark measures how many exact top-k edges the bounded pool recovers; speed alone is not treated as sufficient graph quality.

`GraphLimits` separately controls candidate slots, query degree, receiving block size, node/edge ceilings, exact pair-work ceiling, and proposal seed. Exceeding limits raises an explicit error. The constructor never silently changes layout, omits a query, reduces a requested top-k below its declared candidate budget, or substitutes another implementation. Empty/low-support neighborhoods can naturally have fewer than k edges.

Pair operations use receiving blocks, default 256 rows, with bounded source proposals. The arithmetic is approximately O(R L d) for R receiving nodes, L slots, and feature width d; grouping, index building, sorting, and output storage are additional costs. Exact enumeration remains quadratic and is protected by `dense_pair_limit`. Higher-order groups consume explicit node/edge budgets.

`receivers="all"` returns the full graph over the materialized prefix. `receivers="current"` returns all retained source nodes but only connections into current observation nodes and candidate queries, plus any required group incidence. It is a current message block, not a cheaply reconstructed complete historical graph. Both modes are benchmarked separately.

`GraphBuildState` caches one immutable prepared prefix and its indices, plus the current immutable query batch. Changing only kernel/layout/degree/arity/time bias can reuse raw packing and retrieval indices. Supplied metric weights are reapplied every call. Changing history object, episode, cutoff, node view, resolution, lookback, or seed invalidates preparation. Changing the query object rebuilds query attachment. Returned arrays are read-only.

Advancing the cutoff currently rematerializes the prefix; a live append/eviction engine is outside this constructor-only implementation. Cold timing is the relevant measurement for a new prefix, while cached timing measures repeated construction on the same prepared prefix, such as graph tuning. There is no neural memory in this package.

The public build function logs entry/exit through Python logging and returns phase timings, node/edge counts, candidate-slot counts, cache hits, and stored-array bytes. `pair_workspace_estimate_bytes` is an estimate, not a measured peak. The benchmark measures peak traced allocations separately.

**Verification**

From the repository root:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_temporal_graph.py
.venv/Scripts/python.exe -m advisor.temporal_graph.benchmark --repeats 5 --output "$env:TEMP\tw_temporal_graph_benchmark.json"
```

The benchmark uses deterministic synthetic state histories with stable and changing streams, shared entities/references, positions, selected-action events, and current action queries. It checks 48 recipes, structural sensitivity to all eight controls, causal/query invariants, full/current graph scaling through more than 132,000 raw rows, and exact endpoint agreement with a scalar scoring/selection reference. It also measures sparse retrieval recall against exact all-pairs graphs.

Timing runs use wall-clock measurement around the complete constructor. Five cached repeats provide a small-sample median/p95; they are not production latency guarantees. Allocation measurements run separately under tracemalloc, include the cold constructor's temporary/retained allocations, and exclude the already prepared History. They are not operating-system resident-memory measurements. No real game, database, reward model, or GPU is used.

See BENCHMARK.md for the measured results and their limits.
