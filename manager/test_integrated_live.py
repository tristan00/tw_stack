import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_STACK = os.path.dirname(_HERE)
sys.path.insert(0, _STACK)
import common

for _repo in ("input", "shots", "logs", "ui-capture", "bus"):
    sys.path.insert(0, os.path.join(_STACK, _repo))

import manager
import input_stream
import shots_stream
import logs_stream
import ui_capture_stream

GAME_DIR = common.GAME_DIR
APPDATA = os.path.expandvars(r"%APPDATA%/The Creative Assembly/Warhammer3/logs")
OUT_ROOT = common.DECOMP_LIVETEST_ROOT


def main():
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    os.makedirs(OUT_ROOT, exist_ok=True)
    streams = [
        {"run": input_stream.run, "name": "input"},
        {"run": shots_stream.run, "name": "shots", "kwargs": {"shot_every": 2.0}},
        {"run": logs_stream.run, "name": "logs", "kwargs": {"log_dirs": [GAME_DIR, APPDATA]}},
        {"run": ui_capture_stream.run, "name": "ui-capture", "out_file": "ui_components.jsonl"},
    ]
    rec = manager.start(OUT_ROOT, streams, recorder_version="v5-decomp",
                        meta_overrides={"game_dir": GAME_DIR, "appdata_logs": APPDATA,
                                        "shots_enabled": True, "ui_enabled": True})
    print("MANAGER STARTED -> %s  (running %ds; open panels now)" % (rec.out_dir, dur), flush=True)
    time.sleep(dur)
    rec.stop()
    out = rec.out_dir

    fails = []

    def load(name):
        p = os.path.join(out, name)
        rows = []
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
        return rows

    events = load("events.jsonl")
    ekinds = set(r.get("kind") for r in events)
    ui_rows = load("ui_components.jsonl")

    if not ({"focus", "move"} & ekinds or {"key_down", "mouse_down"} & ekinds):
        fails.append("input: no input rows (kinds=%s)" % sorted(ekinds))
    sdir = os.path.join(out, "shots")
    njpg = len([f for f in os.listdir(sdir) if f.endswith(".jpg")]) if os.path.isdir(sdir) else 0
    if njpg < 2 or "shot" not in ekinds:
        fails.append("shots: only %d jpgs / shot rows=%s" % (njpg, "shot" in ekinds))
    ldir = os.path.join(out, "logs")
    twstate = 0
    tails = []
    if os.path.isdir(ldir):
        for f in os.listdir(ldir):
            if f.endswith(".tail"):
                tails.append(f)
                try:
                    with open(os.path.join(ldir, f), "rb") as fh:
                        twstate += fh.read().count(b"TWSTATE")
                except OSError:
                    pass
    if "log_open" not in ekinds:
        fails.append("logs: no log_open rows")
    if twstate == 0:
        fails.append("logs: NO TWSTATE captured (%d tail files) -- the semantic signal is missing" % len(tails))
    ui_status = [r.get("status") for r in ui_rows if r.get("kind") == "ui_status"]
    menu_opens = [r for r in ui_rows if r.get("kind") == "menu_open"]
    if "bus_available" not in ui_status:
        fails.append("ui-capture: never reported bus_available (status=%s)" % ui_status)
    meta = load("meta.json")
    metaobj = json.load(open(os.path.join(out, "meta.json"))) if os.path.isfile(os.path.join(out, "meta.json")) else {}
    if metaobj.get("recorder_version") != "v5-decomp":
        fails.append("meta.recorder_version wrong: %s" % metaobj.get("recorder_version"))

    print("\n================= INTEGRATED LIVE RESULT =================")
    print("run dir           : %s" % out)
    print("events.jsonl kinds: %s" % sorted(ekinds))
    print("shots jpgs        : %d" % njpg)
    print("log tails         : %d files, %d TWSTATE lines" % (len(tails), twstate))
    print("ui_components rows : %d  (status=%s, menu_open=%d)" % (len(ui_rows), ui_status, len(menu_opens)))
    if menu_opens:
        print("  sample panels   : %s" % [m.get("panel") for m in menu_opens][:6])
    errlog = os.path.join(out, "errors.log")
    if os.path.isfile(errlog):
        print("errors.log        : %s" % open(errlog).read()[:400])
    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("\nPASS: the decomposed recorder captured all four streams live -- input + shots + real "
          "TWSTATE logs + ui-capture on the bus -- one shared clock, no game contact by the "
          "orchestrator itself.")


if __name__ == "__main__":
    main()
