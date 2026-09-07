# GNN full-window startup verification

Same window 2500, trial-0 model and graph parameters, batch 512, 133033 valid graphs, 54350107 nodes, 273759473 directed edges and 17232242532 tensor bytes in every run. Each check freshly queried the full source and stopped after its first optimizer step. No compressed inputs, cached graphs, reduced sample, or changed GPU-budget gate.

| Measurement | Original | Check 1 | Check 2 |
|---|---:|---:|---:|
| First step, seconds | 504.498 | 194.394 | 204.314 |
| Full graph walk, seconds | 398.231 | 164.090 | 175.079 |
| Graph workers | 2 | 4 | 4 |
| Peak GPU allocation, GiB | at least 37.811 (sampled) | 23.423 | 23.423 |

Changes: bounded vectorized neighbor selection preserves the shared sort order and caps; four bounded graph workers; CPU training batches copied individually to the GPU instead of retaining the entire input corpus there; first-step timing saved immediately even if a later epoch fails. GPU memory formerly exceeded the 31.842 GiB device capacity under Windows; the new measured peaks fit on the device. The residency change uses more host RAM in exchange for less GPU residency.

Startup improved by 59.5-61.5 percent. The first step is measured after constructing every graph and computing normalization; this is not a deferred-work or subset timing. These checks do not establish validation quality or completion of a full epoch.

Validation: 15 unit tests passed, including all relation caps, ordering equivalence across chunk boundaries, missing locations, exact ties, zero-edge model forward/backward, median defaults and tuner configuration.

Relaunch settings: 12 edge limits, window 2500, maximum 1000 trials, study patience 100, fitting budget 600 seconds per trial. The existing 60-second epoch cap remains.
