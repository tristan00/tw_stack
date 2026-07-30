r"""Offline test for `bus_stats` (NO game / NO bus).

Proves the call-measurement layer end to end without a running WH3:
  1. classify_outcome() maps real find/eval reply shapes to hit/empty.
  2. StatsTracker accumulates in memory and flushes to sqlite (accumulate-on-conflict),
     and build_report() surfaces the JUNK list (calls>=5, hits==0).
  3. Bus.send()'s instrumentation records the right outcome for a HIT / EMPTY / TIMEOUT
     call when _send_impl is stubbed (so no game is touched), and those land in the DB.

    python test_bus_stats.py
"""
import os
import sys
import tempfile

# make bus_stats write to a scratch DB and stay enabled BEFORE importing the modules
_TMP = tempfile.mkdtemp(prefix="bus_stats_test_")
os.environ["BUS_STATS"] = "1"
os.environ["BUS_STATS_DB"] = os.path.join(_TMP, "bus_stats.sqlite")

import bus_stats
from errors import TWError

fails = []


# ---- 1. classifier ------------------------------------------------------------------------
FIND_HIT = {"seq": 1, "cmd": "find", "path": "units_panel",
            "result": {"found": True, "id": "units_panel"}, "child_ids": ["a", "b"]}
FIND_EMPTY = {"seq": 2, "cmd": "find", "path": "rituals_panel",
              "result": {"found": False}, "child_ids": []}
EVAL_HIT = {"seq": 3, "cmd": "eval", "result": 42, "error": None}
EVAL_EMPTY = {"seq": 4, "cmd": "eval", "result": None, "error": None}

cases = [
    ("find HIT", "find", FIND_HIT, "hit"),
    ("find EMPTY (found=false)", "find", FIND_EMPTY, "empty"),
    ("eval HIT (scalar result)", "eval", EVAL_HIT, "hit"),
    ("eval EMPTY (null result)", "eval", EVAL_EMPTY, "empty"),
    ("garbage reply (not a dict)", "find", None, "empty"),
]
for name, ch, reply, want in cases:
    got = bus_stats.classify_outcome(ch, reply)
    if got != want:
        fails.append("classify %s: got %r want %r" % (name, got, want))


# ---- 2. StatsTracker + report round-trip --------------------------------------------------
# tiny flush thresholds so we exercise a real disk flush; register_atexit off for a clean test.
tr = bus_stats.StatsTracker(os.environ["BUS_STATS_DB"], flush_every_n=3, flush_every_s=999,
                            register_atexit=False)

# a genuine JUNK key: 6 calls, all empty (never a hit) -> must appear in the junk list.
for _ in range(6):
    tr.record("find", "great_game_rituals", "empty", 12.5)
# a healthy key: mostly hits -> must NOT be junk.
for _ in range(5):
    tr.record("find", "units_panel", "hit", 3.0)
# a timeout-only key with >=5 calls and 0 hits -> junk too (wasted time is what we want to see).
for _ in range(5):
    tr.record("find", "rituals_panel", "timeout", 300.0)
# a low-volume empty key (<5 calls) -> below the junk threshold, must NOT be junk.
for _ in range(2):
    tr.record("find", "occasional_panel", "empty", 4.0)
tr.flush()

# accumulate-on-conflict check: a SECOND flush of the same key must ADD, not overwrite.
tr.record("find", "great_game_rituals", "empty", 7.5)
tr.flush()

rep = bus_stats.build_report(os.environ["BUS_STATS_DB"], junk_min_calls=5)
junk_keys = {(r["channel"], r["key"]) for r in rep["junk"]}

if ("find", "great_game_rituals") not in junk_keys:
    fails.append("great_game_rituals (7 empty calls) should be JUNK but isn't")
if ("find", "rituals_panel") not in junk_keys:
    fails.append("rituals_panel (5 timeouts, 0 hits) should be JUNK but isn't")
if ("find", "units_panel") in junk_keys:
    fails.append("units_panel (has hits) must NOT be junk")
if ("find", "occasional_panel") in junk_keys:
    fails.append("occasional_panel (<5 calls) must NOT be junk")

# accumulate check: great_game_rituals must show 7 calls (6 + 1), not 1.
ggr = next((r for r in rep["rows"] if r["key"] == "great_game_rituals"), None)
if ggr is None or ggr["calls"] != 7 or ggr["empties"] != 7:
    fails.append("accumulate-on-conflict broken: great_game_rituals=%r (want calls=7 empties=7)" % ggr)

# totals sanity: 6+5+5+2 + 1(second flush) = 19 calls, 5 hits.
if rep["totals"]["calls"] != 19:
    fails.append("totals.calls = %d, want 19" % rep["totals"]["calls"])
if rep["totals"]["hits"] != 5:
    fails.append("totals.hits = %d, want 5" % rep["totals"]["hits"])


# ---- 3. Bus.send instrumentation (stubbed _send_impl, NO game) -----------------------------
import bus

# Section 3 exercises the MEASUREMENT layer in isolation: disable the active barrier so repeated
# timeouts are all recorded as real round-trips (the barrier is proven separately in Section 4).
os.environ["BUS_GUARD"] = "0"

# a fresh tracker for the send-path DB, installed as the process singleton the wrapper uses.
send_db = os.path.join(_TMP, "send_stats.sqlite")
send_tr = bus_stats.StatsTracker(send_db, flush_every_n=1, flush_every_s=999,
                                 register_atexit=False)  # flush every call so we can read immediately
bus_stats.install_tracker(send_tr)

b = bus.Bus(cmd_path=os.path.join(_TMP, "commands.txt"),
            out_path=os.path.join(_TMP, "twcontrol.jsonl"))


def _stub_send_impl(channel, payload="", timeout=30):
    """Stand in for the real bus round-trip: reply shape / exception keyed off the payload."""
    if payload == "units_panel":
        return FIND_HIT
    if payload == "great_game_rituals":
        return FIND_EMPTY
    if payload == "slow_missing_panel":
        raise TWError("bus timeout: no result for seq 9 cmd find")
    raise TWError("bus: WH3 process gone while awaiting seq 9 cmd find")  # CTD -> error


b._send_impl = _stub_send_impl  # instance-level monkeypatch (no game touched)

assert b.send("find", "units_panel") is FIND_HIT           # HIT recorded
assert b.send("find", "great_game_rituals") is FIND_EMPTY  # EMPTY recorded
for _ in range(5):
    try:
        b.send("find", "slow_missing_panel")               # TIMEOUT recorded x5 -> junk
    except TWError:
        pass
try:
    b.send("find", "dead_game")                            # CTD -> error (NOT timeout)
except TWError:
    pass
send_tr.flush()

srep = bus_stats.build_report(send_db, junk_min_calls=5)
srows = {r["key"]: r for r in srep["rows"]}
if srows.get("units_panel", {}).get("hits") != 1:
    fails.append("send-path: units_panel should record 1 hit, got %r" % srows.get("units_panel"))
if srows.get("great_game_rituals", {}).get("empties") != 1:
    fails.append("send-path: great_game_rituals should record 1 empty, got %r"
                 % srows.get("great_game_rituals"))
if srows.get("slow_missing_panel", {}).get("timeouts") != 5:
    fails.append("send-path: slow_missing_panel should record 5 timeouts, got %r"
                 % srows.get("slow_missing_panel"))
if srows.get("dead_game", {}).get("errors") != 1:
    fails.append("send-path: CTD should record 1 error (not timeout), got %r"
                 % srows.get("dead_game"))
if ("find", "slow_missing_panel") not in {(r["channel"], r["key"]) for r in srep["junk"]}:
    fails.append("send-path: slow_missing_panel (5 timeouts, 0 hits) should be JUNK")


# ---- 4. ACTIVE junk-find barrier (short-circuit + re-probe + reset + never-suppress-a-hitter) ---
# Prove the barrier WITHOUT a game: stub _send_impl to count real round-trips per payload and
# return a hit / a miss / a timeout by key. A dead key must be short-circuited (NO _send_impl call)
# and return a well-formed miss; re-probe must eventually let one real call through; reset must
# clear suppression; a HITTING key must NEVER be suppressed; non-find channels must NEVER be
# short-circuited.
os.environ["BUS_GUARD"] = "1"

# small threshold / reprobe so the test is short and deterministic.
guard = bus_stats.SuppressionGuard(threshold=3, reprobe_every=5)
bus_stats.install_guard(guard)
bus_stats.reset_suppression()   # start clean

impl_calls = {}


def _guard_stub(channel, payload="", timeout=30):
    """Count real round-trips; reply shape keyed off (channel,payload). NO game touched."""
    impl_calls[(channel, payload)] = impl_calls.get((channel, payload), 0) + 1
    if channel == "find" and payload == "units_panel":
        return FIND_HIT
    if channel == "find" and payload == "absent_panel":
        return FIND_EMPTY
    if channel == "find" and payload == "slow_panel":
        raise TWError("bus timeout: no result for seq 1 cmd find")   # -> timeout outcome
    if channel == "eval":
        return EVAL_EMPTY
    return FIND_EMPTY


gb = bus.Bus(cmd_path=os.path.join(_TMP, "g_commands.txt"),
             out_path=os.path.join(_TMP, "g_twcontrol.jsonl"))
gb._send_impl = _guard_stub

# (a) below threshold: the first 3 finds on an absent key all reach the mod (learning phase).
for _ in range(3):
    r = gb.send("find", "absent_panel")
    if r.get("short_circuited"):
        fails.append("barrier: absent_panel was short-circuited BEFORE reaching the threshold")
if impl_calls.get(("find", "absent_panel")) != 3:
    fails.append("barrier: first 3 absent_panel finds should all hit _send_impl, got %r"
                 % impl_calls.get(("find", "absent_panel")))

# (b) the 4th find is now short-circuited: NO _send_impl call, and a well-formed synthetic miss.
r4 = gb.send("find", "absent_panel")
if impl_calls.get(("find", "absent_panel")) != 3:
    fails.append("barrier: 4th absent_panel find must NOT call _send_impl (still 3), got %r"
                 % impl_calls.get(("find", "absent_panel")))
if not r4.get("short_circuited"):
    fails.append("barrier: 4th absent_panel find should be short_circuited, got %r" % r4)
if (r4.get("result") or {}).get("found") is not False or r4.get("child_ids") != []:
    fails.append("barrier: synthetic miss has the wrong not-found shape: %r" % r4)
if bus_stats.classify_outcome("find", r4) != "empty":
    fails.append("barrier: synthetic miss must classify as 'empty' (a clean miss), not %r"
                 % bus_stats.classify_outcome("find", r4))

# (c) re-probe: keep sending on the dead key; with reprobe_every=5, exactly ONE of the next 5
# checks is let through to a real _send_impl call (auto un-suppress path).
before = impl_calls.get(("find", "absent_panel"))
sc_count = 0
for _ in range(5):
    rr = gb.send("find", "absent_panel")
    if rr.get("short_circuited"):
        sc_count += 1
after = impl_calls.get(("find", "absent_panel"))
if after - before != 1:
    fails.append("barrier: re-probe should let EXACTLY 1 real call through per 5, got %d" % (after - before))
if sc_count != 4:
    fails.append("barrier: 4 of every 5 dead-key calls should be short-circuited, got %d" % sc_count)

# (d) reset clears suppression: after reset the dead key is treated as fresh -> real _send_impl again.
bus_stats.reset_suppression()
before2 = impl_calls.get(("find", "absent_panel"))
r_reset = gb.send("find", "absent_panel")
if impl_calls.get(("find", "absent_panel")) != before2 + 1:
    fails.append("barrier: after reset_suppression the dead key must reach _send_impl again")
if r_reset.get("short_circuited"):
    fails.append("barrier: after reset_suppression the first call must NOT be short-circuited")

# (e) a HITTING key is NEVER suppressed, even well past the threshold.
bus_stats.reset_suppression()
for i in range(10):
    rh = gb.send("find", "units_panel")
    if rh.get("short_circuited"):
        fails.append("barrier: a hitting key (units_panel) must NEVER be short-circuited")
        break
if impl_calls.get(("find", "units_panel")) != 10:
    fails.append("barrier: every units_panel find must reach _send_impl, got %r"
                 % impl_calls.get(("find", "units_panel")))

# (f) SCOPE SAFETY: a non-find channel (eval) is NEVER short-circuited, no matter how many empties.
bus_stats.reset_suppression()
for _ in range(8):
    re_ = gb.send("eval", "some_absent_getter")
    if isinstance(re_, dict) and re_.get("short_circuited"):
        fails.append("barrier: eval must NEVER be short-circuited (scope violation)")
        break
if impl_calls.get(("eval", "some_absent_getter")) != 8:
    fails.append("barrier: every eval must reach _send_impl (never suppressed), got %r"
                 % impl_calls.get(("eval", "some_absent_getter")))

# (g) timeout-only keys also go dead (hits==0 via timeouts, not just empties).
bus_stats.reset_suppression()
for _ in range(3):
    try:
        gb.send("find", "slow_panel")
    except TWError:
        pass
before3 = impl_calls.get(("find", "slow_panel"))
r_slow = gb.send("find", "slow_panel")   # 4th -> should be short-circuited (no timeout burned)
if impl_calls.get(("find", "slow_panel")) != before3:
    fails.append("barrier: a timeout-only dead key must be short-circuited too (no _send_impl call)")
if not r_slow.get("short_circuited"):
    fails.append("barrier: 4th slow_panel find should be short_circuited, got %r" % r_slow)

# (h) BUS_GUARD=0 disables the barrier entirely (exact prior behaviour: no short-circuit).
os.environ["BUS_GUARD"] = "0"
bus_stats.install_guard(None)            # force a rebuild against the new env on next get_guard()
before4 = impl_calls.get(("find", "absent_panel"))
for _ in range(6):
    rg = gb.send("find", "absent_panel")
    if rg.get("short_circuited"):
        fails.append("barrier: with BUS_GUARD=0 nothing may be short-circuited")
        break
if impl_calls.get(("find", "absent_panel")) != before4 + 6:
    fails.append("barrier: with BUS_GUARD=0 every call must reach _send_impl, got %r"
                 % (impl_calls.get(("find", "absent_panel")) - before4))
os.environ["BUS_GUARD"] = "1"            # restore for any later use
bus_stats.install_guard(None)

print("### ACTIVE JUNK-FIND BARRIER (offline, no game/bus) ###\n")
print("  (a/b) absent_panel: 3 finds reached the mod, then find #4 short-circuited "
      "(no _send_impl call), returning a synthetic miss classified as 'empty'.")
print("  (c)   re-probe: over the next 5 dead-key calls, exactly 1 real find was let through "
      "(reprobe_every=5) -- auto un-suppress path.")
print("  (d)   reset_suppression(): the dead key reached _send_impl again on the next call.")
print("  (e)   hitting key (units_panel): 10/10 finds reached the mod -- NEVER suppressed.")
print("  (f)   scope: 8/8 eval calls reached the mod -- non-find channels are NEVER short-circuited.")
print("  (g)   timeout-only dead key (slow_panel): short-circuited after 3 timeouts -- no 4th timeout burned.")
print("  (h)   BUS_GUARD=0: 6/6 finds reached the mod -- barrier fully disabled (exact prior behaviour).\n")


# ---- report / verdict ---------------------------------------------------------------------
print("### JUNK-CALL REPORT (from StatsTracker round-trip DB) ###\n")
print(bus_stats.format_report(rep))
print("\n### JUNK-CALL REPORT (from instrumented Bus.send DB) ###\n")
print(bus_stats.format_report(srep))
print()

if fails:
    print("FAIL:")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("PASS: classifier, sqlite accumulate/flush, report junk-list, Bus.send instrumentation, AND the "
      "active junk-find barrier (short-circuit + re-probe + reset + never-suppress-a-hitter + scope + "
      "BUS_GUARD gate) all verified offline (no game / no bus).")
