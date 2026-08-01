r"""Record a human-driven diplomacy deal. Read-only: polls the UI tree and never clicks.

    python diplo_capture.py [seconds]     -> D:/twdata/diplo_capture/<timestamp>.jsonl
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

from bus import Bus                                           # noqa: E402
import interrupts                                             # noqa: E402
import diplomacy as D                                         # noqa: E402

OUT_DIR = r"D:\twdata\diplo_capture"
POLL = 0.25

# pipe-joined treaty scalars for one faction
_LUA_TREATIES = (
    "local me=cm:get_faction(cm:get_local_faction_name(true)) "
    "if not me or me:is_null_interface() then return 'nofaction' end "
    "local them=cm:get_faction('%s') "
    "if not them or them:is_null_interface() then return 'notarget' end "
    "local function b(f) local ok,v=pcall(f) if not ok then return 'ERR' end return tostring(v) end "
    "return b(function() return me:at_war_with(them) end)..'|'.."
    "b(function() return me:allied_with(them) end)..'|'.."
    "b(function() return me:trade_agreement_with(them) end)..'|'.."
    "b(function() return me:is_vassal_of(them) end)..'|'.."
    "b(function() return them:is_vassal_of(me) end)..'|'.."
    "b(function() return cm:model():world():diplomacy_manager()"
    ":diplomatic_standing_between_factions(me:name(),them:name()) end)"
)


def treaties(bus, faction_key):
    if not faction_key:
        return None
    try:
        r = bus.send("eval", _LUA_TREATIES % faction_key, timeout=8.0) or {}
    except Exception as e:
        return {"error": repr(e)[:80]}
    if r.get("error"):
        return {"error": str(r["error"])[:120]}
    p = str(r.get("result") or "").split("|")
    if len(p) != 6:
        return {"raw": str(r.get("result"))[:120]}
    return {"at_war": p[0], "allied": p[1], "trade": p[2],
            "we_vassal_of_them": p[3], "they_vassal_of_us": p[4], "standing": p[5]}


def snapshot(bus):
    roots = interrupts.roots(bus)
    rec = {"ts": time.time(), "roots": roots, "panel_open": D.ROOT in roots}
    if not rec["panel_open"]:
        return rec, ("closed", tuple(sorted(roots)))
    nodes = []
    for n in D._tree(bus):
        nodes.append({"id": n.get("id"), "state": n.get("state"), "visible": n.get("visible"),
                      "text": n.get("text"), "path": n.get("path") or n.get("full_path"),
                      "x": n.get("x"), "y": n.get("y"), "w": n.get("w"), "h": n.get("h")})
    rec["nodes"] = nodes
    rec["n_nodes"] = len(nodes)

    by_id = {}
    for n in nodes:
        by_id.setdefault(str(n["id"] or ""), n)

    rec["offered_terms"] = {k[len("diplomatic_option_"):]: v["state"]
                            for k, v in by_id.items()
                            if k.startswith("diplomatic_option_") and v["visible"]}
    chance = by_id.get("label_deal_success_chance", {}).get("text")
    rec["success_chance_raw"] = chance
    send = by_id.get("button_send")
    rec["button_send_state"] = send.get("state") if send else None
    rec["button_send_visible"] = send.get("visible") if send else None
    sel = [k[len("faction_row_entry_"):] for k, v in by_id.items()
           if k.startswith("faction_row_entry_") and "select" in str(v.get("state") or "")]
    rec["selected_faction"] = sel[0] if sel else None

    # dedup signature
    sig = ("open", tuple(sorted(rec["offered_terms"].items())), chance,
           rec["button_send_state"], rec["selected_faction"], len(nodes))
    return rec, sig


def main():
    limit = float(sys.argv[1]) if len(sys.argv) > 1 else 900.0
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(OUT_DIR, "%s.jsonl" % stamp)
    bus = Bus()
    fh = io.open(path, "w", encoding="utf-8")

    def emit(rec):
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

    sys.stderr.write("diplo_capture -> %s   (%.0fs, poll %.2fs)\n" % (path, limit, POLL))
    emit({"ts": time.time(), "kind": "capture_opened", "poll": POLL, "limit": limit})

    last_sig, last_faction, n = None, None, 0
    t0 = time.time()
    heartbeat = 0.0
    while time.time() - t0 < limit:
        try:
            rec, sig = snapshot(bus)
        except Exception as e:
            emit({"ts": time.time(), "kind": "poll_error", "error": repr(e)[:200]})
            time.sleep(1.0)
            continue

        if sig != last_sig:
            rec["kind"] = "change"
            if rec.get("selected_faction") and rec["selected_faction"] != last_faction:
                rec["treaties_now"] = treaties(bus, rec["selected_faction"])
                last_faction = rec["selected_faction"]
            emit(rec)
            n += 1
            sys.stderr.write("[%3d] %s terms=%s chance=%s send=%s faction=%s\n"
                             % (n, "OPEN " if rec["panel_open"] else "CLOSED",
                                rec.get("offered_terms"), rec.get("success_chance_raw"),
                                rec.get("button_send_state"), rec.get("selected_faction")))
            last_sig = sig

        if time.time() - heartbeat > 15.0:
            heartbeat = time.time()
            emit({"ts": heartbeat, "kind": "heartbeat", "panel_open": rec.get("panel_open"),
                  "changes_so_far": n})
        time.sleep(POLL)

    if last_faction:
        emit({"ts": time.time(), "kind": "final_treaties", "faction": last_faction,
              "treaties": treaties(bus, last_faction)})
    emit({"ts": time.time(), "kind": "capture_closed", "changes": n})
    fh.close()
    sys.stderr.write("done: %d changes -> %s\n" % (n, path))


if __name__ == "__main__":
    main()
