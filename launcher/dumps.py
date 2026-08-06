from __future__ import annotations
import collections
import json
import os
import sys

_T = 8.0
_TREE_T = 30.0


def _warn(where, exc):
    sys.stderr.write("dumps: %s -> %s\n" % (where, repr(exc)[:120]))


def _ev(bus, lua):
    try:
        return (bus.send("eval", lua, timeout=_T) or {}).get("result")
    except Exception as exc:
        _warn("eval(%s)" % lua[:48], exc)
        return None


def _recover_hidden(bus, nodes, cap=80):
    out = []
    hidden = [n for n in nodes if n.get("visible") is False and n.get("path")]
    for n in hidden[:cap]:
        try:
            f = bus.send("find", n["path"], timeout=_T) or {}
        except Exception as exc:
            _warn("find(%s)" % str(n.get("path"))[:50], exc)
            continue
        cids = [c for c in (f.get("child_ids") or []) if c]
        if cids:
            out.append({"path": n["path"], "child_ids": cids,
                        "child_contexts": [c for c in (f.get("child_contexts") or []) if c]})
    return out


def dump_tree(bus, root, depth=40, nodes=16000):
    try:
        tr = bus.send("tree", "%s %d %d" % (root, depth, nodes), timeout=_TREE_T) or {}
    except Exception as exc:
        return {"nodes": [], "truncated": None, "error": repr(exc)[:80]}
    nds = tr.get("nodes") or []
    ctx = collections.Counter(str(n["context"]).split(":")[0] for n in nds if n.get("context"))
    return {
        "nodes": nds,
        "truncated": bool(tr.get("truncated")),
        "n": len(nds),
        "with_context": sum(1 for n in nds if n.get("context")),
        "context_types": dict(ctx),
        "id_hist": dict(collections.Counter(str(n.get("id")) for n in nds).most_common(30)),
    }


def dump_screen(bus, log_path=None, save_dir=None, deep=True, depth=40, nodes=16000, log_tail=400):
    D = {
        "roots": [], "open_panels": [], "panels": {}, "entities": {}, "state": {},
        "log_tail": None, "filter_hits": [],
        "caps_known": {"tree_default_nodes": 500, "tree_visible_only_descent": True,
                       "context_field_curated_types_only": True},
    }
    try:
        rr = bus.send("roots", "", timeout=_T) or {}
    except Exception as exc:
        D["filter_hits"].append("roots failed: %s" % repr(exc)[:80])
        rr = {}
    kids = rr.get("kids") or []
    D["roots"] = [{"id": k.get("id"), "visible": k.get("visible"), "children": k.get("children")}
                  for k in kids]
    D["open_panels"] = [k.get("id") for k in kids if k.get("visible") and k.get("id")]

    for pan in D["open_panels"]:
        t = dump_tree(bus, pan, depth, nodes)
        nds = t.pop("nodes")
        if t.get("truncated"):
            D["filter_hits"].append("panel '%s': tree hit the %d-node budget -> INCOMPLETE "
                                    "(raise nodes= or subdivide)" % (pan, nodes))
        if deep:
            hid = _recover_hidden(bus, nds)
            t["hidden_recovered"] = hid
            if hid:
                D["filter_hits"].append("panel '%s': %d hidden subtree(s) the visible-only tree "
                                        "skipped (recovered 1 level via find)" % (pan, len(hid)))
        D["panels"][pan] = t
        if save_dir:
            try:
                os.makedirs(save_dir, exist_ok=True)
                with open(os.path.join(save_dir, "tree_%s.json" % pan), "w", encoding="utf-8") as f:
                    json.dump(nds, f)
            except Exception as exc:
                _warn("save tree_%s.json" % pan, exc)

    for ch, key in (("chars", "chars"), ("setts", "setts"), ("forces", "forces"), ("hostiles", "hostiles")):
        try:
            r = bus.send(ch, "", timeout=_T) or {}
            D["entities"][ch] = r.get(key) or []
        except Exception as exc:
            D["entities"][ch] = {"error": repr(exc)[:60]}
    try:
        D["state"]["snapshot"] = bus.send("snapshot", "", timeout=_T) or {}
    except Exception as exc:
        _warn("snapshot", exc)
        D["state"]["snapshot"] = None
    D["state"]["faction"] = _ev(bus, "cm:get_local_faction_name(true)")
    D["state"]["turn"] = _ev(bus, "cm:turn_number()")
    D["state"]["treasury"] = _ev(bus, "cm:get_faction(cm:get_local_faction_name(true)):treasury()")
    D["state"]["at_war"] = _ev(bus, "cm:get_local_faction(true):at_war()")
    D["state"]["regions"] = _ev(bus, "cm:get_local_faction(true):region_list():num_items()")

    if log_path and os.path.isfile(log_path):
        try:
            D["log_tail"] = open(log_path, encoding="utf-8", errors="replace").read().splitlines()[-log_tail:]
        except Exception as exc:
            _warn("log_tail(%s)" % log_path, exc)

    if save_dir:
        try:
            os.makedirs(save_dir, exist_ok=True)
            with open(os.path.join(save_dir, "dump.json"), "w", encoding="utf-8") as f:
                json.dump(D, f, indent=1)
        except Exception as exc:
            _warn("save dump.json", exc)
    return D


if __name__ == "__main__":
    import sys
    sys.path.insert(0, r"D:\tw_stack\bus")
    sys.path.insert(0, r"D:\tw_stack\launcher")
    from bus import Bus
    out = sys.argv[1] if len(sys.argv) > 1 else None
    d = dump_screen(Bus(), save_dir=out)
    print("open_panels:", d["open_panels"])
    print("state:", d["state"].get("faction"), "turn", d["state"].get("turn"))
    for p, r in d["panels"].items():
        print("  panel %-24s nodes=%s truncated=%s ctx=%s hidden=%s"
              % (p, r.get("n"), r.get("truncated"), r.get("context_types"),
                 len(r.get("hidden_recovered") or [])))
    print("FILTER HITS:", d["filter_hits"] or "(none -- complete capture)")
