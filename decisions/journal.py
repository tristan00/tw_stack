r"""journal.py -- the CALLABLE write API for the advisor and the launcher.

The recorder (manager process) is the SOLE writer of decisions.sqlite. The advisor and the
launcher live in other processes, so they do not touch the DB: they APPEND one JSON line per
event to <run_dir>/decisions_journal.jsonl, and the recorder's `decisions` stream tails that file
and persists it. Same request/journal pattern already proven for the v6 actions stream, and it
means a crash never corrupts the DB -- the journal is itself a replayable raw record.

Who calls what (per the run architecture):
  ADVISOR  log_decision(...)   after each decision point: features + offers + the chosen action
  LAUNCHER log_verification(...) after each execution: did the action actually happen
  ADVISOR  log_target(...)     once at end of turn: the reward row

log_decision + log_verification may be a single call when the advisor already holds the
launcher's ActionRecord (the common case): pass `result=` to log_decision and skip the separate
verification event.
"""
from __future__ import annotations

import json
import os
import time

JOURNAL = "decisions_journal.jsonl"


def _append(run_dir, row):
    row.setdefault("ts", time.time())
    path = os.path.join(run_dir, JOURNAL)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return path


# ------------------------------------------------------------------ STREAM 1: reward
def log_target(run_dir, target_row):
    """End of turn: the reward inputs (campaign_id, turn, income, settlements, allies, vassals,
    power_rank). Exactly-once is enforced downstream by the store's primary key."""
    return _append(run_dir, {"kind": "target", "row": target_row})


# ------------------------------------------- STREAMS 2+3 (+4 when the result is already known)
def log_decision(run_dir, features, offers, pick=None, result=None,
                 decision_seq=0, policy=None, decision_id=None):
    """One decision point.

    features  the context snapshot the model saw (campaign block + context block)
    offers    EVERY action offered at that instant, available or not (with gate reason)
    pick      the action chosen ({action_type, key, params}); noop is a real choice
    result    the launcher ActionRecord if already available (then no separate verification event)
    decision_id  optional caller-supplied id so a later verification event can be matched to
                 this decision; defaults to a timestamp-based id and is returned.
    """
    did = decision_id or "d%.6f" % time.time()
    _append(run_dir, {"kind": "decision", "decision_id": did, "features": features,
                      "offers": offers, "pick": pick, "result": result,
                      "decision_seq": decision_seq, "policy": policy})
    return did


# ------------------------------------------------------------------ STREAM 4: verification
def log_verification(run_dir, decision_id, result):
    """Called by the LAUNCHER after execution: the ActionRecord proving whether the action
    actually happened (executed / confirmed / counted + signal + before/after evidence)."""
    return _append(run_dir, {"kind": "verification", "decision_id": decision_id, "result": result})


def read_journal(run_dir, offset=0):
    """(rows, new_offset) -- used by the recorder stream to tail the journal."""
    path = os.path.join(run_dir, JOURNAL)
    if not os.path.exists(path):
        return [], offset
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"kind": "malformed", "raw": line[:200]})
        offset = f.tell()
    return rows, offset
