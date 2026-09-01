from __future__ import annotations


import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

_TD = common.native(common.TWDATA)
DUMP_GLOBS = tuple(os.path.join(_TD, p) for p in (
    os.path.join("archive", "**", "screens", "*battle_results*.json"),
    os.path.join("agent_demo_*", "tree_change_popup_battle_results_*.json"),
    os.path.join("panel_capture", "*_panel_popup_battle_results.json"),
    os.path.join("runs", "human", "screens", "*battle_results*.json"),
))
PREBATTLE_GLOBS = tuple(os.path.join(_TD, p) for p in (
    os.path.join("archive", "**", "screens", "*popup_pre_battle*.json"),
    os.path.join("runs", "human", "screens", "*popup_pre_battle*.json"),
))
CAPTURED_GLOBS = tuple(os.path.join(_TD, p) for p in (
    os.path.join("archive", "**", "screens", "*settlement_captured*.json"),
    os.path.join("runs", "human", "screens", "*settlement_captured*.json"),
))


def _nodes_of(path):
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


def _has_char_context(nodes, marker):
    return any(str(n.get("context") or "").startswith("CcoCampaignCharacter:")
               and marker in str(n.get("path") or "") for n in nodes)


def check_identity():
    from interrupts import combatant_identity, occupation_panel, prebattle_forecast

    fails = []
    pb_files = sorted(set(f for g in PREBATTLE_GLOBS for f in glob.glob(g, recursive=True)))
    parsed = with_ally = with_enemy = with_region = with_forecast = 0
    for f in pb_files:
        nodes = _nodes_of(f)
        if len(nodes) < 50:
            continue
        parsed += 1
        base = os.path.basename(f)
        ident = combatant_identity(nodes)
        if _has_char_context(nodes, "allies_combatants_panel") and not ident.get("ally_cqi"):
            fails.append("%s: allies panel has a character context but no ally_cqi" % base)
        if _has_char_context(nodes, "enemy_combatants_panel") and not ident.get("enemy_cqi"):
            fails.append("%s: enemy panel has a character context but no enemy_cqi" % base)
        with_ally += bool(ident.get("ally_cqi"))
        with_enemy += bool(ident.get("enemy_cqi"))
        with_region += bool(ident.get("region"))
        with_forecast += bool(prebattle_forecast(nodes))
    print("pre-battle dumps    : %d parsed / ally cqi %d / enemy cqi %d / region %d / "
          "forecast %d" % (parsed, with_ally, with_enemy, with_region, with_forecast))
    if parsed and (with_ally < parsed * 0.95 or with_enemy < parsed * 0.95):
        fails.append("pre-battle combatant cqi coverage below 95%%: ally %d / enemy %d of %d"
                     % (with_ally, with_enemy, parsed))

    sc_files = sorted(set(f for g in CAPTURED_GLOBS for f in glob.glob(g, recursive=True)))
    sc_parsed = sc_region = 0
    for f in sc_files:
        nodes = _nodes_of(f)
        if len(nodes) < 20:
            continue
        sc_parsed += 1
        sc_region += bool(occupation_panel(nodes).get("region"))
    print("captured dumps      : %d parsed / region %d" % (sc_parsed, sc_region))
    if sc_parsed and sc_region < sc_parsed * 0.9:
        fails.append("settlement_captured region coverage below 90%%: %d of %d"
                     % (sc_region, sc_parsed))
    return fails


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
            continue
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

        if _has_char_context(nodes, "allies_combatants_panel") and not facts.get("ally_cqi"):
            fails.append("%s: allies panel has a character context but no ally_cqi" % base)
        if _has_char_context(nodes, "enemy_combatants_panel") and not facts.get("enemy_cqi"):
            fails.append("%s: enemy panel has a character context but no enemy_cqi" % base)

        for n in nodes:
            c = str(n.get("context") or "")
            if c.startswith("CcoCampaignFaction:"):
                bits = c.split(":", 1)[1].split("_")
                if len(bits) > 2:
                    races.add(bits[2])

    fails.extend(check_identity())
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
