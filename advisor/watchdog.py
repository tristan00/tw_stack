from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

STUCK_SECONDS = 75.0
POLL_SECONDS = 15.0
TURN_CAP_SECONDS = 80.0


class Watchdog:
    def __init__(self, request_hash, on_stuck, stuck_seconds=STUCK_SECONDS,
                 poll_seconds=POLL_SECONDS, turn_cap_seconds=TURN_CAP_SECONDS, log=None):
        self._request = request_hash
        self._on_stuck = on_stuck
        self.stuck_seconds = stuck_seconds
        self.poll_seconds = poll_seconds
        self.turn_cap_seconds = turn_cap_seconds
        self._log = log or (lambda m: sys.stderr.write("watchdog: %s\n" % m))
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._last_change = time.time()
        self._last_hash = None
        self._last_roots = []
        self._turn_t0 = None
        self._turn_no = None
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
            t0 = time.time()
            self._thread.join(timeout=self.poll_seconds + 5.0)
            common.waitlog("watchdog_join", time.time() - t0,
                           not self._thread.is_alive())

    def beat(self, why="action"):
        with self._lock:
            self._last_change = time.time()
        return why

    def begin_turn(self, turn=None):
        with self._lock:
            self._turn_t0 = time.time()
            self._turn_no = turn

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
        t0 = time.time()
        self._log("poll loop starting: every %.0fs, stuck after %.0fs, turn cap %.0fs"
                  % (self.poll_seconds, self.stuck_seconds, self.turn_cap_seconds))
        last_error = None
        while not self._stop.is_set():
            reason = detail = None
            with self._lock:
                turn_t0, turn_no = self._turn_t0, self._turn_no
            turn_s = (time.time() - turn_t0) if turn_t0 is not None else None
            idle_now = self.idle_seconds()
            if turn_s is not None and turn_s >= self.turn_cap_seconds:
                reason = "turn_time_cap"
                detail = {"turn": turn_no, "turn_s": round(turn_s, 1),
                          "cap_s": self.turn_cap_seconds, "roots": self._last_roots}
            elif idle_now >= self.stuck_seconds and (self.checks or self.errors):
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
                self._log("STUCK (%s) after %.0fs -- firing handler"
                          % (reason, detail.get("turn_s") or detail.get("idle_s") or 0))
                try:
                    self._on_stuck(reason, detail)
                except Exception as e:
                    self._log("on_stuck handler raised: %s" % repr(e)[:160])
                return
            waits = [self.poll_seconds, self.stuck_seconds - self.idle_seconds()]
            if turn_t0 is not None:
                waits.append(self.turn_cap_seconds - (time.time() - turn_t0))
            self._stop.wait(max(1.0, min(waits)))
        common.waitlog("watchdog_poll", time.time() - t0, True, "stopped")
