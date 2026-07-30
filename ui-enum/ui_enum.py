r"""ui-enum -- enumerate a game UI panel into its options with stable ids, over the bus `find` channel.

Given a panel path, ask the bus `find` channel for the panel's direct children, then turn the raw
{child_ids, child_contexts} into a structured option list [{id, key, context}], where `key` is the
bare game key recovered via idmatch (a Cco context's key, or the id with row/card wrappers stripped).
This is the READ capability the recorder's menu-capture uses (and a future executor will).

Depends on the sibling `bus` repo (imported as a library for now; becomes a service call later) and
the local `idmatch`. Pure parsing (`parse_find`) is separated from the bus call (`enumerate_panel`)
so the parsing is testable offline with no game.
"""
from __future__ import annotations

import os
import sys

import idmatch

# sibling bus repo (co-located). Library import for now; a service boundary later.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bus"))


def _context_key(ctx):
    """Bare key from a child context like 'CcoCampaignFaction:wh2_main_hef_nagarythe' (the part after
    ':'); None when there is no context or no ':'."""
    if ctx and ":" in ctx:
        return ctx.split(":", 1)[1]
    return None


def parse_find(result: dict) -> dict:
    """Pure: a bus `find` result -> {panel, found, count, options:[{id, key, context}]}.

    `found` is False when the result carried no child list at all (panel absent / not a container)."""
    ids = result.get("child_ids")
    ctxs = result.get("child_contexts") or []
    options = []
    for i, cid in enumerate(ids or []):
        ctx = ctxs[i] if i < len(ctxs) else None
        key = idmatch.extract_key(_context_key(ctx) or cid or "")
        options.append({"id": cid, "key": key, "context": ctx})
    return {"panel": result.get("path"), "found": ids is not None,
            "count": len(options), "options": options}


def enumerate_panel(panel: str, bus_client=None, timeout: float = 15.0) -> dict:
    """Enumerate a panel's direct children into structured options (sends `find` over the bus)."""
    if bus_client is None:
        import bus
        bus_client = bus.Bus()
    return parse_find(bus_client.send("find", panel, timeout=timeout))


if __name__ == "__main__":
    import json
    panel = sys.argv[1] if len(sys.argv) > 1 else "hud_campaign"
    print(json.dumps(enumerate_panel(panel), indent=1)[:2000])
