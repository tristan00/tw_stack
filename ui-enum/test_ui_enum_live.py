r"""Live test for ui-enum (needs a running campaign, e.g. via `launcher`).

Enumerate hud_campaign and assert it yields real options including known HUD children.

    python test_ui_enum_live.py
"""
import sys

import ui_enum

out = ui_enum.enumerate_panel("hud_campaign")
ids = {o["id"] for o in out["options"]}
fails = []

if not out["found"] or out["count"] == 0:
    fails.append("no options enumerated: %r" % out)
for expect in ("faction_buttons_docker", "radar_things"):
    if expect not in ids:
        fails.append("expected HUD child %r not enumerated" % expect)

print("hud_campaign -> %d options" % out["count"])
print("  sample ids:", sorted(ids)[:10])
faction_opt = [o for o in out["options"] if o["key"].startswith("wh") and "hef" in o["key"]]
if faction_opt:
    print("  faction context resolved:", faction_opt[0]["key"])

if fails:
    print("\nFAIL:")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("\nPASS: ui-enum enumerated hud_campaign into %d options incl. known children." % out["count"])
