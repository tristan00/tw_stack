r"""Offline test for `bus` (no game): the cross-process seq-safety guarantee.

N separate OS processes each append K commands to one shared cmd file via the locked
allocate+append. Correct = every command line gets a UNIQUE seq that strictly increases in file
order (no collision). Without the _ProcLock, concurrent clients seed from the same file max and
collide -> the mod's strict-increase gate would drop the duplicate -> a 30s timeout. This is the
m0 "bus contention" bug; this test proves the lock fixes it.

    python test_bus.py
"""
import os
import subprocess
import sys
import tempfile

import bus

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "_bus_worker.py")
N, K = 5, 40                      # 5 concurrent processes x 40 appends = 200 commands

tmp = tempfile.mkdtemp(prefix="bus_seqtest_")
cmd = os.path.join(tmp, "commands.txt")
out = os.path.join(tmp, "twcontrol.jsonl")
open(out, "w").close()
with open(cmd, "w", encoding="utf-8") as f:      # a malformed line the parser must skip
    f.write("garbage not a command line\n")

procs = [subprocess.Popen([sys.executable, WORKER, cmd, out, str(K)]) for _ in range(N)]
rc = [p.wait() for p in procs]

seqs = []
for line in open(cmd, encoding="utf-8"):
    m = bus._CMD_RE.match(line)
    if m:
        seqs.append(int(m.group(1)))

fails = []
if any(r != 0 for r in rc):
    fails.append("a worker process exited non-zero: %s" % rc)
if len(seqs) != N * K:                                   # malformed line skipped -> exactly N*K
    fails.append("expected %d command lines, got %d (malformed line not skipped?)" % (N * K, len(seqs)))
if len(set(seqs)) != len(seqs):
    from collections import Counter
    dups = [s for s, c in Counter(seqs).items() if c > 1]
    fails.append("COLLISION: duplicate seqs %s" % sorted(dups)[:10])
if any(seqs[i] <= seqs[i - 1] for i in range(1, len(seqs))):
    fails.append("seqs not strictly increasing in file order")

print("%d processes x %d appends -> %d command lines, %d unique, range %s..%s"
      % (N, K, len(seqs), len(set(seqs)), min(seqs) if seqs else None, max(seqs) if seqs else None))
if fails:
    print("\nFAIL:")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("PASS: %d concurrent processes allocated %d unique, strictly-increasing seqs -- no collision."
      % (N, len(seqs)))
