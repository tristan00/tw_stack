from __future__ import annotations

"""Drive the game directly and check that a chosen lord's state fields actually populate.

No session, no advisor, no runctl. Launch one faction, call collect.snapshot(bus) by hand,
and read the fields. This is the only way to tell "the game says no" from "the read is
broken" -- a random run cannot do it, because the fields that matter here only exist in
states random play essentially never reaches.

WHY IT TAKES A FACTION. Some state is a property of the LORD, not the race: `horde_slots`
is read off that character's own MilitaryForceContext.IsHorde, and a race can field both
horde and non-horde lords. So "sample enough campaigns and it will turn up" is not a test,
it is a hope. Each entry below names a faction whose STARTING lord is known to have the
property, and asserts the field is non-empty.

Measured on a cold random run of three campaigns: 25 of 26 state fields populated from
ordinary play. The residue is what this exists for.

    python -m decisions.probe_state                     -- every faction in EXPECTS
    python -m decisions.probe_state <faction_key> ...   -- just those
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import common  # noqa: E402

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)
sys.path.insert(0, common.DECISIONS)

EXPECTS = {
    "wh3_dlc25_dwf_malakai": [
        ("lord", "horde_slots",
         "Malakai's army is a horde; his faction's other lords are not"),
    ],
    # The control: Beastmen have no settlements and every army is a horde army, so this
    # must populate on turn 1 for every lord.
    "wh_dlc03_bst_beastmen": [
        ("lord", "horde_slots", "all Beastmen armies are horde armies"),
    ],
    "wh2_dlc17_dwf_thorek_ironbrow": [
        ("lord", "equipped", "Thorek starts carrying unique rune ancillaries"),
    ],
}

FAILED = []


def check(cond, what, detail=""):
    print("  %-4s %-46s %s" % ("ok" if cond else "FAIL", what, detail))
    if not cond:
        FAILED.append(what)


def probe(faction, wants):
    from bus import Bus
    from bus_launcher import BusLauncher
    import collect

    print("\n== %s" % faction)
    lz = BusLauncher()
    state = lz.launch(faction)
    print("   launched: %s" % json.dumps(state, default=str)[:160])
    bus = Bus()
    snap = collect.snapshot(bus)

    ents = snap.get("entities") or []
    kinds = {}
    for e in ents:
        kinds.setdefault(e.get("context_kind"), []).append(e.get("state") or {})
    print("   entities: %s"
          % ", ".join("%s=%d" % (k, len(v)) for k, v in sorted(kinds.items())))
    check(not any("offers" in e for e in ents),
          "the snapshot carries state and no offers")

    for kind, field, why in wants:
        rows = kinds.get(kind) or []
        hit = [r for r in rows if r.get(field) not in (None, [], {}, "")]
        check(bool(hit), "%s.%s populates" % (kind, field),
              "%d of %d %s -- %s" % (len(hit), len(rows), kind, why[:70]))
        if hit:
            print("        e.g. %s" % json.dumps(hit[0][field], default=str)[:150])
    return snap


def main(argv):
    want = [a for a in argv if not a.startswith("--")] or sorted(EXPECTS)
    unknown = [f for f in want if f not in EXPECTS]
    if unknown:
        raise SystemExit("no expectation recorded for %s -- add it to EXPECTS with the "
                         "reason that faction is the right probe" % unknown)
    for faction in want:
        try:
            probe(faction, EXPECTS[faction])
        except Exception as e:
            check(False, "probe %s" % faction, repr(e)[:140])
    print("\n%s" % ("state probe OK" if not FAILED else "%d FAILED" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
