r"""Live test for `bus` (needs a running game at a loaded campaign -- e.g. via the launcher).

Round-trips real commands through the mod: eval arithmetic, the local faction, the turn number.
Asserts the reply shape, correctness, and that seqs strictly increase across sends.

    python test_bus_live.py
"""
import sys

import bus

b = bus.Bus()
fails = []

r1 = b.send("eval", "return 1+1", timeout=25)
if r1.get("result") != 2 or r1.get("error"):
    fails.append("eval 1+1 -> %r" % r1)

r2 = b.send("eval", "return cm:get_local_faction_name(true)", timeout=25)
if not isinstance(r2.get("result"), str) or not r2["result"]:
    fails.append("faction eval -> %r" % r2)

r3 = b.send("eval", "return cm:model():turn_number()", timeout=25)
if not isinstance(r3.get("result"), int):
    fails.append("turn eval -> %r" % r3)

seqs = [r1.get("seq"), r2.get("seq"), r3.get("seq")]
if None in seqs or seqs != sorted(seqs) or len(set(seqs)) != 3:
    fails.append("seqs not unique+increasing: %s" % seqs)

print("eval(1+1)=%s  faction=%s  turn=%s  (seqs %s)"
      % (r1.get("result"), r2.get("result"), r3.get("result"), seqs))
if fails:
    print("\nFAIL:")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("PASS: bus round-trips against the live game (eval/faction/turn), seqs strictly increase.")
