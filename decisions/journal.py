r"""journal.py -- the ADVISOR/LAUNCHER side of the recorder contract. Pure file IO + read-only
sqlite. It imports no bus, no launcher and no game code, and that is the point.

THE HARD BOUNDARY
    Only the RECORDER reads the game. The advisor asks for data and gets DB records back; it builds
    its features from those records. If the advisor could open a bus, the recorder would be
    pointless and training data would stop matching what the model saw at decision time.

Who calls what:
    ADVISOR   request_snapshot(run)          -> recorder reads the game NOW, persists the decision
                                                point, returns (decision_id, records)   [stream 2]
    ADVISOR   log_pick(run, did, pick, ...)  -> the action it chose                      [stream 3]
    LAUNCHER  log_verification(run, did, r)  -> did the action actually happen           [stream 4]
    ADVISOR   request_target(run)            -> recorder reads + persists the reward row [stream 1]

Requests go to <run>/decisions_requests.jsonl, replies come back on
<run>/decisions_responses.jsonl, and the recorder's `decisions` stream is the only process that
touches decisions.sqlite. A crash therefore never corrupts the DB, and the request log is itself a
replayable raw record.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

REQUESTS = "decisions_requests.jsonl"
RESPONSES = "decisions_responses.jsonl"
DB_NAME = "decisions.sqlite"
RUNS_ROOT = "D:/twdata/runs/human"
CURRENT_POINTER = "CURRENT_RUN"


def current_run_dir(runs_root=RUNS_ROOT, timeout=0.0):
    """The run dir the recorder is servicing RIGHT NOW, from the pointer the manager publishes.

    Never cache this across a campaign change: the recorder opens a fresh run dir on every campaign
    swap, and a stale path means posting requests into a directory nobody is reading (which fails as
    a request timeout, indistinguishable at a glance from a dead game).
    """
    path = os.path.join(runs_root, CURRENT_POINTER)
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            with open(path, encoding="utf-8") as f:
                d = f.read().strip()
            if d and os.path.isdir(d):
                return d
        except OSError:
            pass
        if time.time() >= deadline:
            raise RuntimeError("no live run dir at %s -- is the manager running?" % path)
        time.sleep(0.5)


# ------------------------------------------------------------------------------- plumbing
def _append(run_dir, name, row):
    row.setdefault("ts", time.time())
    with open(os.path.join(run_dir, name), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _read_rows(path, offset=0):
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


def read_requests(run_dir, offset=0):
    """(rows, new_offset) -- used by the recorder stream to tail the request log."""
    return _read_rows(os.path.join(run_dir, REQUESTS), offset)


def respond(run_dir, req_id, **payload):
    """Recorder -> advisor reply for one request, stamped with the dir it is actually servicing."""
    _append(run_dir, RESPONSES, dict(payload, req_id=req_id,
                                     served_by=str(run_dir).replace("\\", "/")))


_req_seq = [0]


def _new_id(kind):
    _req_seq[0] += 1
    return "%s-%d-%d" % (kind, int(time.time() * 1000), _req_seq[0])


def _await(run_dir, req_id, timeout, poll=0.25):
    """Block until the recorder answers `req_id`. Loud on timeout -- a silent stall here would look
    exactly like "the faction has no options", which is the one thing we must never confuse."""
    path = os.path.join(run_dir, RESPONSES)
    offset, deadline = 0, time.time() + timeout
    while time.time() < deadline:
        rows, offset = _read_rows(path, offset)
        for r in rows:
            if r.get("req_id") == req_id:
                if r.get("error"):
                    raise RuntimeError("recorder failed request %s: %s" % (req_id, r["error"]))
                return r
        time.sleep(poll)
    # A bare timeout is the WRONG story to tell here: by far the most common cause is that the
    # recorder rotated to a new run dir on a campaign swap and this caller is still posting into the
    # abandoned one. Say which dir is live so the failure names its own fix.
    hint = ""
    try:
        live = current_run_dir()
        if os.path.abspath(live) != os.path.abspath(run_dir):
            hint = (" -- THE RECORDER HAS MOVED: it is now servicing %s, not %s. The campaign "
                    "almost certainly changed; re-resolve with journal.current_run_dir()."
                    % (live, run_dir))
    except RuntimeError:
        hint = " -- no live run dir is published either; the manager looks dead."
    raise RuntimeError("recorder never answered request %s within %ss%s" % (req_id, timeout, hint))


# ------------------------------------------------------------- STREAM 2: ask the recorder to look
def request_snapshot(run_dir, active=None, timeout=180.0):
    """Ask the recorder to read the game RIGHT NOW and persist one faction-wide decision point.

    Returns (decision_id, records) where records is what the advisor featurizes:
        {campaign, world, entities: [{context_kind, context_id, state, offers}]}
    read back out of the DB -- so the advisor is provably featurizing the stored record, not a
    live game read.
    """
    rid = _new_id("snapshot")
    t_request = time.time()
    _append(run_dir, REQUESTS, {"kind": "snapshot", "req_id": rid, "active": active})
    reply = _await(run_dir, rid, timeout)
    did = reply.get("decision_id")
    rec = read_decision(run_dir, did)
    # phase stamps the advisor cannot get any other way: how long the RECORDER spent reading the
    # game (collect_ms) vs how long the whole round trip took (the queue wait is the difference).
    rec["_t_request"] = t_request
    rec["_t_received"] = time.time()
    rec["_collect_ms"] = reply.get("collect_ms")
    return did, rec


def request_target(run_dir, timeout=120.0):
    """STREAM 1: ask the recorder to read + persist this turn's reward row. Returns the row."""
    rid = _new_id("target")
    _append(run_dir, REQUESTS, {"kind": "target", "req_id": rid})
    return _await(run_dir, rid, timeout).get("row")


def request_turn(run_dir, timeout=60.0):
    """The current turn number, straight from the recorder (the advisor has no other way to know)."""
    rid = _new_id("turn")
    _append(run_dir, REQUESTS, {"kind": "turn", "req_id": rid})
    return _await(run_dir, rid, timeout).get("turn")


def request_hash(run_dir, timeout=45.0):
    """The liveness digest (see collect.state_hash) -- the watchdog's only input. Raises if the
    recorder cannot answer, which the watchdog counts as "blocked", same as an unchanging hash."""
    rid = _new_id("hash")
    _append(run_dir, REQUESTS, {"kind": "hash", "req_id": rid})
    r = _await(run_dir, rid, timeout)
    return r.get("hash"), r.get("roots") or []


# ------------------------------------------------------------------- STREAMS 3 + 4: what happened
def log_pick(run_dir, decision_id, pick, scores=None, timings=None):
    """STREAM 3 (ADVISOR): the offer it chose out of the decision point, plus the scores it gave
    every offer, so the ranking that produced the choice is stored next to the choice.
    `timings` carries the millisecond phase breakdown for this action (see loop.py)."""
    _append(run_dir, REQUESTS, {"kind": "pick", "decision_id": decision_id,
                                "pick": pick, "scores": scores, "timings": timings})


def log_verification(run_dir, decision_id, result):
    """STREAM 4 (LAUNCHER): the ActionRecord proving whether the action actually happened
    (executed / confirmed / counted + signal + before/after evidence)."""
    _append(run_dir, REQUESTS, {"kind": "verification", "decision_id": decision_id, "result": result})


# --------------------------------------------------------------------- read-only DB access
def _con(run_dir):
    path = os.path.join(run_dir, DB_NAME)
    if not os.path.exists(path):
        raise RuntimeError("no decisions.sqlite in %s -- the recorder has not opened it yet" % run_dir)
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=15.0)
    con.row_factory = sqlite3.Row
    return con


def read_decision(run_dir, decision_id):
    """The stored decision point, in the shape the advisor featurizes."""
    con = _con(run_dir)
    try:
        dp = con.execute("SELECT * FROM decision_points WHERE decision_id=?",
                         (decision_id,)).fetchone()
        if dp is None:
            raise RuntimeError("decision %s not in the store" % decision_id)
        ents, by_snap = [], {}
        for r in con.execute("SELECT * FROM entity_snapshots WHERE decision_id=?", (decision_id,)):
            e = {"snapshot_id": r["snapshot_id"], "context_kind": r["context_kind"],
                 "context_id": r["context_id"], "state": json.loads(r["features"]), "offers": []}
            by_snap[r["snapshot_id"]] = e
            ents.append(e)
        for r in con.execute("SELECT * FROM action_offers WHERE decision_id=?", (decision_id,)):
            e = by_snap.get(r["snapshot_id"])
            if e is not None:
                e["offers"].append({"offer_id": r["offer_id"], "action_type": r["action_type"],
                                    "key": r["action_key"], "available": bool(r["available"]),
                                    "gate": r["gate"], "params": json.loads(r["params"] or "{}")})
        return {"decision_id": decision_id, "turn": dp["turn"], "campaign_id": dp["campaign_id"],
                "campaign": json.loads(dp["campaign"] or "{}"),
                "world": json.loads(dp["world"] or "{}"), "entities": ents}
    finally:
        con.close()


def labelled_decisions(run_dir):
    """(decisions, target_series) for this run -- every CONFIRMED decision point with its label,
    plus the per-turn reward inputs. The trainer's only input (see store.labelled_decisions)."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from store import DecisionStore                                    # noqa: E402
    s = DecisionStore(run_dir)
    try:
        return s.labelled_decisions(), s.target_series()
    finally:
        s.close()
