from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "bus"))
sys.path.insert(0, _HERE)

import ui_component_recorder as uic
from bus import Bus


def run(ctx, bus=None):
    def emit(row):
        ctx.emit({"t": ctx.now(), **row})
    try:
        uic.watch(bus or Bus(), emit, is_running=ctx.is_running)
    except Exception as e:
        ctx.on_error("ui-capture", e)
