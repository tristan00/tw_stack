from __future__ import annotations

"""Battle-result extraction, checked against every archived dump we have.

The battle screen is the one place UI scraping is unavoidable -- all ten table cells and
both faction-name nodes carry `context: null` in every archived dump, so no structured read
exists. That makes the parse itself the thing that can silently rot, and it rots per RACE:
the resources bar differs for every race, and even the treasury path moves.

So this runs the real extractor over real dumps from 10 races and asserts the parts that
must hold everywhere. It needs no game.

    python -m launcher.test_battle_facts
"""

import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

DUMP_GLOBS = (
    r"D:\twdata\archive\**\screens\*battle_results*.json",
    r"D:\twdata\agent_demo_*\tree_change_popup_battle_results_*.json",
    r"D:\twdata\panel_capture\*_panel_popup_battle_results.json",
    r"D:\twdata\runs\human\screens\*battle_results*.json",
)


def _nodes_of(path):
    """Three wrapper shapes exist in the archive; normalise before parsing."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        doc = json.load(fh)
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        if isinstance(doc.get("nodes"), list):
            return doc["nodes"]
        tree = doc.get("tree")
        if isinstance(tree, dict) and isinstance(tree.get("nodes"), list):
            return tree["nodes"]
    return []


def main():
    from interrupts import battle_facts_from

    files = []
    for g in DUMP_GLOBS:
        files.extend(glob.glob(g, recursive=True))
    files = sorted(set(files))
    if not files:
        print("no archived battle-result dumps found -- nothing to check")
        return 0

    fails, parsed, outcomes, races = [], 0, {}, set()
    for f in files:
        nodes = _nodes_of(f)
        if len(nodes) < 50:
            continue                      # tooltip fragments, not a battle screen
        facts = battle_facts_from(nodes)
        base = os.path.basename(f)
        if not facts:
            fails.append("%s: extractor returned nothing" % base)
            continue
        parsed += 1

        if not facts.get("outcome"):
            fails.append("%s: no outcome string" % base)
        else:
            outcomes[facts["outcome"]] = outcomes.get(facts["outcome"], 0) + 1

        # exactly two positional rows, and never labelled ours/theirs
        if len(facts.get("rows") or []) != 2:
            fails.append("%s: %d table rows, expected 2"
                         % (base, len(facts.get("rows") or [])))
        for r in facts.get("rows") or []:
            for col in ("deployed", "lost", "remaining"):
                if not str(r.get(col) or "").strip():
                    fails.append("%s: row %s missing %s" % (base, r.get("row_id"), col))
            if "ours" in r or "player" in r:
                fails.append("%s: row claims a side -- attacker/defender is not "
                             "player/enemy and must be resolved downstream" % base)

        for n in nodes:
            c = str(n.get("context") or "")
            if c.startswith("CcoCampaignFaction:"):
                bits = c.split(":", 1)[1].split("_")
                if len(bits) > 2:
                    races.add(bits[2])

    print("dumps parsed        : %d of %d" % (parsed, len(files)))
    print("races represented   : %d  (%s)" % (len(races), ", ".join(sorted(races))))
    print("outcome vocabulary  : %s" % dict(sorted(outcomes.items())))
    if fails:
        print("\nFAILURES (%d):" % len(fails))
        for f in fails[:25]:
            print("   %s" % f)
        return 1
    print("\nbattle facts extract cleanly from every archived dump")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
