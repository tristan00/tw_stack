from __future__ import annotations

import sys
import threading
import time

STUCK_SECONDS = 75.0         # no progress this long -> stuck
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
        """Reset the idle timer."""
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

    def _run(self):
        last_error = None
        while not self._stop.is_set():
            reason = detail = None
            # decide on the clock, before issuing a request that may block
            idle_now = self.idle_seconds()
            if idle_now >= self.stuck_seconds and (self.checks or self.errors):
                reason = "blocked" if last_error is not None else "identical_state"
                detail = {"idle_s": round(idle_now, 1), "roots": self._last_roots}
                if last_error is not None:
                    detail["error"] = repr(last_error)[:200]
                else:
                    detail["hash"] = self._last_hash
            else:
                try:
                    h, roots = self._request()
                    self.checks += 1
                    last_error = None
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
                    last_error = e
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
                return                        # one verdict per run
            # never sleep past the deadline
            self._stop.wait(max(1.0, min(self.poll_seconds,
                                         self.stuck_seconds - self.idle_seconds())))
