from __future__ import annotations

"""Every stored option must be regenerable from the stored state.

This is the property the whole recorder/advisor split rests on. The recorder writes state;
the advisor builds the move universe from that state and hands back the survivors. If an
option is in the database that `options.generate` cannot produce from the same record,
then the state is INCOMPLETE -- the generator used something at collection time that was
never written down, and every later reader (features, the graph, a retrain) is working
from a record that cannot explain its own contents.

That failure is silent by construction. The corpus looks fine, training runs, and the
model learns from options whose provenance is missing. So it is checked rather than
assumed:

    for every decision:  stored options  ⊆  options.generate(record)

Gating is deliberately NOT compared. It is intra-turn stateful -- caps, retirement,
blacklists all depend on what happened earlier in that turn -- so the same record gated at
replay would not reproduce the live decision, and pretending otherwise would make this
check lie. Generation is a pure function of the record; that is what is checked.

    python -m advisor.replay_options [run_dir] [--n 200]
"""

import collections
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common  # noqa: E402

sys.path.insert(0, common.DECISIONS)


def main(argv):
    import options as O
    from store import DecisionStore

    run = common.cli_path(argv, ("--n",)) or common.RUN_DIR
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 200

    st = DecisionStore(run, readonly=True)
    try:
        hi = st.max_decision_id()
        if not hi:
            print("no decisions in %s -- nothing to replay" % run)
            return 1
        lo = max(1, hi - n + 1)
        checked = missing = stored = 0
        by_type = collections.Counter()
        examples = []
        for did in range(lo, hi + 1):
            try:
                rec = st.read_decision(did)
            except KeyError:
                continue
            have = {(ck, cid, o["action_type"], str(o["key"]))
                    for ck, cid, o in [(e["context_kind"], str(e["context_id"]), o)
                                       for e in rec["entities"]
                                       for o in e.get("offers") or []]}
            if not have:
                continue
            checked += 1
            stored += len(have)
            gen = {(ck, cid, o.get("action_type"), str(o.get("key")))
                   for ck, cid, o in O.generate(rec)}
            gap = have - gen
            for row in gap:
                missing += 1
                by_type[row[2]] += 1
                if len(examples) < 6:
                    examples.append(row)
    finally:
        st.close()

    print("decisions replayed : %d" % checked)
    print("options stored     : %d" % stored)
    print("NOT regenerable    : %d (%.2f%%)"
          % (missing, 100.0 * missing / max(stored, 1)))
    if by_type:
        print("\nby action type:")
        for at, c in by_type.most_common(12):
            print("   %-24s %d" % (at, c))
        print("\nexamples:")
        for e in examples:
            print("   %s" % (e,))
    print("\n%s" % ("every stored option regenerates from the stored state"
                    if not missing else
                    "%d STORED OPTION(S) THE STATE CANNOT EXPLAIN" % missing))
    return 1 if missing else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
