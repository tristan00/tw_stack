from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TURN = [None]
CAMPAIGN = [None]
_STATE = {"run_dir": None, "tracked": set(), "warned": False}

_FACTION_KEY_RE = re.compile(r"\b(wh\d?(?:_dlc\d+|_main|_pro\d+|_twa\d+|_cp\d+)_[a-z0-9_]+)\b")
_NON_FACTION_TOKENS = ("_region_", "_inf_", "_cav_", "_mon_", "_art_", "_veh_", "_bld_",
                       "_building_", "_tech_", "_skill_", "_trait_", "_ancillary_",
                       "_banner_", "_edict_", "_rite_", "_unit_")


def _keyish_ok(k):
    return k and not any(t in k for t in _NON_FACTION_TOKENS)


def reset(run_dir):
    _STATE.update(run_dir=run_dir, warned=False, cap_warned=False)
    _STATE["tracked"] = set()
    TURN[0] = None
    CAMPAIGN[0] = None


TRACK_CAP = 128


def track(faction_key):
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
    rd = _STATE["run_dir"]
    if not rd:
        if not _STATE["warned"]:
            _STATE["warned"] = True
            sys.stderr.write("diplo_stream: no run dir set -- rows are being DROPPED\n")
        return None
    row = dict(fields, kind=kind, turn=TURN[0], campaign_key=CAMPAIGN[0], ts=time.time())
    try:
        from decisions import journal
        journal.log_diplomacy(rd, row)
        return row
    except Exception as e:
        sys.stderr.write("diplo_stream: emit failed -> %s\n" % repr(e)[:90])
        return None


def faction_keys_in(nodes):
    out = []
    for n in nodes or []:
        ctx = str(n.get("context") or "")
        for m in _FACTION_KEY_RE.findall(ctx):
            if _keyish_ok(m) and m not in out:
                out.append(m)
    return out


def pair_relations(bus, faction_key):
    try:
        from diplomacy_actions import _treaty
        return _treaty(bus, faction_key)
    except Exception as e:
        sys.stderr.write("diplo_stream: pair_relations(%s) -> %s\n"
                         % (faction_key, repr(e)[:80]))
        return None


def checkpoint(bus):
    for key in tracked():
        emit("pair_checkpoint", faction=key, pair=pair_relations(bus, key))
