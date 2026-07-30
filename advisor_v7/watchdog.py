r"""watchdog.py -- the stuck detector. Runs on its own thread so it keeps checking while the main
loop is blocked inside a long action, a battle, or a hung bus call.

WHAT COUNTS AS STUCK
The recorder hands back a digest (collect.state_hash) of things that only move when the game
ACTUALLY PROGRESSES -- turn, treasury, income, every character's position/AP/stance, and the set of
visible UI roots. Nothing that ticks on its own is in it, so a frozen game cannot look alive.

Two failure shapes, one verdict:
    IDENTICAL   the digest has not changed for STUCK_SECONDS -> the game is sitting on something we
                do not handle (an unhandled popup, a modal waiting for a click, a finished battle)
    BLOCKED     the recorder cannot answer at all for STUCK_SECONDS -> the bus is dead, or a modal
                has paused the game tick so the mod never runs

Both mean the run cannot make progress on its own, so both fire on_stuck(reason, detail). The
caller's job (loop.py) is: tell the LAUNCHER to screenshot the screen as evidence, then end this run
so a fresh one can start. The watchdog itself neither screenshots nor kills -- it only decides.

⚠ the timer is reset by `beat()` too: the loop calls it after every CONFIRMED action, because a
confirmed action is proof of progress even if the digest happens to land on the same value.
"""
from __future__ import annotations

import sys
import threading
import time

# 10 minutes: deliberately generous while we are still learning which situations strand a run --
# a long AI inter-turn with several battles can legitimately look quiet for a while, and a false
# abandon destroys the evidence we are trying to collect. Tighten once the failure modes are known.
STUCK_SECONDS = 600.0        # unchanged/unanswered this long -> stuck
POLL_SECONDS = 15.0          # how often the digest is requested


class Watchdog:
    def __init__(self, request_hash, on_stuck, stuck_seconds=STUCK_SECONDS,
                 poll_seconds=POLL_SECONDS, log=None):
        """request_hash() -> (hash, roots), raising if the game cannot answer.
        on_stuck(reason, detail) is called ONCE, from this thread."""
        self._request = request_hash
        self._on_stuck = on_stuck
        self.stuck_seconds = stuck_seconds
        self.poll_seconds = poll_seconds
        self._log = log or (lambda m: sys.stderr.write("watchdog: %s\n" % m))
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._last_change = time.time()
        self._last_hash = None
        self._last_roots = []
        self._fired = False
        self.checks = 0
        self.errors = 0

    # ------------------------------------------------------------------ control
    def start(self):
        self._last_change = time.time()
        self._thread = threading.Thread(target=self._run, name="v7-watchdog", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_seconds + 5.0)

    def beat(self, why="action"):
        """Progress happened for a reason the digest may not show (a confirmed action). Reset."""
        with self._lock:
            self._last_change = time.time()
        return why

    def idle_seconds(self):
        with self._lock:
            return time.time() - self._last_change

    @property
    def fired(self):
        return self._fired

    @property
    def last_roots(self):
        return list(self._last_roots)

    # ------------------------------------------------------------------ the thread
    def _run(self):
        while not self._stop.is_set():
            reason = detail = None
            try:
                h, roots = self._request()
                self.checks += 1
                with self._lock:
                    if h != self._last_hash:
                        self._last_hash, self._last_roots = h, roots
                        self._last_change = time.time()
                    idle = time.time() - self._last_change
                if idle >= self.stuck_seconds:
                    reason = "identical_state"
                    detail = {"idle_s": round(idle, 1), "hash": h, "roots": roots}
            except Exception as e:
                self.errors += 1
                with self._lock:
                    idle = time.time() - self._last_change
                self._log("digest request failed (%.0fs idle): %s" % (idle, repr(e)[:120]))
                if idle >= self.stuck_seconds:
                    reason = "blocked"
                    detail = {"idle_s": round(idle, 1), "error": repr(e)[:200],
                              "roots": self._last_roots}
            if reason and not self._fired:
                self._fired = True
                self._log("STUCK (%s) after %.0fs -- firing handler" % (reason, detail["idle_s"]))
                try:
                    self._on_stuck(reason, detail)
                except Exception as e:
                    self._log("on_stuck handler raised: %s" % repr(e)[:160])
                return                        # one verdict per run; the caller tears the run down
            self._stop.wait(self.poll_seconds)
