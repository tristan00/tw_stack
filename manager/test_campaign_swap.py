r"""Offline test for R3 -- the recorder auto-swaps to a fresh run dir when a NEW campaign starts.

No game, no bus. Drives a TWO-CAMPAIGN script_log through the REAL logs stream + REAL manager +
REAL splitter.CampaignTracker, with an injected fake bus-reset and a temp game-log dir, and proves:

  [A] TWO run dirs are created (initial + one swap) for a two-campaign log; ONE for a single one.
  [B] BOUNDARY ROWS land in the correct dir -- campaign A's faction rows only in dir1, campaign B's
      only in dir2 (byte-exact split at the boundary line).
  [C] NOTHING IS DROPPED -- the sum of every dir's tail bytes equals the original log's bytes.
  [D] EACH STREAM'S WRITER + out_dir is RE-POINTED to the new dir on the swap.
  [E] reset_bus is invoked exactly once per swap (0 times with no swap).
  [F] a fresh meta.json (campaign_index + swapped_from) and swap markers are written per dir.
  [G] single-campaign recording is UNCHANGED (no swap signal -> exactly one dir, whole-file tail).

    python test_campaign_swap.py
"""
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

import manager                       # noqa: E402
import logs_stream                   # noqa: E402

# Two campaigns in ONE script_log: high-elf Nagarythe (turns 1..3) then Slaanesh (turns 1..2).
FAC_A = b"wh2_main_hef_nagarythe"
SUB_A = b"wh2_main_sc_hef_high_elves"
FAC_B = b"wh3_dlc27_sla_masque_of_slaanesh"
SUB_B = b"wh3_main_sc_sla_slaanesh"


def _faction_row(fac: bytes, sub: bytes, turn: int) -> bytes:
    return (b'TWSTATE {"kind":"faction","is_human":true,"faction":"' + fac +
            b'","subculture":"' + sub + b'","turn":%d}\n' % turn)


def _filler(turn: int) -> bytes:
    # a non-identity state line (no is_human) -> ignored by the boundary kernel, still tailed.
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
    """Thread-safe stand-in for reset_bus_files: counts swap-time invocations, touches no bus."""

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
    """Write a static script_log, record it through the real logs stream + manager until the tail
    is fully drained, then stop. Returns (rec, gamedir, out_root, log_name, log_size)."""
    tmp = tempfile.mkdtemp(prefix="r3_swap_")
    out_root = os.path.join(tmp, "runs")
    gamedir = os.path.join(tmp, "game")
    os.makedirs(out_root)
    os.makedirs(gamedir)

    log_name = "script_log_270720_1200.txt"
    log_path = os.path.join(gamedir, log_name)
    with open(log_path, "wb") as f:                  # complete, closed BEFORE recording starts
        f.write(log_bytes)
    log_size = os.path.getsize(log_path)

    streams = [{"run": logs_stream.run, "name": "logs",
                "kwargs": {"log_dirs": [gamedir], "poll_every": 0.05, "own_slack": 9999.0}}]
    rec = manager.start(out_root, streams, recorder_version="r3-test",
                        meta_overrides={"shots_enabled": False, "ui_enabled": False},
                        reset_bus=reset_bus)

    # wait until every byte of the source log has been tailed (across whatever dirs were produced).
    deadline = time.time() + 5.0
    while time.time() < deadline:
        got = sum(len(_tail_bytes(d, log_name)) for d in rec.dirs)
        if got >= log_size:
            break
        time.sleep(0.05)
    time.sleep(0.15)                                 # let the final log_tail row flush
    rec.stop()
    return rec, gamedir, out_root, log_name, log_size


def two_campaign():
    fails = []
    reset_bus = FakeBusReset()
    log_bytes = _campaign_a() + _campaign_b()
    rec, gamedir, out_root, log_name, log_size = _run(log_bytes, reset_bus)

    dirs_on_disk = sorted(os.path.join(out_root, d) for d in os.listdir(out_root)
                          if os.path.isdir(os.path.join(out_root, d)))

    # [A] two dirs
    if len(rec.dirs) != 2:
        fails.append("[A] expected 2 run dirs, rec.dirs=%s" % rec.dirs)
    if len(dirs_on_disk) != 2:
        fails.append("[A] expected 2 dirs on disk, got %s" % dirs_on_disk)
    if rec.swap_count != 1:
        fails.append("[A] expected swap_count==1, got %d" % rec.swap_count)

    if len(rec.dirs) == 2:
        d1, d2 = rec.dirs[0], rec.dirs[1]
        t1, t2 = _tail_bytes(d1, log_name), _tail_bytes(d2, log_name)

        # [B] boundary rows in the correct dir
        if FAC_A not in t1 or FAC_B in t1:
            fails.append("[B] dir1 tail must hold campaign A only (A in=%s, B in=%s)"
                         % (FAC_A in t1, FAC_B in t1))
        if FAC_B not in t2 or FAC_A in t2:
            fails.append("[B] dir2 tail must hold campaign B only (B in=%s, A in=%s)"
                         % (FAC_B in t2, FAC_A in t2))
        # the split must fall exactly at campaign B's first faction row
        if t1 != _campaign_a() or t2 != _campaign_b():
            fails.append("[B] byte-exact split wrong: len(t1)=%d len(t2)=%d (want %d/%d)"
                         % (len(t1), len(t2), len(_campaign_a()), len(_campaign_b())))

        # [C] nothing dropped -- parts sum to the whole
        if len(t1) + len(t2) != log_size:
            fails.append("[C] DROPPED BYTES: t1+t2=%d != original=%d" % (len(t1) + len(t2), log_size))

        # [D] writer + out_dir re-pointed to the new dir
        ctx, out_file = rec._ctxs[0]
        if getattr(ctx._emit, "out_dir", None) != d2:
            fails.append("[D] ctx._emit not re-pointed to dir2 (=%s)" % getattr(ctx._emit, "out_dir", None))
        if ctx.out_dir != d2:
            fails.append("[D] ctx.out_dir not re-pointed to dir2 (=%s)" % ctx.out_dir)
        if rec._writers["events.jsonl"].out_dir != d2:
            fails.append("[D] events writer not re-pointed to dir2")
        # the OLD writer still points at dir1 (kept open so a racing emit is never dropped)
        old = [w for w in rec._all_writers if w.out_dir == d1 and w.name == "events.jsonl"]
        if not old:
            fails.append("[D] old dir1 events writer was not retained")

        # [E] reset_bus invoked once (per swap)
        if reset_bus.calls != 1:
            fails.append("[E] reset_bus called %d times, expected 1" % reset_bus.calls)

        # [F] meta + swap markers per dir
        m1 = json.load(open(os.path.join(d1, "meta.json")))
        m2 = json.load(open(os.path.join(d2, "meta.json")))
        if m1.get("campaign_index") != 0:
            fails.append("[F] dir1 meta.campaign_index != 0 (=%s)" % m1.get("campaign_index"))
        if m2.get("campaign_index") != 1 or m2.get("swapped_from") != d1:
            fails.append("[F] dir2 meta wrong: campaign_index=%s swapped_from=%s"
                         % (m2.get("campaign_index"), m2.get("swapped_from")))
        e1k = [r.get("kind") for r in _events(d1)]
        e2 = _events(d2)
        if "campaign_swap_out" not in e1k:
            fails.append("[F] dir1 events.jsonl missing 'campaign_swap_out' (got %s)" % e1k)
        if not any(r.get("kind") == "start" and r.get("swap") for r in e2):
            fails.append("[F] dir2 events.jsonl missing swap 'start' row")

    # no stream errored in either dir
    for d in rec.dirs:
        el = os.path.join(d, "errors.log")
        if os.path.isfile(el):
            fails.append("errors.log in %s: %s" % (os.path.basename(d), open(el).read()[:200]))
    return fails, rec


def single_campaign():
    """[G] one campaign -> no swap signal -> exactly one dir, whole-file tail, bus never reset."""
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
    print("\nPASS: R3 auto-swaps to a fresh run dir on a new campaign -- two campaigns land in two "
          "dirs split byte-exactly at the boundary (nothing dropped), every stream re-pointed, bus "
          "reset once per swap; a single campaign records unchanged in one dir. All offline, no game.")


if __name__ == "__main__":
    main()
