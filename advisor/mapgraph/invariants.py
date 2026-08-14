from __future__ import annotations


import ast
import inspect
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import common

for _p in (common.DECISIONS, common.ADVISOR, common.ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _code_only(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
            if not node.body and not isinstance(node, ast.Module):
                node.body = [ast.Pass()]
    return ast.unparse(tree)


def _launcher_screens():
    path = os.path.join(common.LAUNCHER, "interrupts.py")
    tree = ast.parse(open(path, encoding="utf-8").read())

    def lits(node):
        if isinstance(node, ast.Constant):
            return {node.value} if isinstance(node.value, str) else set()
        if isinstance(node, ast.IfExp):
            return lits(node.body) | lits(node.orelse)
        if isinstance(node, ast.BoolOp):
            return set().union(*(lits(v) for v in node.values))
        return set()

    def resolve(arg, scope):
        got = lits(arg)
        if got or not isinstance(arg, ast.Name) or scope is None:
            return got
        for n in ast.walk(scope):
            if isinstance(n, ast.Assign) and any(
                    getattr(t, "id", None) == arg.id for t in n.targets):
                got |= lits(n.value)
        return got

    parent = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parent[c] = n

    def enclosing(node):
        p = parent.get(node)
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            p = parent.get(p)
        return p

    out = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fn = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        if fn in ("_choose", "_sticky_choice") and n.args:
            out |= resolve(n.args[0], enclosing(n))
        elif fn == "_drive_decision" and len(n.args) > 2:
            out |= resolve(n.args[2], enclosing(n))
    return out


def check_catalogue(verbose=True):
    try:
        from advisor.mapgraph import schema as S
        from advisor.mapgraph import catalogue as C
    except ImportError:
        import schema as S
        import catalogue as C
    import collections

    results = []
    dense = C.dense_ids()
    if not any(dense.values()):
        results.append((False, "catalogue ids are dense",
                        "reference.sqlite unreadable -- ids fall back to hashing"))
    for kind in sorted(dense):
        keys = list(dense[kind])
        if not keys:
            continue
        seen = collections.defaultdict(list)
        for k in keys:
            seen[S.cat_index(kind, k)].append(k)
        dup = {i: v for i, v in seen.items() if len(v) > 1}
        spare = S.CAT_BUCKETS[kind] - len(keys)
        results.append((not dup, "cat_index injective for %-13s" % kind,
                        "%d keys, %d spare rows%s"
                        % (len(keys), spare,
                           "" if not dup else "  COLLIDING: %s" % list(dup.values())[:2])))
        results.append((spare > 0, "cat_index has room above %-11s" % kind,
                        "%d free of %d" % (spare, S.CAT_BUCKETS[kind])))
    span = {k: (S.CAT_OFFSET[k], S.CAT_OFFSET[k] + S.CAT_BUCKETS[k]) for k in S.CAT_BUCKETS}
    overlap = [(a, b) for a in span for b in span
               if a < b and span[a][0] < span[b][1] and span[b][0] < span[a][1]]
    results.append((not overlap, "cat_global ranges are disjoint",
                    "%d kinds, vocab %d" % (len(span), S.CAT_VOCAB)))
    if verbose:
        for good, name, detail in results:
            print("  %s  %-48s %s" % ("PASS" if good else "FAIL", name, detail))
    return [r for r in results if not r[0]]


def check(verbose=True):
    import torch
    try:
        from advisor.mapgraph import net as N
        from advisor.mapgraph import schema as S
        from advisor.mapgraph import train as T
    except ImportError:
        import net as N
        import schema as S

    src_net = open(os.path.join(_HERE, "net.py"), encoding="utf-8").read()
    code_trn = _code_only(os.path.join(_HERE, "train.py"))
    results = []

    def ok(name, cond, detail=""):
        results.append((bool(cond), name, detail))

    head = inspect.getsource(N.Head.forward)
    q_line = [l for l in head.splitlines() if l.strip().startswith("q =")][0]
    ok("action is a node; q reads h[action_index] alone",
       "self.q(h[data.action_index])" in q_line and "cat" not in q_line, q_line.strip())

    nll = inspect.getsource(N.listwise_nll)
    ok("listwise NLL over each decision's candidate set",
       "softmax(q, a_graph" in nll and "-torch.log" in nll)

    mse = [ast.unparse(n) for n in ast.walk(ast.parse(code_trn))
           if isinstance(n, ast.Call) and "mse" in ast.unparse(n.func).lower()]
    ok("no mse(q, ...); only mse(v, y_z)",
       all(not c.replace(" ", "").split("(", 1)[1].startswith("q,") for c in mse),
       "; ".join(mse) or "none")

    ok("advantage weight exp((y_z - v)/tau), clipped",
       "b.y_z - v.detach()" in code_trn and "adv_clip" in code_trn)

    banned_calls, importers = [], []
    for fn in sorted(os.listdir(_HERE)):
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
    ok("no mapgraph module imports advisor/features.py",
       not banned_calls and not importers,
       "importers: %s  banned calls: %s" % (importers or "none", banned_calls or "none"))

    enc = inspect.getsource(N.Encoder.forward)
    ok("entity-only phase runs before any action round",
       "for _ in range(self.entity_layers)" in enc
       and enc.index("for _ in range(self.entity_layers)")
       < enc.index("for r in range(self.action_rounds)"))

    e = N.from_cfg(dict(T.CFG, hidden=64, entity_layers=2, action_rounds=2)).encoder
    conv = N.RelConv(8, S.REL_DIM, "mean", False, T.CFG["conv"], T.CFG["dst_dim"])
    empty = conv(torch.randn(5, 8), torch.zeros((2, 0), dtype=torch.long),
                 torch.zeros((0, S.REL_DIM)))
    ok("a2e gate initialised to zero", bool(torch.all(e.a2e_gate == 0)))
    ok("RelConv returns a pure message (exactly 0 with no edges)",
       bool(torch.all(empty == 0)), "max|out|=%.3g" % float(empty.abs().max()))

    msg = inspect.getsource(N.RelConv.message)
    ok("message conditions on x_i, x_j and rel jointly",
       all(t in msg for t in ("x_i", "x_j", "rel", "cat")),
       msg.strip().splitlines()[-1].strip())

    ok("per-node-type encoders and norms",
       "F.embedding(node_type, self.bias)" in src_net
       and "F.embedding(node_type, self.affine)" in src_net)
    widest = max(S.TYPE_FIELDS, key=lambda t: len(S.TYPE_FIELDS[t]))
    ok("scalar count is computed, not written down",
       S.N_SCALARS == sum(len(v) for v in S.TYPE_FIELDS.values()),
       "%d scalars across %d node types" % (S.N_SCALARS, len(S.NODE_TYPES)))
    ok("MAX_FIELDS is the widest single type, and every type fits it",
       S.MAX_FIELDS == len(S.TYPE_FIELDS[widest])
       and all(len(v) <= S.MAX_FIELDS for v in S.TYPE_FIELDS.values()),
       "MAX_FIELDS=%d, set by %r; input row is _NT*MAX_FIELDS = %d wide"
       % (S.MAX_FIELDS, widest, len(S.NODE_TYPES) * S.MAX_FIELDS))
    ok("the value head sees no fact about the option generator",
       "log_n_offers" not in S.G_CTX_FIELDS and "n_offers" not in "".join(S.G_CTX_FIELDS),
       "%d context scalars: %s" % (S.G_CTX_DIM, ", ".join(S.G_CTX_FIELDS)))
    ok("every instance node type carries an identity or a scalar",
       all(S.TYPE_FIELDS[t] or t in S.CAT_BUCKETS or t in ("slot", "cgroup")
           for t in S.INSTANCE_TYPES),
       "faction was the exception: no scalar beyond is_player and no catalogue id, so "
       "two same-race factions were one node")
    ok("schema keeps the full node-type set",
       len(S.NODE_TYPES) >= 15,
       "%d node types, %d relations" % (len(S.NODE_TYPES), S.N_RELATIONS))

    launcher_screens = _launcher_screens()
    missing = sorted(launcher_screens - set(S.SCREEN_TYPES))
    ok("every launcher interrupt screen is in schema.SCREEN_TYPES",
       not missing and bool(launcher_screens),
       "%d found in launcher/interrupts.py%s"
       % (len(launcher_screens), ("  MISSING: %s" % missing) if missing else ""))

    map_types = set(S.ACTION_TYPES) - set(S.SCREEN_ACTION_TYPES)
    clash = sorted(map_types & set(S.SCREEN_ACTION_TYPES))
    ok("screen action types do not collide with map action types",
       not clash and len(set(S.ACTION_TYPES)) == len(S.ACTION_TYPES),
       "%d map + %d screen = %d distinct"
       % (len(map_types), len(S.SCREEN_ACTION_TYPES), len(set(S.ACTION_TYPES))))

    if verbose:
        for good, name, detail in results:
            print("  %s  %-48s %s" % ("PASS" if good else "FAIL", name, detail))
    return [r for r in results if not r[0]]


if __name__ == "__main__":
    common.require_venv()
    print("mapgraph invariants")
    print(" catalogue:")
    bad = check_catalogue()
    print(" network:")
    bad += check()
    print("\n%s" % ("all invariants hold" if not bad else
                    "%d INVARIANT(S) BROKEN" % len(bad)))
    raise SystemExit(1 if bad else 0)
