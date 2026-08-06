import json
import os
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_STACK = os.path.dirname(_HERE)
for _repo in ("input", "shots", "logs"):
    sys.path.insert(0, os.path.join(_STACK, _repo))

import manager
import input_stream
import shots_stream
import logs_stream
from PIL import Image


def load_rows(out_dir):
    rows = []
    with open(os.path.join(out_dir, "events.jsonl"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def unit_stubs():
    fails = []

    def stub_ok(ctx):
        ctx.emit({"t": ctx.now(), "kind": "stub_ok"})
        while ctx.is_running():
            time.sleep(0.02)

    def stub_marker(ctx):
        with open(os.path.join(ctx.out_dir, "marker.txt"), "w") as f:
            f.write("ran")
        ctx.emit({"t": ctx.now(), "kind": "stub_marker"})
        while ctx.is_running():
            time.sleep(0.02)

    def stub_crash(ctx):
        raise RuntimeError("boom")

    out_root = tempfile.mkdtemp(prefix="mgr_unit_")
    rec = manager.start(out_root,
                        [{"run": stub_ok, "name": "ok"},
                         {"run": stub_crash, "name": "crash"},
                         {"run": stub_marker, "name": "marker"}],
                        recorder_version="test-decomp",
                        meta_overrides={"shots_enabled": False, "ui_enabled": False})
    time.sleep(0.3)
    rec.stop()

    out = rec.out_dir
    if not os.path.isfile(os.path.join(out, "meta.json")):
        fails.append("meta.json missing")
    else:
        meta = json.load(open(os.path.join(out, "meta.json")))
        if meta.get("recorder_version") != "test-decomp":
            fails.append("meta.recorder_version wrong: %s" % meta.get("recorder_version"))
        if "t0_epoch" not in meta or "started" not in meta:
            fails.append("meta missing clock/started fields")

    rows = load_rows(out)
    kinds = [r["kind"] for r in rows]
    for need in ("start", "stub_ok", "stub_marker", "stop"):
        if need not in kinds:
            fails.append("events.jsonl missing %r row" % need)
    if not os.path.isfile(os.path.join(out, "marker.txt")):
        fails.append("stub_marker did NOT run (a crashing sibling stopped it?)")
    errlog = os.path.join(out, "errors.log")
    if not (os.path.isfile(errlog) and "stream:crash" in open(errlog).read()):
        fails.append("crashing stream was not logged to errors.log (silent death)")
    ts = [r["t"] for r in rows]
    if any(t < 0 or t > 5.0 for t in ts):
        fails.append("row timestamps outside the shared-clock window: %s" % ts)
    return fails


class FakeOS:
    def __init__(self):
        self.fg = ("editor - x.py", 1)
        self.pos = (0, 0)
        self.keys = set()

    def cursor(self):
        return self.pos

    def foreground(self):
        return self.fg

    def keystate(self, vk):
        return vk in self.keys


def integration():
    fails = []
    tmp = tempfile.mkdtemp(prefix="mgr_integ_")
    out_root = os.path.join(tmp, "runs")
    logdir = os.path.join(tmp, "game")
    os.makedirs(out_root)
    os.makedirs(logdir)

    os_state = FakeOS()
    probes = input_stream.Probes(cursor=os_state.cursor, foreground=os_state.foreground,
                                 keystate=os_state.keystate)

    def fake_grab():
        return Image.new("RGB", (32, 24), (5, 5, 5))

    streams = [
        {"run": input_stream.run, "name": "input", "kwargs": {"probes": probes}},
        {"run": shots_stream.run, "name": "shots",
         "kwargs": {"grab": fake_grab, "foreground": (lambda: ("w", 1)), "shot_every": 0.3}},
        {"run": logs_stream.run, "name": "logs",
         "kwargs": {"log_dirs": [logdir], "poll_every": 0.15, "own_slack": 0.0}},
    ]
    rec = manager.start(out_root, streams, recorder_version="v5-decomp",
                        meta_overrides={"shots_enabled": True, "ui_enabled": False})

    time.sleep(0.2)
    os_state.pos = (100, 200)
    time.sleep(0.15)
    os_state.keys = {0x53}
    time.sleep(0.15)
    os_state.keys = set()
    os_state.keys = {0x01}
    time.sleep(0.15)
    os_state.keys = set()
    with open(os.path.join(logdir, "script_log_test.txt"), "wb") as f:
        f.write(b"TWSTATE fresh session line\n")
    time.sleep(0.4)
    rec.stop()

    out = rec.out_dir
    rows = load_rows(out)
    kinds = set(r["kind"] for r in rows)

    for need in ("start", "focus", "move", "key_down", "shot", "log_open", "log_tail", "stop"):
        if need not in kinds:
            fails.append("integration events.jsonl missing %r (got %s)" % (need, sorted(kinds)))
    sdir = os.path.join(out, "shots")
    if not (os.path.isdir(sdir) and any(f.endswith(".jpg") for f in os.listdir(sdir))):
        fails.append("no shots/*.jpg produced")
    tail = os.path.join(out, "logs", "script_log_test.txt.tail")
    if not (os.path.isfile(tail) and b"TWSTATE fresh session line" in open(tail, "rb").read()):
        fails.append("fresh game log not captured from byte 0")
    ts = [r["t"] for r in rows]
    if any(t < 0 or t > 6.0 for t in ts):
        fails.append("timestamps outside shared-clock window")
    if os.path.isfile(os.path.join(out, "errors.log")):
        fails.append("a stream errored: %s" % open(os.path.join(out, "errors.log")).read()[:300])
    return fails


def main():
    u = unit_stubs()
    print("UNIT (stubs): %s" % ("PASS" if not u else "FAIL"))
    for f in u:
        print("  - " + f)
    g = integration()
    print("INTEGRATION (real input+shots+logs): %s" % ("PASS" if not g else "FAIL"))
    for f in g:
        print("  - " + f)
    if u or g:
        sys.exit(1)
    print("\nPASS: manager builds the run dir + shared clock + meta, runs streams as threads, "
          "logs a crashing stream without stopping siblings, and the full input+shots+logs "
          "pipeline captures a populated run -- all offline, no game.")


if __name__ == "__main__":
    main()
