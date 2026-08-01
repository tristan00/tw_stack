r"""ui-capture stream -- bus-based menu scraping (the 4th capture stream).

run(ctx) wraps ui_component_recorder.watch(), enumerating each opened panel into ui_components.jsonl.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "bus"))
sys.path.insert(0, _HERE)

import ui_component_recorder as uic     # noqa: E402
from bus import Bus                      # noqa: E402


def run(ctx, bus=None):
    """Watch for panel opens and scrape them onto ui_components.jsonl until ctx.is_running() flips."""
    def emit(row):
        ctx.emit({"t": ctx.now(), **row})
    try:
        uic.watch(bus or Bus(), emit, is_running=ctx.is_running)
    except Exception as e:
        ctx.on_error("ui-capture", e)
