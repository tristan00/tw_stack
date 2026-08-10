from __future__ import annotations

"""What must stay true of v3, checked mechanically.

v2 was not dropped because it was slow. It was dropped because it was a graph in name
only: the candidate action was a feature vector rather than a node, the loss broadcast
one graph-level scalar onto every candidate so no term ever compared two candidates, and
CatBoost columns were stamped into the record before the graph was built.

Optimisation work is exactly the situation in which those properties get traded away
quietly -- a conv that is 1.5x cheaper but cannot tell which relation attached to which
neighbour still trains, still reports a loss, and still looks fine. So each upgrade gets
a mechanical check here, and speed work has to keep them all green.

    python -m mapgraph3.invariants
"""

import ast
import inspect
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if not os.path.isdir(os.path.join(_ROOT, "decisions")):
    _ROOT = r"D:\tw_stack"
for _p in (os.path.join(_ROOT, "decisions"), os.path.join(_ROOT, "advisor"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _code_only(path):
    """Source with docstrings removed.

    train.py's own docstring says "no mse(q, y_z)" and names the v2 functions that are
    NOT called. Grepping the prose that documents an absence is not evidence about the
    code, so the checks below run against executable statements only.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
            # a class or def whose entire body was the docstring still needs a body,
            # or ast.unparse emits something that will not re-parse
            if not node.body and not isinstance(node, ast.Module):
                node.body = [ast.Pass()]
    return ast.unparse(tree)


def check(verbose=True):
    import torch
    try:
        from mapgraph3 import net as N
        from mapgraph3 import schema as S
    except ImportError:
        import net as N
        import schema as S

    src_net = open(os.path.join(_HERE, "net.py"), encoding="utf-8").read()
    code_trn = _code_only(os.path.join(_HERE, "train.py"))
    results = []

    def ok(name, cond, detail=""):
        results.append((bool(cond), name, detail))

    # 1. the candidate action is a NODE and q reads that node alone after message
    #    passing -- nothing pooled or concatenated back in. This is the whole point.
    head = inspect.getsource(N.Head.forward)
    q_line = [l for l in head.splitlines() if l.strip().startswith("q =")][0]
    ok("action is a node; q reads h[action_index] alone",
       "self.q(h[data.action_index])" in q_line and "cat" not in q_line, q_line.strip())

    # 2. the loss compares candidates within a decision
    nll = inspect.getsource(N.listwise_nll)
    ok("listwise NLL over each decision's candidate set",
       "softmax(q, a_graph" in nll and "-torch.log" in nll)

    # 3. no mse(q, y): v2's q and v regressed to the same scalar, so q-v was zero by
    #    construction and non-zero only where v underfit
    mse = [ast.unparse(n) for n in ast.walk(ast.parse(code_trn))
           if isinstance(n, ast.Call) and "mse" in ast.unparse(n.func).lower()]
    ok("no mse(q, ...); only mse(v, y_z)",
       all(not c.replace(" ", "").split("(", 1)[1].startswith("q,") for c in mse),
       "; ".join(mse) or "none")

    # 4. advantage weighting: what makes this policy improvement, not plain imitation
    ok("advantage weight exp((y_z - v)/tau), clipped",
       "b.y_z - v.detach()" in code_trn and "adv_clip" in code_trn)

    # 5. the ban: no mapgraph3 module may import advisor/features.py, and nothing may
    #    call v2's CatBoost-stamping helpers.
    #    Static, across the whole package. A runtime sys.modules check (guard.py used to
    #    have one) cannot do this job: the LABEL legitimately goes through base_model,
    #    which imports features, so after the first walk `features` is loaded no matter
    #    how clean mapgraph3 is. Only the import graph distinguishes "we use the label"
    #    from "we transcribed the feature set".
    banned_calls, importers = [], []
    for fn in sorted(os.listdir(_HERE)):
        # skip this file: it names the banned helpers in order to look for them
        if not fn.endswith(".py") or fn == os.path.basename(__file__):
            continue
        code = _code_only(os.path.join(_HERE, fn))
        for b in ("stamp_prev_actions", "stamp_action_counts"):
            if b in code:
                banned_calls.append("%s:%s" % (fn, b))
        for n in ast.walk(ast.parse(code)):
            mods = ([a.name for a in n.names] if isinstance(n, ast.Import)
                    else ([n.module or ""] if isinstance(n, ast.ImportFrom) else []))
            if any(m.split(".")[-1] == "features" for m in mods):
                importers.append(fn)
    ok("no mapgraph3 module imports advisor/features.py",
       not banned_calls and not importers,
       "importers: %s  banned calls: %s" % (importers or "none", banned_calls or "none"))

    # 6. entity-only layers before actions join, so the map embedding cannot depend on
    #    which offers the generator happened to emit
    enc = inspect.getsource(N.Encoder.forward)
    ok("entity-only phase runs before any action round",
       "for _ in range(self.entity_layers)" in enc
       and enc.index("for _ in range(self.entity_layers)")
       < enc.index("for r in range(self.action_rounds)"))

    # 7. the a2e gate starts at zero AND the conv returns a pure message, so a node with
    #    no incoming action edge is genuinely unperturbed. A conv that returns
    #    f(own_state) instead of 0 makes the gate's gradient come mostly from nodes the
    #    channel cannot reach, and the printed gate stops meaning anything.
    e = N.Encoder(64, 2, 2)
    conv = N.RelConv(8, S.REL_DIM, "mean")
    empty = conv(torch.randn(5, 8), torch.zeros((2, 0), dtype=torch.long),
                 torch.zeros((0, S.REL_DIM)))
    ok("a2e gate initialised to zero", bool(torch.all(e.a2e_gate == 0)))
    ok("RelConv returns a pure message (exactly 0 with no edges)",
       bool(torch.all(empty == 0)), "max|out|=%.3g" % float(empty.abs().max()))

    # 8. relation identity is bound to its endpoint INSIDE the message. If the message
    #    is linear in (source, relation) the two sums decouple and "besieging S while
    #    garrisoned in T" is indistinguishable from the swap.
    msg = inspect.getsource(N.RelConv.message)
    ok("message conditions on x_i, x_j and rel jointly",
       all(t in msg for t in ("x_i", "x_j", "rel", "cat")),
       msg.strip().splitlines()[-1].strip())

    # 9/10. per-node-type parameters, and the schema breadth v2 did not have
    ok("per-node-type encoders and norms",
       "F.embedding(node_type, self.bias)" in src_net
       and "F.embedding(node_type, self.affine)" in src_net)
    ok("schema keeps the full node-type set",
       len(S.NODE_TYPES) >= 15,
       "%d node types, %d relations" % (len(S.NODE_TYPES), S.N_RELATIONS))

    if verbose:
        for good, name, detail in results:
            print("  %s  %-48s %s" % ("PASS" if good else "FAIL", name, detail))
    return [r for r in results if not r[0]]


if __name__ == "__main__":
    print("mapgraph3 v3 invariants")
    bad = check()
    print("\n%s" % ("all invariants hold" if not bad else
                    "%d INVARIANT(S) BROKEN" % len(bad)))
    raise SystemExit(1 if bad else 0)
