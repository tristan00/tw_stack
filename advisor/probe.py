r"""Live menu-probe over the command bus. Use during a play session (WH3 running with the mod loaded)
to inspect whatever menu is open and figure out, empirically, how to collect its data.

The mod already exposes these bus commands (mod/twcontrol.lua): roots, tree, find, eval. This wraps them.
NOTHING changes in the game -- this only reads. Once a probe proves how to get a menu's data, we bake
that method into capture as a targeted change.

    python probe.py roots                     # list the open UI roots (which panels are up)
    python probe.py tree  <path>              # deep-sweep a panel: every node + label/tooltip/state/ctx
    python probe.py find  <path>              # one node's immediate children (ids only)
    python probe.py eval  "<lua expr>"        # run a CCO/game-API expression live (resolve ids -> keys)

`path` is a pipe- or '>'-separated UIComponent path from a root, e.g.
    main > dilemma  OR  root|events|event_layouts|dilemma_active
Copy paths straight out of a `tree`/`roots` dump.
"""
import os
import sys

sys.path.insert(0, r"D:/tw_stack/bus")
from bus import Bus  # noqa: E402

_b = Bus()


def _norm(path):
    return path.replace(">", "|").replace(" ", "").strip()


def roots():
    return _b.send("roots", "")


def tree(path):
    return _b.send("tree", _norm(path))


def find(path):
    return _b.send("find", _norm(path))


def ev(expr):
    return _b.send("eval", expr)


def _short(s, n=44):
    if s is None:
        return ""
    s = str(s).replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "\u2026"


def print_tree(reply):
    """Pretty per-node view of a tree reply: path-tail | state | text | tooltip | context."""
    if not reply:
        print("(no reply -- is the game up + mod loaded + a menu open?)"); return
    nodes = reply.get("nodes") or []
    print(f"tree path={reply.get('path')} count={len(nodes)} truncated={reply.get('truncated')} turn={reply.get('turn')}")
    for nd in nodes:
        pid = nd.get("id") or ""
        tail = (nd.get("path") or "").split("|")[-1]
        txt = _short(nd.get("text"))
        lab = _short(nd.get("text_label"), 24)
        tip = _short(nd.get("tooltip"), 50)
        st = nd.get("state") or ""
        ctx = nd.get("context") or ""
        cols = [f"{tail:32.32s}", f"{st:10.10s}"]
        if txt:
            cols.append(f"txt={txt}")
        if lab:
            cols.append(f"lab={lab}")
        if tip:
            cols.append(f"tip={tip}")
        if ctx:
            cols.append(f"ctx={ctx}")
        print("  " + " | ".join(cols))


def main(argv):
    if not argv:
        print(__doc__); return
    cmd = argv[0]
    if cmd == "roots":
        r = roots()
        print("roots:", r.get("kids") if r else r)
    elif cmd == "tree":
        print_tree(tree(" ".join(argv[1:])))
    elif cmd == "find":
        r = find(" ".join(argv[1:]))
        print("find:", r)
    elif cmd == "eval":
        r = ev(" ".join(argv[1:]))
        print("eval:", (r or {}).get("result") if isinstance(r, dict) else r)
    else:
        print("unknown cmd; run with no args for help")


if __name__ == "__main__":
    main(sys.argv[1:])
