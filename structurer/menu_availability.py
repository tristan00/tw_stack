r"""Data view: EVERY menu open, with which options were AVAILABLE vs NOT available.

Reads the run's ui_components.jsonl (the menu scrapes captured by the ui-capture stream). For each
menu_open, emits one dataset row per option with its availability:
    available      = clickable is True   (selectable)
    not_available  = clickable is False  (shown but locked/disabled -- e.g. needs a building/tech)
    unknown        = clickable is null    (unread / virtualized / indeterminate)

    python menu_availability.py <run_dir> [out.jsonl]

Writes a JSONL dataset (one row per menu-option) and prints a per-panel available/not/unknown summary.
"""
import json
import os
import sys
from collections import Counter, defaultdict


def main():
    run = sys.argv[1]
    ui = os.path.join(run, "ui_components.jsonl")
    exports = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
    os.makedirs(exports, exist_ok=True)
    run_name = os.path.basename(run.rstrip("/\\"))
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(exports, "menus_%s.jsonl" % run_name)

    menu_seq = 0
    nrows = 0
    _bad = 0
    per_panel = defaultdict(Counter)
    with open(out, "w", encoding="utf-8") as w:
        for line in open(ui, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                _bad += 1           # hot per-line parse: count, don't log each
                continue
            if r.get("kind") != "menu_open":
                continue
            menu_seq += 1
            panel = r.get("panel")
            for o in r.get("options", []):
                c = o.get("clickable")
                avail = "available" if c is True else ("not_available" if c is False else "unknown")
                per_panel[panel][avail] += 1
                w.write(json.dumps({
                    "menu_seq": menu_seq, "t": r.get("t"), "panel": panel,
                    "option_id": o.get("id"), "key": o.get("key"), "source": o.get("source"),
                    "state": o.get("state"), "visible": o.get("visible"), "available": avail,
                }, ensure_ascii=False) + "\n")
                nrows += 1

    if _bad:
        sys.stderr.write("menu_availability: skipped %d malformed lines\n" % _bad)
    print("wrote %d menu-option rows across %d menu opens -> %s" % (nrows, menu_seq, out))
    print("--- per panel:  available / not_available / unknown ---")
    for panel, c in sorted(per_panel.items()):
        print("   %-22s available=%-4d  not_available=%-4d  unknown=%-4d"
              % (panel, c["available"], c["not_available"], c["unknown"]))


if __name__ == "__main__":
    main()
