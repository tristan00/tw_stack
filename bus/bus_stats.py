"""bus_stats -- low-overhead call-measurement layer for the WH3 command-bus client.

Report:  `python bus_stats.py`  -> totals + the JUNK list (keys with calls>=5 and hits==0).
"""

from __future__ import annotations

import atexit
import os
import sqlite3
import sys
import threading
import time
from collections import deque

DEFAULT_DB_PATH = "D:/totalwar_runner/data/bus_stats.sqlite"
KEY_MAXLEN = 400
FLUSH_EVERY_N = 200
FLUSH_EVERY_S = 10.0

VALID_OUTCOMES = ("hit", "empty", "timeout", "error")

GUARD_CHANNELS = ("find", "tree")
GUARD_THRESHOLD = 3
GUARD_REPROBE_EVERY = 25
GUARD_STRESS_WINDOW = 40
GUARD_STRESS_TIMEOUT_FRAC = 0.5
GUARD_STRESS_MIN_SAMPLES = 10
GUARD_STRESS_THRESHOLD = 2


def _env_enabled() -> bool:
    """True unless BUS_STATS is explicitly set to a falsey value (0/false/no/off)."""
    v = os.environ.get("BUS_STATS")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def _env_guard_enabled() -> bool:
    """True unless BUS_GUARD is explicitly set to a falsey value (0/false/no/off)."""
    v = os.environ.get("BUS_GUARD")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def _env_db_path() -> str:
    """Resolved DB path (BUS_STATS_DB override, else DEFAULT_DB_PATH)."""
    return os.environ.get("BUS_STATS_DB") or DEFAULT_DB_PATH


def classify_outcome(channel: str, reply) -> str:
    """Classify an arrived reply dict as 'hit' (something came back) or 'empty'; 'error' if the shape is unexpected."""
    try:
        if not isinstance(reply, dict):
            return "empty"
        result = reply.get("result")
        child_ids = reply.get("child_ids")
        if isinstance(result, dict) and "found" in result:
            if result.get("found"):
                return "hit"
            return "hit" if child_ids else "empty"
        if isinstance(reply.get("found"), bool):
            if reply.get("found"):
                return "hit"
            return "hit" if child_ids else "empty"
        if child_ids:
            return "hit"
        if result is not None:
            if isinstance(result, (str, list, dict, tuple)) and len(result) == 0:
                return "empty"
            return "hit"
        return "empty"
    except Exception as e:
        sys.stderr.write("bus_stats: classify_outcome failed -> %s\n" % repr(e)[:120])
        return "error"


def make_key(channel: str, payload: str) -> str:
    """The aggregation key: the payload string (for find/tree this IS the path), truncated."""
    try:
        s = "" if payload is None else str(payload)
    except Exception:
        s = "<unstr>"
    if len(s) > KEY_MAXLEN:
        s = s[:KEY_MAXLEN] + "..."
    return s


_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_stats (
    channel  TEXT NOT NULL,
    key      TEXT NOT NULL,
    calls    INTEGER NOT NULL DEFAULT 0,
    hits     INTEGER NOT NULL DEFAULT 0,
    empties  INTEGER NOT NULL DEFAULT 0,
    timeouts INTEGER NOT NULL DEFAULT 0,
    errors   INTEGER NOT NULL DEFAULT 0,
    total_ms REAL    NOT NULL DEFAULT 0,
    last_ts  REAL,
    PRIMARY KEY (channel, key)
);
"""

_UPSERT = """
INSERT INTO call_stats (channel, key, calls, hits, empties, timeouts, errors, total_ms, last_ts)
VALUES (:channel, :key, :calls, :hits, :empties, :timeouts, :errors, :total_ms, :last_ts)
ON CONFLICT(channel, key) DO UPDATE SET
    calls    = calls    + excluded.calls,
    hits     = hits     + excluded.hits,
    empties  = empties  + excluded.empties,
    timeouts = timeouts + excluded.timeouts,
    errors   = errors   + excluded.errors,
    total_ms = total_ms + excluded.total_ms,
    last_ts  = excluded.last_ts;
"""


class StatsTracker:
    """In-memory per-(channel,key) accumulator with batched, accumulate-on-conflict sqlite flush."""

    def __init__(self, db_path: str, flush_every_n: int = FLUSH_EVERY_N,
                 flush_every_s: float = FLUSH_EVERY_S, register_atexit: bool = True) -> None:
        self.db_path = db_path
        self.flush_every_n = max(1, int(flush_every_n))
        self.flush_every_s = float(flush_every_s)
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], list] = {}
        self._calls_since_flush = 0
        self._last_flush = time.time()
        if register_atexit:
            atexit.register(self.close)

    def record(self, channel: str, key: str, outcome: str, elapsed_ms: float) -> None:
        """Accumulate one call in memory; trigger a batched flush when due. Never raises."""
        try:
            if outcome not in VALID_OUTCOMES:
                outcome = "error"
            k = (channel, key)
            with self._lock:
                row = self._pending.get(k)
                if row is None:
                    row = [0, 0, 0, 0, 0, 0.0, 0.0]
                    self._pending[k] = row
                row[0] += 1
                row[1] += 1 if outcome == "hit" else 0
                row[2] += 1 if outcome == "empty" else 0
                row[3] += 1 if outcome == "timeout" else 0
                row[4] += 1 if outcome == "error" else 0
                row[5] += float(elapsed_ms)
                row[6] = time.time()
                self._calls_since_flush += 1
                due = (self._calls_since_flush >= self.flush_every_n
                       or (time.time() - self._last_flush) >= self.flush_every_s)
            if due:
                self.flush()
        except Exception as e:
            sys.stderr.write("bus_stats: record failed -> %s\n" % repr(e)[:120])

    def flush(self) -> None:
        """Write pending deltas to the sqlite DB (accumulate-on-conflict). Never raises."""
        try:
            with self._lock:
                if not self._pending:
                    self._calls_since_flush = 0
                    self._last_flush = time.time()
                    return
                snapshot = self._pending
                self._pending = {}
                self._calls_since_flush = 0
                self._last_flush = time.time()
        except Exception as e:
            sys.stderr.write("bus_stats: flush snapshot failed -> %s\n" % repr(e)[:120])
            return
        try:
            self._write(snapshot)
        except Exception as e:
            sys.stderr.write("bus_stats: flush write failed (%d keys dropped) -> %s\n"
                             % (len(snapshot), repr(e)[:100]))

    def _write(self, snapshot: dict) -> None:
        """Open a short-lived WAL connection and upsert-accumulate every pending delta."""
        d = os.path.dirname(self.db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute(_SCHEMA)
            rows = [{
                "channel": ch, "key": ky,
                "calls": v[0], "hits": v[1], "empties": v[2],
                "timeouts": v[3], "errors": v[4], "total_ms": v[5], "last_ts": v[6],
            } for (ch, ky), v in snapshot.items()]
            conn.executemany(_UPSERT, rows)
            conn.commit()
        finally:
            conn.close()

    def close(self) -> None:
        """Final flush (atexit)."""
        self.flush()


def synthetic_miss(channel: str, payload: str) -> dict:
    """Synthetic MISS reply for a short-circuited find/tree; must stay shaped like the mod's real not-found reply (twcontrol.lua handlers.find / handlers.tree)."""
    if channel == "tree":
        return {"seq": -1, "cmd": "tree", "path": payload, "count": 0,
                "truncated": False, "found": False, "nodes": [], "short_circuited": True}
    return {"seq": -1, "cmd": "find", "path": payload, "result": {"found": False},
            "child_ids": [], "child_contexts": [], "short_circuited": True}


class SuppressionGuard:
    """Session-scoped, thread-safe dead-key detector: a (channel,key) called >= threshold times with zero hits is suppressed, with a periodic re-probe."""

    def __init__(self, threshold: int = GUARD_THRESHOLD, reprobe_every: int = GUARD_REPROBE_EVERY,
                 stress_window: int = GUARD_STRESS_WINDOW,
                 stress_frac: float = GUARD_STRESS_TIMEOUT_FRAC,
                 stress_min_samples: int = GUARD_STRESS_MIN_SAMPLES,
                 stress_threshold: int = GUARD_STRESS_THRESHOLD) -> None:
        self.threshold = max(1, int(threshold))
        self.reprobe_every = max(1, int(reprobe_every))
        self.stress_frac = float(stress_frac)
        self.stress_min_samples = max(1, int(stress_min_samples))
        self.stress_threshold = max(1, int(stress_threshold))
        self._lock = threading.Lock()
        self._state: dict[tuple[str, str], list] = {}
        self._recent = deque(maxlen=max(1, int(stress_window)))

    def note(self, channel: str, key: str, outcome: str) -> None:
        """Record the outcome of a REAL (non-short-circuited) find/tree round-trip. Never raises."""
        if channel not in GUARD_CHANNELS:
            return
        with self._lock:
            st = self._state.get((channel, key))
            if st is None:
                st = [0, 0, 0]
                self._state[(channel, key)] = st
            st[0] += 1
            if outcome == "hit":
                st[1] += 1
            self._recent.append(1 if outcome == "timeout" else 0)

    def _effective_threshold_locked(self) -> int:
        """Dead-key threshold, lowered while the bus is in a timeout storm. Caller holds the lock."""
        n = len(self._recent)
        if n >= self.stress_min_samples and (sum(self._recent) / n) >= self.stress_frac:
            return min(self.threshold, self.stress_threshold)
        return self.threshold

    def should_short_circuit(self, channel: str, key: str) -> bool:
        """True => skip the real send and return a synthetic miss; False => do a real round-trip."""
        if channel not in GUARD_CHANNELS:
            return False
        with self._lock:
            st = self._state.get((channel, key))
            if st is None:
                return False
            calls, hits, sup = st
            if hits > 0 or calls < self._effective_threshold_locked():
                return False
            sup += 1
            st[2] = sup
            if sup % self.reprobe_every == 0:
                return False
            return True

    def is_stressed(self) -> bool:
        """Whether the rolling window currently reads as a timeout storm (exposed for testing)."""
        with self._lock:
            return self._effective_threshold_locked() < self.threshold

    def reset(self) -> None:
        """Drop ALL dead-key state and the stress window."""
        with self._lock:
            self._state.clear()
            self._recent.clear()


_tracker: StatsTracker | None = None
_tracker_lock = threading.Lock()
_disabled_logged = False
_guard: SuppressionGuard | None = None
_guard_lock = threading.Lock()


def enabled() -> bool:
    """Cheap gate check for the hot path -- honours env BUS_STATS."""
    return _env_enabled()


def guard_enabled() -> bool:
    """Cheap gate check for the active junk-find short-circuit -- honours env BUS_GUARD."""
    return _env_guard_enabled()


def active() -> bool:
    """True if the send-path wrapper must engage at all (measurement OR the barrier)."""
    return _env_enabled() or _env_guard_enabled()


def get_tracker() -> StatsTracker | None:
    """Return the process-wide tracker (lazily built from env), or None if disabled."""
    global _tracker, _disabled_logged
    if not _env_enabled():
        return None
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                try:
                    _tracker = StatsTracker(_env_db_path())
                except Exception as e:
                    if not _disabled_logged:
                        sys.stderr.write("bus_stats: tracker init failed (stats off) -> %s\n"
                                         % repr(e)[:120])
                        _disabled_logged = True
                    return None
    return _tracker


def record(channel: str, key: str, outcome: str, elapsed_ms: float) -> None:
    """Route one recorded call to the singleton tracker. Never raises."""
    try:
        t = get_tracker()
        if t is not None:
            t.record(channel, key, outcome, elapsed_ms)
    except Exception as e:
        sys.stderr.write("bus_stats: record dispatch failed -> %s\n" % repr(e)[:120])


def install_tracker(tracker: StatsTracker | None) -> None:
    """Test/tooling hook: force a specific tracker as the process singleton."""
    global _tracker
    with _tracker_lock:
        _tracker = tracker


def reset_tracker() -> None:
    """Test hook: drop the singleton so the next get_tracker() rebuilds from env, and clear suppression."""
    install_tracker(None)
    reset_suppression()


def get_guard() -> SuppressionGuard | None:
    """Return the process-wide suppression guard (lazily built), or None if BUS_GUARD is off."""
    global _guard
    if not _env_guard_enabled():
        return None
    if _guard is None:
        with _guard_lock:
            if _guard is None:
                try:
                    _guard = SuppressionGuard()
                except Exception as e:
                    sys.stderr.write("bus_stats: guard init failed (barrier off) -> %s\n"
                                     % repr(e)[:120])
                    return None
    return _guard


def install_guard(guard: SuppressionGuard | None) -> None:
    """Test/tooling hook: force a specific guard as the process singleton."""
    global _guard
    with _guard_lock:
        _guard = guard


def note_outcome(channel: str, key: str, outcome: str) -> None:
    """Feed one REAL round-trip outcome to the guard so it can learn dead keys. Never raises."""
    try:
        g = get_guard()
        if g is not None:
            g.note(channel, key, outcome)
    except Exception as e:
        sys.stderr.write("bus_stats: note_outcome failed -> %s\n" % repr(e)[:120])


def short_circuit_reply(channel: str, payload: str, key: str) -> dict | None:
    """If this find/tree key is dead, return a synthetic MISS reply (skipping the mod); else None. Never raises."""
    try:
        g = get_guard()
        if g is None:
            return None
        if not g.should_short_circuit(channel, key):
            return None
        return synthetic_miss(channel, payload)
    except Exception as e:
        sys.stderr.write("bus_stats: short_circuit_reply failed (falling back to real send) -> %s\n"
                         % repr(e)[:120])
        return None


def reset_suppression() -> None:
    """Clear ALL session-scoped dead-key suppression; call on every bus/session/campaign reset."""
    try:
        g = _guard
        if g is not None:
            g.reset()
    except Exception as e:
        sys.stderr.write("bus_stats: reset_suppression failed -> %s\n" % repr(e)[:120])


def load_rows(db_path: str | None = None) -> list[dict]:
    """Read every call_stats row as a dict. Returns [] if the DB is missing/unreadable."""
    path = db_path or _env_db_path()
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT channel, key, calls, hits, empties, timeouts, errors, total_ms, last_ts "
                "FROM call_stats")
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        sys.stderr.write("bus_stats: load_rows failed -> %s\n" % repr(e)[:120])
        return []


def build_report(db_path: str | None = None, junk_min_calls: int = 5) -> dict:
    """Compute totals + the JUNK list (calls>=junk_min_calls and hits==0), sorted by calls desc."""
    rows = load_rows(db_path)
    tot = {"calls": 0, "hits": 0, "empties": 0, "timeouts": 0, "errors": 0, "total_ms": 0.0}
    for r in rows:
        for k in tot:
            tot[k] += r.get(k, 0) or 0
    junk = [r for r in rows if (r.get("calls", 0) or 0) >= junk_min_calls
            and (r.get("hits", 0) or 0) == 0]
    junk.sort(key=lambda r: (r.get("calls", 0) or 0), reverse=True)
    junk_wasted_ms = sum((r.get("total_ms", 0.0) or 0.0) for r in junk)
    return {"rows": rows, "totals": tot, "junk": junk,
            "junk_wasted_ms": junk_wasted_ms, "junk_min_calls": junk_min_calls}


def _pct(n: int, d: int) -> str:
    return "%5.1f%%" % (100.0 * n / d) if d else "  0.0%"


def format_report(rep: dict, top: int = 40) -> str:
    """Render a build_report() dict as the human-readable text the CLI prints."""
    t = rep["totals"]
    calls = t["calls"]
    out = []
    out.append("=" * 78)
    out.append("BUS CALL STATS")
    out.append("=" * 78)
    out.append("total calls : %d  (across %d distinct channel/key pairs)"
               % (calls, len(rep["rows"])))
    out.append("  hit       : %8d  %s" % (t["hits"], _pct(t["hits"], calls)))
    out.append("  empty     : %8d  %s   <- reply arrived but returned nothing"
               % (t["empties"], _pct(t["empties"], calls)))
    out.append("  timeout   : %8d  %s" % (t["timeouts"], _pct(t["timeouts"], calls)))
    out.append("  error     : %8d  %s" % (t["errors"], _pct(t["errors"], calls)))
    out.append("  total time: %.1f s" % (t["total_ms"] / 1000.0))
    out.append("")
    junk = rep["junk"]
    out.append("-" * 78)
    out.append("JUNK CALLS  (calls >= %d and hits == 0 -- NEVER returned anything): %d keys"
               % (rep["junk_min_calls"], len(junk)))
    out.append("total time wasted on junk: %.1f s" % (rep["junk_wasted_ms"] / 1000.0))
    out.append("-" * 78)
    if not junk:
        out.append("(none)")
    else:
        out.append("%-8s %7s %7s %7s %7s %9s  %s"
                   % ("channel", "calls", "empty", "t/out", "error", "wasted_ms", "key"))
        for r in junk[:top]:
            out.append("%-8s %7d %7d %7d %7d %9.0f  %s"
                       % (r["channel"], r["calls"], r["empties"], r["timeouts"],
                          r["errors"], r["total_ms"], r["key"]))
        if len(junk) > top:
            out.append("... and %d more (raise --top to see all)" % (len(junk) - top))
    out.append("=" * 78)
    return "\n".join(out)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Report WH3 bus call stats + junk (never-return) calls.")
    p.add_argument("--db", default=None, help="sqlite path (default: env BUS_STATS_DB or %s)"
                   % DEFAULT_DB_PATH)
    p.add_argument("--min", type=int, default=5, dest="min_calls",
                   help="min calls for the junk list (default 5)")
    p.add_argument("--top", type=int, default=40, help="max junk rows to print (default 40)")
    args = p.parse_args(argv)
    db = args.db or _env_db_path()
    if not os.path.exists(db):
        sys.stderr.write("bus_stats: no DB at %s (has the instrumented bus run yet?)\n" % db)
        return 1
    rep = build_report(db, junk_min_calls=args.min_calls)
    print(format_report(rep, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
