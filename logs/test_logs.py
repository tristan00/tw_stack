r"""Offline test for the `logs` stream (no game): the silent-data-critical capture rule.

Uses SYNTHETIC log files in a temp "game dir" to prove all four behaviours without WH3:
  1. a PRE-EXISTING log -> its history is SKIPPED; only bytes appended after start are tailed.
  2. a NEW log created after start (the game-restart case) -> captured from byte 0.
  3. byte-exactness -> the .tail is exactly the appended bytes, no more, no less.
  4. rotation/truncation -> the offset resets and post-rotation content is re-captured.

This is the exact code path that used to silently gut a restarted run, so it is verified in
full offline.

    python test_logs.py
"""
import os
import sys
import tempfile
import threading
import time

import logs_stream


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.rows = []
        self._run = True
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


def read_bytes(p):
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as f:
        return f.read()


def main():
    tmp = tempfile.mkdtemp(prefix="logs_test_")
    logdir = os.path.join(tmp, "game")
    outdir = os.path.join(tmp, "out")
    os.makedirs(logdir)
    os.makedirs(outdir)

    # (1) PRE-EXISTING log, created BEFORE the stream starts.
    old = os.path.join(logdir, "old.log")
    with open(old, "wb") as f:
        f.write(b"OLD-HISTORY-LINE\n")
    time.sleep(0.05)                         # ensure old.log's ctime is clearly before start

    ctx = FakeCtx(outdir)
    th = threading.Thread(target=logs_stream.run, args=(ctx, [logdir]),
                          kwargs={"poll_every": 0.15, "own_slack": 0.0}, daemon=True)
    th.start()

    time.sleep(0.4)                          # stream opens old.log at its end (history skipped)
    with open(old, "ab") as f:
        f.write(b"APPENDED-AFTER-START\n")
    time.sleep(0.4)                          # only the appended bytes are tailed

    # (2) NEW log created AFTER start -> captured from byte 0.
    new = os.path.join(logdir, "new_script.txt")
    with open(new, "wb") as f:
        f.write(b"FRESH-FROM-BYTE-0\n")
    time.sleep(0.4)
    with open(new, "ab") as f:
        f.write(b"SECOND-LINE\n")
    time.sleep(0.4)

    # (4) rotation: truncate-in-place -> offset resets, post-rotation content re-captured.
    with open(new, "wb") as f:
        f.write(b"ROTATED-CONTENT\n")
    time.sleep(0.4)

    ctx.stop()
    th.join(timeout=2.0)

    fails = []
    old_tail = read_bytes(os.path.join(outdir, "logs", "old.log.tail"))
    new_tail = read_bytes(os.path.join(outdir, "logs", "new_script.txt.tail"))

    # (1) + (3): pre-existing history skipped, appended bytes captured byte-exactly
    if old_tail != b"APPENDED-AFTER-START\n":
        fails.append("old.log.tail should be EXACTLY the appended bytes, got %r" % old_tail)
    if old_tail is not None and b"OLD-HISTORY" in old_tail:
        fails.append("SILENT-DATA BUG: pre-existing history leaked into old.log.tail")

    # (2): new file captured from byte 0
    if not (new_tail and b"FRESH-FROM-BYTE-0" in new_tail):
        fails.append("new file not captured from byte 0 (got %r)" % new_tail)
    if not (new_tail and b"SECOND-LINE" in new_tail):
        fails.append("append to new file not tailed")
    # (4): rotation re-captured
    if not (new_tail and b"ROTATED-CONTENT" in new_tail):
        fails.append("post-rotation content not re-captured (got %r)" % new_tail)

    # log_open 'ours' flags correct
    opens = {os.path.basename(r["src"]): r for r in ctx.rows if r.get("kind") == "log_open"}
    if opens.get("old.log", {}).get("ours") is not False:
        fails.append("old.log should be marked ours=False")
    if opens.get("new_script.txt", {}).get("ours") is not True:
        fails.append("new_script.txt should be marked ours=True")

    if ctx.errors:
        fails.append("stream caught errors: %s" % ctx.errors[:3])

    print("old.log.tail=%r" % old_tail)
    print("new_script.txt.tail=%r" % new_tail)
    print("log_open ours flags: %s" % {k: v.get("ours") for k, v in opens.items()})
    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("PASS: logs stream skips pre-existing history, captures new logs from byte 0, is "
          "byte-exact, and re-reads on rotation -- the restart-gutting path, proven offline.")


if __name__ == "__main__":
    main()
