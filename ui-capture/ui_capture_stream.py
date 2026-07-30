r"""ui-capture stream -- bus-based menu scraping (the 4th capture stream).

Wraps the proven good-run `ui_component_recorder.watch()`: it tails the newest script_log for
PanelOpenedCampaign and, on each fire, enumerates the opened panel's options over the command bus
into ui_components.jsonl. This is the ONLY capture stream that touches the bus; it self-degrades
(emits `bus_unavailable` + backs off) the instant twcontrol stops answering, so it can never block
the bus-free streams.

`ui_component_recorder.py` here is byte-faithful to the working v5 recorder (which produced 2700 UI
rows in the good run); the ONLY edits are import redirection (twapi.bus/errors -> the bus repo) and
the inlined `_OCC_ORDER` constant, so it carries no twapi cascade.

Contract with the manager -- run(ctx):
    ctx.emit(row)              append one row to ui_components.jsonl (the manager routes it there)
    ctx.now() -> float         seconds since the shared T0
    ctx.is_running() -> bool   True while capture should continue
    ctx.on_error(where, exc)   record a caught (never fatal) exception
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "bus"))   # for `from bus import Bus`
sys.path.insert(0, _HERE)                                          # errors, ui_component_recorder

import ui_component_recorder as uic     # noqa: E402
from bus import Bus                      # noqa: E402


def run(ctx, bus=None):
    """Watch for panel opens and scrape them onto ui_components.jsonl until ctx.is_running() flips.

    Args:
        ctx: The manager context.
        bus: An optional connected Bus (default: a fresh Bus() on the shared command bus).
    """
    def emit(row):
        ctx.emit({"t": ctx.now(), **row})
    try:
        uic.watch(bus or Bus(), emit, is_running=ctx.is_running)
    except Exception as e:
        ctx.on_error("ui-capture", e)
