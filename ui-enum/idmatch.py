r"""twapi.idmatch -- ONE shared key-embedded-id matcher (plan W-IDMATCH).

The same "recover the bare game key from a wrapped UI component Id" logic was copied into three
sites: tools/ui_component_recorder._extract_key, twapi/verbs/build.py (card-id matching), and
twapi/access.find_row (row_prefix + entity_id). This module is the single source; each site delegates.
Pure + offline-testable (differential test in this module's __main__ vs the originals).
"""
from __future__ import annotations

import re

_RITUAL_RE = re.compile(r"^Cco[A-Za-z]+\d+(wh\d?_.+)$")   # CcoCampaignRitual30<wh..key> -> wh..key
_CCO_NUM_RE = re.compile(r"^Cco[A-Za-z]+(\d+)$")          # CcoCampaignAncillary137 -> 137
_ROW_PREFIXES = ("faction_row_entry_", "row_entry_", "character_row_")


def extract_key(component_id: str) -> str:
    """Best-effort BARE game key from a key-embedded component Id.

    Strips row/card wrappers so the key is the entity's own id: a trailing cqi for a character row,
    the province/faction key for a list row, the unit key for a recruit card, a Cco* record's numeric
    id -- and a wh*_skill_/wh*_tech_ leaf IS already its own key.

    Examples: "character_row_1042"->"1042", "row_entry_wh3_main_combi_province_qiang"->
    "wh3_main_combi_province_qiang", "wh_main_inf_x_recruitable"->"wh_main_inf_x",
    "CcoCampaignAncillary137"->"137". Returns the Id itself when no wrapper is recognised.
    """
    s = component_id
    if s.endswith("_recruitable"):
        s = s[:-len("_recruitable")]
    for pre in _ROW_PREFIXES:
        if s.startswith(pre):
            return s[len(pre):]
    m = _RITUAL_RE.match(s)     # ritual key BEFORE the numeric-tail rule (Cco...137 with no wh key)
    if m:
        return m.group(1)
    m = _CCO_NUM_RE.match(s)
    if m:
        return m.group(1)
    return s


def row_id(row_prefix: str, entity_id) -> str:
    """The full list-row component Id for an entity: `<row_prefix><entity_id>` (access.find_row's
    match target). Kept here so the row-id convention lives beside extract_key (its inverse)."""
    return "%s%s" % (row_prefix, entity_id)


def matches_entity(component_id: str, entity_key: str) -> bool:
    """True when a component Id resolves to (or ends with) the given bare entity key -- the shared
    predicate behind find_row's prefix match and build.py's card match."""
    return extract_key(component_id) == entity_key or component_id.endswith(entity_key)


if __name__ == "__main__":
    # differential test: extract_key MUST equal the original ui_component_recorder._extract_key on the
    # documented cases + a real ui_components.jsonl corpus of component ids.
    import json
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
    from ui_component_recorder import _extract_key as ORIG  # the original

    cases = ["character_row_1042", "row_entry_wh3_main_combi_province_qiang",
             "wh_main_inf_x_recruitable", "CcoCampaignAncillary137",
             "CcoCampaignRitual30wh3_dlc27_ritual_x", "wh_main_skill_all_lord_campaign",
             "faction_row_entry_wh2_main_hef_nagarythe", "button_end_turn", ""]
    corpus = list(cases)
    # pull real ids from any available ui_components.jsonl
    import glob
    for p in glob.glob("D:/twdata/runs/human/*/ui_components.jsonl")[:3]:
        try:
            for line in open(p, encoding="utf-8", errors="replace"):
                try:
                    j = json.loads(line)
                except ValueError:
                    continue  # intentional: skip malformed line, per-line corpus parse (no per-line log)
                for nd in (j.get("nodes") or []):
                    if nd.get("id"):
                        corpus.append(nd["id"])
                for o in (j.get("options") or []):
                    if o.get("id"):
                        corpus.append(o["id"])
        except OSError as e:
            sys.stderr.write("idmatch: cannot read %s -> %s\n" % (p, repr(e)[:60]))
    mism = [(c, extract_key(c), ORIG(c)) for c in corpus if extract_key(c) != ORIG(c)]
    print("differential test: %d ids checked, %d mismatches vs original" % (len(corpus), len(mism)))
    for c, a, b in mism[:10]:
        print("  MISMATCH id=%r new=%r orig=%r" % (c, a, b))
    print("W-IDMATCH extract_key:", "IDENTICAL to original" if not mism else "MISMATCH")
