r"""Offline test for ui-enum (no game): parse a canned bus `find` result into correct options.

    python test_ui_enum.py
"""
import sys

import ui_enum

# a find-result modeled on a real hud_campaign response (contexts + row/card ids)
RESULT = {"cmd": "find", "path": "hud_campaign",
          "child_ids": ["faction_buttons_docker", "radar_things",
                        "faction_row_entry_wh2_main_hef_nagarythe", "wh_main_inf_x_recruitable"],
          "child_contexts": [None, None, "CcoCampaignFaction:wh2_main_hef_nagarythe", None]}

out = ui_enum.parse_find(RESULT)
byid = {o["id"]: o for o in out["options"]}
fails = []

if out["count"] != 4:
    fails.append("count %d != 4" % out["count"])
if byid.get("faction_row_entry_wh2_main_hef_nagarythe", {}).get("key") != "wh2_main_hef_nagarythe":
    fails.append("context/row key wrong: %r" % byid.get("faction_row_entry_wh2_main_hef_nagarythe"))
if byid.get("wh_main_inf_x_recruitable", {}).get("key") != "wh_main_inf_x":
    fails.append("recruitable wrapper not stripped: %r" % byid.get("wh_main_inf_x_recruitable"))
if byid.get("radar_things", {}).get("key") != "radar_things":
    fails.append("plain id should pass through unchanged")
if ui_enum.parse_find({"path": "x"})["found"] is not False:
    fails.append("a result with no child list should report found=False")

print("parsed %d options:" % out["count"])
for o in out["options"]:
    print("    %-45s -> %s" % (o["id"], o["key"]))
if fails:
    print("\nFAIL:")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("\nPASS: ui-enum parses find results into options with correct stable keys.")
