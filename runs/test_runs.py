r"""Offline test for `runs`, run against the real recorded corpus (no game).

Asserts the validity-gate invariants + the two specific runs that MUST fail the gate (the mistakes
that motivated this repo), then prints the full valid/invalid partition as evidence.

    python test_runs.py
"""
import sys

import runs

MUST_BE_INVALID = {
    "20260719_223310": "unversioned (no recorder_version) -- the run an agent trusted off a memory note",
    "20260725_043045": "recorder_version=dev, not v5",
}

recs = {r["run"]: r for r in runs.list_runs()}
valid = runs.list_valid_runs()

fails: list[str] = []

# invariant 1: the known-bad runs are scanned, flagged NOT valid, with a reason
for rid, why in MUST_BE_INVALID.items():
    r = recs.get(rid)
    if r is None:
        fails.append("%s not found in corpus (expected invalid: %s)" % (rid, why))
    elif r["valid"]:
        fails.append("%s wrongly VALID (expected invalid: %s)" % (rid, why))
    elif not r["reasons"]:
        fails.append("%s invalid but gave no reason" % rid)

# invariant 2: every valid run is v5 + has all required streams + a derivable faction
for r in valid:
    if r["recorder_version"] != "v5":
        fails.append("%s valid but recorder_version=%r" % (r["run"], r["recorder_version"]))
    if not all(r["streams"].values()):
        fails.append("%s valid but missing streams %r" % (r["run"], r["streams"]))
    if not r["campaigns"]:
        fails.append("%s valid but no derivable faction" % r["run"])

# invariant 3: never guess -- a run with UNKNOWN identity is never valid
for r in recs.values():
    if not r["campaigns"] and r["valid"]:
        fails.append("%s valid despite UNKNOWN identity" % r["run"])

# evidence
print("VALID v5 runs (%d):" % len(valid))
for r in sorted(valid, key=lambda x: x["run"]):
    print("  %-18s ver=%s  campaigns=%d" % (r["run"], r["recorder_version"], len(r["campaigns"])))
    for c in sorted(r["campaigns"], key=lambda c: -c["max_turn"]):
        print("      %-36s turns %s..%s" % (c["faction"], c["min_turn"] or "?", c["max_turn"]))
invalid = [r for r in recs.values() if not r["valid"]]
print("\nINVALID / excluded (%d):" % len(invalid))
for r in sorted(invalid, key=lambda x: x["run"]):
    print("  %-18s ver=%-4s -> %s" % (r["run"], r["recorder_version"], "; ".join(r["reasons"])[:90]))

if fails:
    print("\nFAIL (%d):" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("\nPASS: gate invariants hold; %d valid v5 runs; %s correctly excluded."
      % (len(valid), ", ".join(MUST_BE_INVALID)))
