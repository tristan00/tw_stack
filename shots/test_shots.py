import os
import sys
import tempfile
import threading
import time

from PIL import Image

import shots_stream


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
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
    tmp = tempfile.mkdtemp(prefix="shots_test_")
    ctx = FakeCtx(tmp)

    def fake_grab():
        return Image.new("RGB", (64, 48), (10, 20, 30))

    def fake_fg():
        return ("test-window", 42)

    th = threading.Thread(target=shots_stream.run, args=(ctx,),
                          kwargs={"grab": fake_grab, "foreground": fake_fg, "shot_every": 0.3},
                          daemon=True)
    th.start()

    time.sleep(0.35)
    ctx.shot_req.set()
    time.sleep(0.4)
    time.sleep(0.4)
    ctx.stop()
    th.join(timeout=2.0)

    fails = []
    shots_dir = os.path.join(tmp, "shots")
    jpgs = sorted(f for f in os.listdir(shots_dir)) if os.path.isdir(shots_dir) else []
    shot_rows = [r for r in ctx.rows if r.get("kind") == "shot"]
    triggers = {r.get("trigger") for r in shot_rows}

    if len(jpgs) < 2:
        fails.append("expected >=2 jpgs, got %d" % len(jpgs))
    if "click" not in triggers:
        fails.append("no click-triggered shot (shot_req path) -- triggers=%s" % triggers)
    if "interval" not in triggers:
        fails.append("no interval-triggered shot -- triggers=%s" % triggers)
    if ctx.shot_req.is_set():
        fails.append("shot_req not cleared after the click shot")
    for r in shot_rows:
        p = r.get("file")
        if not (p and os.path.isfile(p)):
            fails.append("announced shot file missing: %s" % p)
            continue
        try:
            with Image.open(p) as im:
                im.verify()
        except Exception as e:
            fails.append("announced file is not a valid JPEG (%s): %r" % (p, e))
        if r.get("size") != [64, 48] or r.get("window") != "test-window":
            fails.append("shot row fields wrong: %s" % r)
    if ctx.errors:
        fails.append("stream caught errors: %s" % ctx.errors[:3])

    print("wrote %d jpg(s); %d shot rows; triggers=%s" % (len(jpgs), len(shot_rows), sorted(triggers)))
    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("PASS: shots stream captures click + interval frames as valid JPEGs with matching "
          "rows -- all offline, no desktop grab.")


if __name__ == "__main__":
    main()
