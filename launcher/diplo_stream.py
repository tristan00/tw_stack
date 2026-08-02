r"""diplo_stream.py -- the diplomacy ANALYSIS dataset stream, independent of policy data.

One JSONL row per diplomacy event / per-turn pair checkpoint, appended to
<run_dir>/diplomacy.jsonl by the session process (single writer per file; sqlite stays the
recorder's). Rows join to interrupt records and screen dumps by timestamp, and to turns by
the TURN stamp the loop maintains. A stream failure must never kill a campaign: emit paths
swallow OS errors loudly instead of raising.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

TURN = [None]                 # the loop stamps the current campaign turn here
_STATE = {"run_dir": None, "tracked": set(), "warned": False}

# faction keys as they appear in cco context ids -- machine ids, not display names
_FACTION_KEY_RE = re.compile(r"\b(wh\d?(?:_dlc\d+|_main|_pro\d+|_twa\d+|_cp\d+)_[a-z0-9_]+)\b")
# same key shape, different record families: units/buildings/tech/skills/regions share the
# prefix pattern and would fill TRACK_CAP with ids cm:get_faction nils on
_NON_FACTION_TOKENS = ("_region_", "_inf_", "_cav_", "_mon_", "_art_", "_veh_", "_bld_",
                       "_building_", "_tech_", "_skill_", "_trait_", "_ancillary_",
                       "_banner_", "_edict_", "_rite_", "_unit_")


def _keyish_ok(k):
    return k and not any(t in k for t in _NON_FACTION_TOKENS)


def reset(run_dir):
    """New campaign: point the stream at its run dir and clear the tracked-pair set."""
    _STATE.update(run_dir=run_dir, warned=False, cap_warned=False)
    _STATE["tracked"] = set()
    TURN[0] = None


TRACK_CAP = 12


def track(faction_key):
    """Register a faction for per-turn pair checkpoints. Deal parties only, never a sweep;
    capped so a diplomacy-heavy campaign cannot grow the per-turn checkpoint cost unbounded."""
    k = str(faction_key or "").strip()
    if not _keyish_ok(k):
        return
    if k not in _STATE["tracked"] and len(_STATE["tracked"]) >= TRACK_CAP:
        if not _STATE.get("cap_warned"):
            _STATE["cap_warned"] = True
            sys.stderr.write("diplo_stream: TRACK_CAP=%d reached -- further factions not "
                             "checkpointed (deal rows still emitted)\n" % TRACK_CAP)
        return
    _STATE["tracked"].add(k)


def tracked():
    return sorted(_STATE["tracked"])


def emit(kind, **fields):
    """Append one row; returns it, or None when dropped (no run dir / OS error)."""
    rd = _STATE["run_dir"]
    if not rd:
        if not _STATE["warned"]:
            _STATE["warned"] = True
            sys.stderr.write("diplo_stream: no run dir set -- rows are being DROPPED\n")
        return None
    row = dict(fields, kind=kind, turn=TURN[0], ts=time.time())
    try:
        with open(os.path.join(rd, "diplomacy.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return row
    except OSError as e:
        sys.stderr.write("diplo_stream: append failed -> %s\n" % repr(e)[:90])
        return None


def faction_keys_in(nodes):
    """Faction keys found in node CONTEXT ids of a screen tree, in first-seen order."""
    out = []
    for n in nodes or []:
        ctx = str(n.get("context") or "")
        for m in _FACTION_KEY_RE.findall(ctx):
            if _keyish_ok(m) and m not in out:
                out.append(m)
    return out


def pair_relations(bus, faction_key):
    """Player<->faction war/ally/trade/vassal flags + standing (the proven cm eval);
    None when unreadable."""
    try:
        from diplomacy_actions import _treaty
        return _treaty(bus, faction_key)
    except Exception as e:
        sys.stderr.write("diplo_stream: pair_relations(%s) -> %s\n"
                         % (faction_key, repr(e)[:80]))
        return None


def checkpoint(bus):
    """Per-turn treaty/standing checkpoint for every tracked faction. Bounded by design:
    only deal parties are tracked -- a full faction enumeration over the bus has killed a
    campaign before and must never happen here."""
    for key in tracked():
        emit("pair_checkpoint", faction=key, pair=pair_relations(bus, key))
