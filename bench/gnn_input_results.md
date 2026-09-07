GNN input performance, measured September 6, 2026 on Windows with an RTX 5090 and 64 GiB system RAM.

During the subsequent window-4,000 study, trial 2 exhausted CPU allocation capacity during collation. The final resource sample showed 9.8 GiB physical RAM available, but Windows committed memory was not yet monitored. After the process exited, the system still had approximately 98.4 GiB committed against a 123.1 GiB limit. This supports committed-memory pressure as the likely cause, but does not establish the exact peak at failure.

Preparation now transfers each tensor field across all training and validation batches, then releases that field from CPU graph blocks before transferring the next field. Previously, randomized batch membership retained nearly every complete CPU block while GPU copies accumulated. The new path preserves the split, shuffled batch membership, order, tensor values, and PyG round-trip metadata. Six unit tests, including shuffled partitions sharing a source block, and two GPU tuning smoke trials passed. The original GPU-budget rejection rule remains unchanged. Resource samples now include committed memory and its limit. The study resumed with its existing results and 497 of 500 attempts remaining. The startup timings below predate this additional transfer change.

The final startup benchmark uses the original cancelled study's trial 0 graph and model parameters, window 2,000, and all 113,210 trainable graphs. The model has hidden width 112, five entity layers, four action rounds, and batch size 512. Graph tensors occupy 10.830 GiB. No smaller subset or model was substituted.

| Trial startup stage | Previous trial 0 | Final benchmark |
| --- | ---: | ---: |
| Full input loading and graph construction | 281.1 s | 174.6 s |
| Normalization and GPU batch preparation | 23.3 s | 21.4 s |
| Probe, setup, and other startup work | Included in total | 7.2 s, including first training step |
| Total from trial start | 309 s to training readiness | 203.2 s to first completed GPU training step |

The final endpoint is later than the original endpoint: the original log did not time the first completed GPU step. The measured reduction is therefore at least 105.8 seconds, or 34.2%. Input loading and graph construction decreased by 37.9%. Header selection happens before the trial clock starts, matching tuning; it takes approximately 0.4 seconds. The final benchmark stops immediately after the first optimizer step, synchronizing CUDA before recording its completion. It does not measure a complete fitting run.

Normalization took 11.2 seconds. Train and validation collation together took 6.9 seconds to build batches, 2.8 seconds to transfer, and 0.4 seconds to release CPU storage. Sampled peak process-tree RSS was 13.146 GiB and minimum available system RAM was 17.715 GiB, sampled every 15 seconds; these samples can miss brief peaks. Each graph worker used approximately 0.25–0.30 GiB. Peak PyTorch GPU allocation through the first training step was 29.329 GiB. The wide model still needs substantial GPU memory; this change reduces input overhead without altering the oversized-graph GPU budget.

Worker timings accumulated across two concurrent graph workers were 93.9 seconds for input, including 50.6 seconds executing SQL and receiving 18,922,209 rows, 167.7 seconds for graph building, and 34.9 seconds constructing numeric arrays. These overlap between workers and must not be added to the wall-clock stages above. SQL timing is a subset of input timing.

The latest structural changes passed 3,000 exact graph comparisons against the saved previous builder and tensor converter, covering 1,000 real decisions and three graph configurations. Parallel transport matched the serial path for 1,196 real graphs, including labels and ordering. Five input unit tests and all mapgraph invariants passed. Benchmarks ran sequentially, with two bounded graph workers within each full benchmark. The cancelled 100-trial study and its monitor remain stopped.

The historical default-graph measurements below use the same window but a different graph configuration (14.631 GiB of tensors), and a smaller training smoke model: hidden width 32, one entity layer, one action round, batch size 512, and a 20-second fitting budget. They are not directly comparable to the final trial-0 startup measurement above.

The original source loader stopped at the benchmark's memory floor after 246 seconds, with 23.344 GiB process RSS, before completing hydration or building graphs. It reconstructed gameplay records and regenerated gameplay offers. A 400-decision profile recorded 8,257 SQL calls, including 7,437 collection reads.

The new loader selects lightweight decision headers, then projects model inputs directly from relational tables in bounded groups of 1,024 decisions. Each batch retains snapshot-to-collection links and reads the referenced member rows once, preserving the normalized database structure in the input batch. SQL joins resolve dictionary identifiers. Stored offer identities determine only the parameters consumed by the graph builder. Campaign memory replay, targets, graph construction, and the selected decision window retain their existing semantics.

Two workers build at most two outstanding groups of 1,024 decisions. Workers construct NumPy graph arrays directly, without creating per-graph PyTorch tensors and converting them back for transport. Each returns contiguous arrays per tensor field, with offsets describing individual graphs. The parent retains lightweight graph views instead of millions of separate tensor objects. Collation concatenates fields and adjusts graph indices with vectorized array operations. CPU input storage is released as its views are consumed. No source or graph cache and no compression are used.

Catalogue scalar extraction is grouped by node type and processed through provenance-aware numeric row blocks. Relation filtering uses a set for the current graph configuration. A redundant complete graph-finalization pass was removed. These changes preserve nodes, edges, values, ordering, and scalar provenance against the previous implementation.

Normalization computes grouped quantiles and variance using NumPy operations, one feature column at a time to bound temporary memory. A small loop retains the original independently seeded samples for node types exceeding the sampling limit. Label arithmetic is vectorized. GPU preparation and fitting use at most four CPU threads by default.

An intermediate full run completed in 597 seconds: graph construction took 304.5 seconds and preparation took 269.4 seconds. Subsequent diagnostic instrumentation attributed roughly 310 seconds to releasing individual graph objects, versus 9 seconds to transfer and 21 seconds to collation. That diagnostic overlapped another run and was profiled; its wall time is not a clean performance comparison. It motivated contiguous field storage and lightweight graph views.

The GPU-budget oversized-trial rule is unchanged. A tuner smoke test forced an oversized projection and verified that preparation was never called. Two accepted trials then completed. No graph-size threshold or model parameter was changed to make an oversized trial pass.

The earlier default-graph checkpoint was executed alone, after verifying the previous benchmark's processes had exited. These are historical measurements preceding the subsequent optimization of total trial startup:

| Measurement | Final result |
| --- | ---: |
| Header selection | 0.6 s |
| SQL input and graph construction | 291.1 s |
| Normalization | 10.9 s |
| Complete preparation, including normalization | 23.2 s |
| CPU graph release within collation | 0.5 s |
| Fitting smoke test | 20.5 s |
| End-to-end time | 339.0 s |
| Sampled peak process-tree RSS | 17.261 GiB |
| Minimum available system memory | 14.204 GiB |
| Peak GPU tensor allocation | 17.698 GiB |

Preparation improved by about 11.6 times from the unprofiled intermediate run, and total elapsed time decreased by 43%. The original end-to-end path never completed the memory-guarded baseline, so there is no measured original total-time ratio. Normalization alone is not claimed to be faster than the original implementation; its grouped implementation bounds temporary storage while retaining the same statistics. The largest measured improvement is removal of individual graph-object allocation and deallocation overhead. The remaining dominant stage is SQL input and graph construction.

Correctness checks cover 2,000 real decisions under default, sparse, and dense graph configurations: 6,000 exact graph comparisons against the gameplay hydration path. Tests also compare normalization against the original formula, including sampling and zero-IQR fields; collation against PyG, including round trips; label arithmetic; and tensor transport.

Run benchmarks sequentially and verify their processes exit before starting another. Current test commands are in `tests/README.md`. These historical measurements used the original validation helpers; the maintained full-window benchmark is `bench/gnn_first_step_check.py`.

The final full-startup log, metrics, and resource samples are in `bench/gnn-startup-final`. Earlier diagnostic profiles and saved reference implementations are archived in `C:/Users/trist/.codex/visualizations/2026/09/06/01a075ce-ec3b-7e60-bc4c-3a105cb995d7/gnn-input-evidence`. The current startup benchmark uses the cancelled 12-edge study's trial-0 parameters, as described in `tests/README.md`.

The profiling script samples process-tree RSS and available system memory every two seconds. It stops its worker processes and exits if available memory falls below 8 GiB. Reported CPU seconds cover the parent process; they are not total CPU consumption across workers. GPU peak is PyTorch's peak allocated memory, not total driver memory. Normalization and preparation timings are logged separately from graph construction.
