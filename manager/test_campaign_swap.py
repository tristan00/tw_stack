import json
import os
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_STACK = os.path.dirname(_HERE)
for _repo in ("logs", "campaigns"):
    sys.path.insert(0, os.path.join(_STACK, _repo))

import manager
import logs_stream

FAC_A = b"wh2_main_hef_nagarythe"
SUB_A = b"wh2_main_sc_hef_high_elves"
FAC_B = b"wh3_dlc27_sla_masque_of_slaanesh"
SUB_B = b"wh3_main_sc_sla_slaanesh"


def _faction_row(fac: bytes, sub: bytes, turn: int) -> bytes:
    return (b'TWSTATE {"kind":"faction","is_human":true,"faction":"' + fac +
            b'","subculture":"' + sub + b'","turn":%d}\n' % turn)


def _filler(turn: int) -> bytes:
    return b'TWSTATE {"kind":"turn","turn":%d,"note":"filler"}\n' % turn


def _campaign_a() -> bytes:
    out = b""
    for t in (1, 2, 3):
        out += _faction_row(FAC_A, SUB_A, t) + _filler(t)
    return out


def _campaign_b() -> bytes:
    out = b""
    for t in (1, 2):
        out += _faction_row(FAC_B, SUB_B, t) + _filler(t)
    return out


class FakeBusReset:

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self.calls += 1
        return 0


def _tail_bytes(run_dir: str, base: str) -> bytes:
    p = os.path.join(run_dir, "logs", base + ".tail")
    if not os.path.isfile(p):
        return b""
    with open(p, "rb") as f:
        return f.read()


def _events(run_dir: str) -> list:
    rows = []
    p = os.path.join(run_dir, "events.jsonl")
    if os.path.isfile(p):
        for ln in open(p, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def _run(log_bytes: bytes, reset_bus):
    tmp = tempfile.mkdtemp(prefix="r3_swap_")
    out_root = os.path.join(tmp, "runs")
    gamedir = os.path.join(tmp, "game")
    os.makedirs(out_root)
    os.makedirs(gamedir)

    log_name = "script_log_270720_1200.txt"
    log_path = os.path.join(gamedir, log_name)
    with open(log_path, "wb") as f:
        f.write(log_bytes)
    log_size = os.path.getsize(log_path)

    streams = [{"run": logs_stream.run, "name": "logs",
                "kwargs": {"log_dirs": [gamedir], "poll_every": 0.05, "own_slack": 9999.0}}]
    rec = manager.start(out_root, streams, recorder_version="r3-test",
                        meta_overrides={"shots_enabled": False, "ui_enabled": False},
                        reset_bus=reset_bus)

    deadline = time.time() + 5.0
    while time.time() < deadline:
        got = sum(len(_tail_bytes(d, log_name)) for d in rec.dirs)
        if got >= log_size:
            break
        time.sleep(0.05)
    time.sleep(0.15)
    rec.stop()
    return rec, gamedir, out_root, log_name, log_size


def two_campaign():
    """Two campaigns in one game log land in ONE run dir, byte for byte."""
    fails = []
    reset_bus = FakeBusReset()
    log_bytes = _campaign_a() + _campaign_b()
    rec, gamedir, out_root, log_name, log_size = _run(log_bytes, reset_bus)

    dirs_on_disk = sorted(os.path.join(out_root, d) for d in os.listdir(out_root)
                          if os.path.isdir(os.path.join(out_root, d)))

    if len(rec.dirs) != 1:
        fails.append("[A] expected exactly 1 run dir, rec.dirs=%s" % rec.dirs)
    if len(dirs_on_disk) != 1:
        fails.append("[A] expected 1 dir on disk, got %s" % dirs_on_disk)

    if len(rec.dirs) == 1:
        tail = _tail_bytes(rec.dirs[0], log_name)
        if FAC_A not in tail:
            fails.append("[B] campaign A missing from the tail")
        if FAC_B not in tail:
            fails.append("[B] campaign B missing from the tail")
        if tail != log_bytes:
            fails.append("[C] DROPPED OR REORDERED BYTES: tail=%d != original=%d"
                         % (len(tail), log_size))
        ctx, _out_file = rec._ctxs[0]
        if ctx.out_dir != rec.dirs[0]:
            fails.append("[D] ctx.out_dir drifted from the run dir (=%s)" % ctx.out_dir)

    if reset_bus.calls != 0:
        fails.append("[E] bus was reset mid-run (%d times) -- nothing swaps any more"
                     % reset_bus.calls)

    print("run dirs: %s" % [os.path.basename(d) for d in rec.dirs])
    print("tail bytes: %d of %d" % (len(_tail_bytes(rec.dirs[0], log_name)), log_size))
    if fails:
        print("\nTWO-CAMPAIGN one-dir: FAIL")
        for f in fails:
            print("  - %s" % f)
    else:
        print("TWO-CAMPAIGN one-dir: PASS")
    return fails, rec


def single_campaign():
    fails = []
    reset_bus = FakeBusReset()
    log_bytes = _campaign_a()
    rec, gamedir, out_root, log_name, log_size = _run(log_bytes, reset_bus)

    if len(rec.dirs) != 1 or rec.swap_count != 0:
        fails.append("[G] single campaign must NOT swap (dirs=%s swap_count=%d)"
                     % (rec.dirs, rec.swap_count))
    t = _tail_bytes(rec.dirs[0], log_name)
    if t != log_bytes:
        fails.append("[G] single-campaign tail != whole file (len %d vs %d)" % (len(t), log_size))
    if reset_bus.calls != 0:
        fails.append("[G] reset_bus should NOT fire with no swap (called %d)" % reset_bus.calls)
    return fails


def main():
    two, rec = two_campaign()
    print("TWO-CAMPAIGN swap: %s" % ("PASS" if not two else "FAIL"))
    for f in two:
        print("  - " + f)
    print("  dirs produced: %s" % [os.path.basename(d) for d in rec.dirs])

    one = single_campaign()
    print("SINGLE-CAMPAIGN (no swap): %s" % ("PASS" if not one else "FAIL"))
    for f in one:
        print("  - " + f)

    if two or one:
        sys.exit(1)
    print("\nPASS: two campaigns in one game log land in ONE run dir, byte for byte, "
          "nothing dropped at the boundary, no stream re-pointed and no bus reset -- a campaign "
          "no longer opens a new run directory. All offline, no game.")


if __name__ == "__main__":
    main()
