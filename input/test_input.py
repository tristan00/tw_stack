r"""Offline test for the `input` stream (no game, no real system input).

Drives input_stream.run() with FAKE OS probes: the test thread mutates a scripted OS state
(focus / cursor / pressed keys) while the stream polls, and we assert it emits the correct
record.py row shapes -- focus, move, key_down/up, mouse_down/up -- with a monotonic clock and
shot_req raised on the click. Deterministic; nothing touches the real desktop or the game.

    python test_input.py
"""
import sys
import threading
import time

import input_stream


class FakeOS:
    """Scripted OS state the test mutates live while the stream polls it."""

    def __init__(self):
        self.fg = ("editor - notes.py", 111)
        self.pos = (0, 0)
        self.keys = set()

    def cursor(self):
        return self.pos

    def foreground(self):
        return self.fg

    def keystate(self, vk):
        return vk in self.keys


class FakeCtx:
    """Minimal manager context: collects emitted rows, owns the run flag + shot_req."""

    def __init__(self):
        self.rows = []
        self._run = True
        self.shot_req = threading.Event()
        self.errors = []
        self._t0 = time.time()
        self._lock = threading.Lock()

    def emit(self, row):
        with self._lock:
            self.rows.append(row)

    def now(self):
        return round(time.time() - self._t0, 3)

    def is_running(self):
        return self._run

    def stop(self):
        self._run = False

    def on_error(self, where, exc):
        self.errors.append((where, repr(exc)))


def main():
    os_state = FakeOS()
    ctx = FakeCtx()
    probes = input_stream.Probes(cursor=os_state.cursor,
                                 foreground=os_state.foreground,
                                 keystate=os_state.keystate)
    th = threading.Thread(target=input_stream.run, args=(ctx,), kwargs={"probes": probes},
                          daemon=True)
    th.start()

    step = 0.10
    time.sleep(step)
    os_state.pos = (10, 20)
    time.sleep(step)
    os_state.keys = {0x41}
    time.sleep(step)
    os_state.keys = set()
    time.sleep(step)
    os_state.keys = {0x01}
    time.sleep(step)
    os_state.keys = set()
    time.sleep(step)
    os_state.fg = ("Total War: WARHAMMER 3", 222)
    time.sleep(step)
    ctx.stop()
    th.join(timeout=2.0)

    rows = ctx.rows
    kinds = [r["kind"] for r in rows]
    fails = []

    for need in ("focus", "move", "key_down", "key_up", "mouse_down", "mouse_up"):
        if need not in kinds:
            fails.append("missing kind: %s" % need)

    if not ctx.shot_req.is_set():
        fails.append("mouse_down did not set shot_req")

    keydowns = [r for r in rows if r["kind"] == "key_down"]
    if not any(r.get("key") == "A" for r in keydowns):
        fails.append("no key_down for 'A' (got %s)" % [r.get("key") for r in keydowns])
    focuses = [r.get("window") for r in rows if r["kind"] == "focus"]
    if "Total War: WARHAMMER 3" not in focuses:
        fails.append("focus change to the game window not captured (got %s)" % focuses)
    mdowns = [r for r in rows if r["kind"] == "mouse_down"]
    if not (mdowns and mdowns[0].get("button") == "L" and "screen" in mdowns[0]):
        fails.append("mouse_down row malformed: %s" % (mdowns[:1]))

    ts = [r["t"] for r in rows]
    if any(ts[i] < ts[i - 1] for i in range(1, len(ts))):
        fails.append("t not monotonic non-decreasing")

    if ctx.errors:
        fails.append("stream caught errors: %s" % ctx.errors[:3])

    print("emitted %d rows; kinds present: %s" % (len(rows), sorted(set(kinds))))
    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("PASS: input stream emits focus/move/key/mouse rows with a monotonic clock and "
          "raises shot_req on click -- all offline, deterministic.")


if __name__ == "__main__":
    main()
