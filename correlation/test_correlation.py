r"""Offline test for `correlation`, run against real v5 session logs (no game).

Correctness is NOT "high match_rate on every session": rate reflects the UI-vs-map click mix (a
map-heavy session logs few UI components, so few clicks can match -- correctly). The real signals:
  * the offset is deterministic (brute force, no RNG),
  * clicks resolve to SPECIFIC components, not just the root panel (a spurious offset would not),
  * at least one UI-heavy session aligns at a high rate (proves the mechanism),
  * offsets are physically consistent: sessions launch sequentially, so a later session (its game
    clock restarts near 0) has a MORE-NEGATIVE offset than an earlier one.

    python test_correlation.py
"""
import sys

import correlation

RUN = r"D:/twdata/runs/human/20260726_113146"
# session logs in launch order (by filename timestamp)
FIXTURES = [
    ("script_log_260726_1212.txt.tail", RUN + "/logs/script_log_260726_1212.txt.tail"),
    ("script_log_260726_1256.txt.tail", RUN + "/logs/script_log_260726_1256.txt.tail"),
]
MIN_BEST_RATE = 80.0
GENERIC = {"root", "panel_manager", None}

results = []
fails: list[str] = []
for name, log in FIXTURES:
    r1 = correlation.correlate(RUN, [log])
    r2 = correlation.correlate(RUN, [log])                     # determinism
    results.append((name, r1))
    print("%s: clicks=%d ui=%d offset=%s matched=%d rate=%.1f%%"
          % (name, r1["clicks"], r1["ui_events"], r1["offset"], r1["matched"], r1["match_rate"]))
    for s in r1["samples"][:4]:
        print("    t=%.1f gt=%.1f -> %s" % (s["t"], s["gt"], s["hit"]))
    if r1["offset"] is None:
        fails.append("%s: no offset solved" % name)
    if (r1["offset"], r1["matched"]) != (r2["offset"], r2["matched"]):
        fails.append("%s: non-deterministic" % name)
    if not any(s["hit"] not in GENERIC for s in r1["samples"]):
        fails.append("%s: no specific-component matches (offset may be spurious)" % name)

# at least one session aligns well (rate is meaningful only where there is UI to hit)
best = max((r["match_rate"] for _, r in results), default=0.0)
if best < MIN_BEST_RATE:
    fails.append("best match_rate %.1f%% < %.1f%% -- no session aligned well" % (best, MIN_BEST_RATE))

# physical cross-check: later sessions launch later, so their offset is more negative
offs = [r["offset"] for _, r in results if r["offset"] is not None]
if offs != sorted(offs, reverse=True):
    fails.append("offsets not monotonic with launch order %r (later session should be more negative)" % offs)

if fails:
    print("\nFAIL (%d):" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("\nPASS: offset deterministic + physically consistent across sessions; best UI-heavy session "
      "aligned at %.1f%% with specific components." % best)
print("(Per-session rate reflects UI-vs-map click mix, not alignment correctness.)")
