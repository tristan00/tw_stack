r"""campaigns -- split a recorded run directory into its constituent campaigns.

A single run dir can hold several campaigns/sessions (quit-to-menu + new game, or the
recorder tailing across game relaunches). This package detects the campaign boundaries
from the state log's human-faction identity over time and partitions every stream
(state log, events.jsonl, ui_components.jsonl, shots) into per-campaign parts, with a
hard invariant: sum-of-parts == original, nothing dropped.

Public API (see splitter.py):
    split_run(run_dir)               -> full per-campaign partition + reconciliation
    detect_campaigns(run_dir)        -> campaigns with state byte-ranges + t-windows
    segment_blocks(blocks)           -> pure boundary logic over identity blocks
    CampaignTracker                  -> incremental/live boundary detector for the recorder
"""
from __future__ import annotations

from .splitter import (
    CampaignTracker,
    detect_campaigns,
    segment_blocks,
    split_run,
)
