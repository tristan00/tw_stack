from __future__ import annotations

"""Check every CCO route the collector uses against the game's own catalogue.

`g(c,p)` is `pcall(c:Call(p))` and swallows its failure by design, so a property name that
does not exist on the context it is called against returns nil forever. Nothing downstream
can tell that apart from "the game had nothing to say". That is how `params.item_key` was
the string "nil" in 95,178 of 95,178 rows -- `CcoCampaignAncillary` has no `Key` -- and how
`building_dismantle.refund` was null in every row of the corpus: `DismantleRefundAmount` is
a real property, but it lives on `CcoCampaignBuilding`, not on the
`CcoCampaignBuildingSlot` we were calling it against. Reading a property off the wrong
context looks exactly like reading a legitimately-absent one.

`reference/ui3_extraction/CCO.tsv` is CA's own dump of every context and every property on
it. This walks the Lua embedded in collect.py, resolves the context type of each `g()`
receiver, and checks each route segment against that catalogue.

    python -m decisions.cco_audit [--verbose] [--tsv PATH]

Exit code 1 means a route names a property that does not exist on the context it is called
against, or that a receiver's type could not be resolved. UNRESOLVED is a failure too: a
route nobody can type is a route nobody can check, and every silent-nil bug in the corpus
lived in exactly that gap.
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import common  # noqa: E402

CCO_TSV = common.CCO_TSV

# Receivers whose type does not follow from the source: a closure parameter, or an element
# of a list that a helper is called with. Each entry is a claim about a variable and is
# checked against the catalogue like any other route once applied -- it buys a name, not
# an exemption.
ROOTS = {
    # agg(list, canfn) is called twice, with FactionContext.MercenaryPoolContext... and
    # ProvinceContext.MercenaryPoolContext...; both are MercenaryPoolUnitList.
    ("_LUA_MERC_POOLS", "u"): "CcoCampaignMercenaryPoolUnit",
    ("_LUA_MERC_POOLS", "rec"): "CcoMainUnitRecord",
}

# CA's dump declares every CcoCampaignConstructionItem property as returning Void, so a
# route cannot be followed through it on the catalogue alone. The property exists; only
# its return type is missing upstream. Naming it here keeps the segment checked.
TYPE_HINTS = {
    ("CcoCampaignConstructionItem", "BuildingLevelRecordContext"): "CcoBuildingLevelRecord",
    ("CcoCharacterTraitLevelRecord", "TraitRecordContext"): "CcoCharacterTraitRecord",
}

# An un-indexed list value is still a context: CcoContextList is where Size, Count and
# IsEmpty live, and `SomeList.Size` is how the collector reads a length.
_LIST_CTX = "CcoContextList"

# The collector reads; the executors act on what it read. A guard property that does not
# exist fails identically in both -- `_slot_exec("CancelConstruction", "CanBeCancelled")`
# made `building_cancel` return 'REFUSED-nil' for the life of the corpus.
MODULES = ("decisions.collect", "launcher.cco_actions")

# The mod names context types as bare strings for GetContextObjectId. A name the game does
# not have never matches, so the panel it identifies reports no context at all -- silent,
# like every other failure in this family. CcoCampaignRegion and
# CcoCampaignBuildingChainRecord were both invented and both sat in CCO_TYPES.
LUA_FILES = (os.path.join(os.path.dirname(_HERE), "bus", "mod", "twcontrol.lua"),
             os.path.join(os.path.dirname(_HERE), "bus", "mod", "twstate.lua"))


def load_catalogue(path=None):
    """context -> {property: (return_type, arg_type)}"""
    cat = {}
    with open(path or CCO_TSV, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 3 or not p[0]:
                continue
            cat.setdefault(p[0], {})
            if p[1] and p[1] != "<CONTEXT>":
                cat[p[0]][p[1]] = (p[2], p[3] if len(p) > 3 else "")
    return cat


def _elem(ret):
    """'CcoBuildingLevelRecord (list)' -> ('CcoBuildingLevelRecord', True)"""
    ret = (ret or "").strip()
    if ret.endswith("(list)"):
        return ret[: -len("(list)")].strip(), True
    return ret, False


# ------------------------------------------------------------------ Lua extraction

def _args_of(src, i):
    """src[i] is the '(' opening a call. Return (arg_strings, index_after_close)."""
    depth, j, out, start, q = 0, i, [], i + 1, None
    while j < len(src):
        ch = src[j]
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                out.append(src[start:j])
                return out, j + 1
        elif ch == "," and depth == 1:
            out.append(src[start:j])
            start = j + 1
        j += 1
    return out, j


_STR = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_IDENT = re.compile(r"[A-Za-z_]\w*")


def _concat_parts(expr):
    """Parse a Lua `a..'b'..c` concatenation into [('lit',s) | ('var',name)].

    Returns None if the expression is anything else -- a call, an index, arithmetic. The
    collector builds routes this way (`local base=e..'['..i..']'`, then
    `f:Call(base..'.CanRecruitCharacter')`), and a route assembled from variables is still
    a route that has to exist.
    """
    parts, i, n = [], 0, len(expr)
    while True:
        while i < n and expr[i] == " ":
            i += 1
        if i >= n:
            return None if not parts else parts
        ch = expr[i]
        if ch in "\"'":
            j = expr.find(ch, i + 1)
            if j < 0:
                return None
            parts.append(("lit", expr[i + 1:j]))
            i = j + 1
        else:
            m = _IDENT.match(expr, i)
            if not m:
                return None
            i = m.end()
            k = i
            while k < n and expr[k] == " ":
                k += 1
            # A call, an index or a field access is not a concatenation. '..' is.
            if k < n and (expr[k] in "([:" or
                          (expr[k] == "." and expr[k:k + 2] != "..")):
                return None
            parts.append(("var", m.group(0)))
        while i < n and expr[i] == " ":
            i += 1
        if expr[i:i + 2] != "..":
            return parts if i >= n else None
        i += 2


def _string_vars(src):
    """Lua locals whose value is a literal string, or a concatenation of them. Unknown
    pieces become '#', which is how an index or a subtype name reads in a route."""
    seen = {}
    assigns = []
    for m in re.finditer(r"local\s+(\w+)\s*=\s*", src):
        end = len(src)
        for stop in (" local ", " if ", " for ", " return ", " end ", " o[", " out["):
            k = src.find(stop, m.end())
            if 0 <= k < end:
                end = k
        assigns.append((m.group(1), src[m.end():end]))
    for _ in range(6):
        before = dict(seen)
        for name, expr in assigns:
            parts = _concat_parts(expr)
            if parts is None:
                continue
            seen[name] = "".join(p if kind == "lit" else seen.get(p, "#")
                                 for kind, p in parts)
        if seen == before:
            break
    return seen


def _template(expr, svars=None):
    """A Lua string expression -> its literal text, every interpolated run replaced by
    '#'. `'List['..i..'].Key'` -> `List[#].Key`. None if there is no literal at all."""
    exact = _concat_parts(expr)
    if exact is not None:
        # `g(c,p)` inside g()'s own body is a route made entirely of a parameter. There is
        # nothing there to check against the catalogue, so it is not a route.
        if not any(k == "lit" and v for k, v in exact):
            return None
        svars = svars or {}
        out = "".join(p if kind == "lit" else svars.get(p, "#") for kind, p in exact)
        return out or None
    parts, pos, saw = [], 0, False
    for m in _STR.finditer(expr):
        if saw and expr[pos:m.start()].strip(" .") != "":
            parts.append("#")
        elif saw and expr[pos:m.start()].strip() == "":
            parts.append("#")          # adjacency without '..' cannot happen in Lua
        parts.append(m.group(1) if m.group(1) is not None else m.group(2))
        pos, saw = m.end(), True
    if not saw:
        return None
    if expr[pos:].strip(" .") != "":
        parts.append("#")
    return "".join(parts)


def _split_route(route):
    """Split on '.' at bracket depth 0 -- arguments and indices contain dots."""
    segs, depth, cur = [], 0, []
    for ch in route:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "." and depth == 0:
            segs.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    segs.append("".join(cur))
    return [s for s in segs if s]


def _ctx_after(ctx, segs):
    """The context a prefix of a route lands on, for blaming the right segment."""
    ret = walk(ctx, ".".join(segs), _CAT)
    if ret is None:
        return None
    t, is_list = _elem(ret)
    return t if not is_list else _LIST_CTX


_ARG_ROUTE = re.compile(r"^[A-Za-z_][\w.\[\]#()\"', ]*$")
# CCO globals that build a context out of thin air rather than off the receiver, so they
# are arguments but not routes.
_ARG_GLOBALS = ("DatabaseRecordContext",)


def _arg_routes(seg):
    """Arguments to a CCO call are themselves routes, resolved against the *receiver* --
    `CanRecruitUnitForFaction(FactionContext, ...)` off a character means
    CcoCampaignCharacter.FactionContext. An argument naming a property that does not exist
    fails exactly as silently as a bad route does."""
    if "(" not in seg:
        return []
    inner = seg[seg.index("(") + 1:seg.rindex(")")] if seg.rstrip().endswith(")") else ""
    out, depth, cur = [], 0, []
    for ch in inner:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    keep = []
    for a in out:
        a = a.strip()
        # `this` is the receiver itself; literals and numbers are not routes.
        if not a or a == "this" or a[0] in "\"'#" or a[0].isdigit():
            continue
        if a.startswith(_ARG_GLOBALS):
            continue
        if "(" in a and not a.split("(", 1)[0].strip().isidentifier():
            continue
        if _ARG_ROUTE.match(a):
            keep.append(a)
    return keep


def walk(ctx, route, cat, root=None):
    """Follow a dotted route from context `ctx`. Returns the final declared return type,
    or None the moment a segment is not a property of the context it is applied to."""
    cur, last = ctx, None
    root = root or ctx
    for seg in _split_route(route):
        for arg in _arg_routes(seg):
            if walk(root, arg, cat, root) is None:
                return None
        if not cur or not cur.startswith("Cco"):
            return None
        props = cat.get(cur)
        if props is None:
            return None
        name = re.split(r"[(\[]", seg, maxsplit=1)[0]
        if name not in props:
            return None
        last = props[name][0]
        hint = TYPE_HINTS.get((cur, name))
        t, is_list = _elem(last)
        if hint:
            cur, last = hint, hint
        elif is_list:
            cur = t if "[" in seg else _LIST_CTX
        else:
            cur = t if t.startswith("Cco") else None
    return last


# ------------------------------------------------------------------ type resolution

_CCO_CALL = re.compile(r"cco\(\s*'(Cco\w+)'")
_INDEXED = re.compile(r"^(\w+)\s*\[")
# `local sf = s and g(s,'StationedForceContext')` -- the guard chain is idiomatic here.
_ASSIGNED = re.compile(r"(?:local\s+)?(\w+)\s*=\s*(?:[\w\[\]]+\s+and\s+)*$")


def _resolve(expr, types, lists):
    """Context type of a `g()` receiver expression, or None."""
    expr = expr.strip().strip("()").strip()
    expr = expr.split(" and ")[-1].strip()
    m = _CCO_CALL.search(expr)
    if m:
        return m.group(1)
    m = _INDEXED.match(expr)
    if m:
        return lists.get(m.group(1))
    return types.get(expr)


def _recv_before(src, i):
    """The receiver expression ending at src[i] (exclusive) -- `x`, or `cco(..)`."""
    j = i - 1
    while j >= 0 and src[j] == " ":
        j -= 1
    if j >= 0 and src[j] == ")":
        depth, q = 0, None
        while j >= 0:
            ch = src[j]
            if q:
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch
            elif ch in ")]}":
                depth += 1
            elif ch in "([{":
                depth -= 1
                if depth == 0:
                    break
            j -= 1
        k = j - 1
        while k >= 0 and (src[k].isalnum() or src[k] == "_"):
            k -= 1
        return src[k + 1:i].strip(), k + 1
    k = j
    while k >= 0 and (src[k].isalnum() or src[k] == "_" or src[k] in "[]"):
        k -= 1
    return src[k + 1:i].strip(), k + 1


def _calls(src):
    """Yield (assigned_name_or_None, receiver_expr, route_template) for every
    `g(recv,'route')` and every `recv:Call('route')`."""
    svars = _string_vars(src)
    i = 0
    while True:
        k = src.find("g(", i)
        if k < 0:
            break
        i = k + 2
        if k and (src[k - 1].isalnum() or src[k - 1] == "_"):
            continue
        args, _end = _args_of(src, k + 1)
        if len(args) != 2:
            continue
        route = _template(args[1], svars)
        if route is None:
            continue
        m = _ASSIGNED.search(src[max(0, k - 60):k])
        yield (m.group(1) if m else None), args[0].strip(), route
    i = 0
    while True:
        k = src.find(":Call(", i)
        if k < 0:
            return
        i = k + 6
        args, _end = _args_of(src, k + 5)
        if len(args) != 1:
            continue
        route = _template(args[0], svars)
        if route is None:
            continue
        recv, start = _recv_before(src, k)
        if not recv or recv == "c":       # `c:Call(p)` is g()'s own body
            continue
        m = _ASSIGNED.search(src[max(0, start - 60):start])
        yield (m.group(1) if m else None), recv, route


def _var_types(src):
    """Iterate to a fixed point: an assignment may reference a variable typed later."""
    types, lists = {}, {}
    for m in re.finditer(r"(?:local\s+)?(\w+)\s*=\s*cco\(\s*'(Cco\w+)'", src):
        types[m.group(1)] = m.group(2)
    calls = list(_calls(src))
    for _ in range(8):
        before = (dict(types), dict(lists))
        for name, recv, route in calls:
            if not name:
                continue
            ctx = _resolve(recv, types, lists)
            if not ctx:
                continue
            ret = walk(ctx, route, _CAT)
            if ret is None:
                continue
            t, is_list = _elem(ret)
            if t.startswith("Cco"):
                (lists if is_list else types)[name] = t
        for rx in (r"(?:local\s+)?(\w+)\s*=\s*(\w+)\s*\[",
                   r"for\s+\w+\s*,\s*(\w+)\s+in\s+ipairs\(\s*(\w+)\s*\)"):
            for m in re.finditer(rx, src):
                t = lists.get(m.group(2))
                if t:
                    types[m.group(1)] = t
        if (types, lists) == before:
            break
    return types, lists


_PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def _subs_for(mod):
    """The literal values a template's %(name)s placeholders take, read back off the call
    sites rather than restated here -- a fourth slot action with a bad guard is then
    caught by adding no code at all."""
    import inspect
    out = {}
    try:
        src = inspect.getsource(mod)
    except (OSError, TypeError):
        return out
    for m in re.finditer(r"_slot_exec(?:_paced)?\(\s*[\"'](\w+)[\"']\s*,\s*[\"'](\w+)[\"']",
                         src):
        out.setdefault("cmd", set()).add(m.group(1))
        out.setdefault("guard", set()).add(m.group(2))
    return out


def _expand(route, subs):
    """A route naming a placeholder becomes one route per literal value it can take."""
    names = _PLACEHOLDER.findall(route)
    if not names:
        return [route]
    out = [route]
    for n in names:
        vals = subs.get(n)
        if not vals:
            return []                      # unsubstitutable -> caller reports it
        out = [r.replace("%%(%s)s" % n, v) for r in out for v in sorted(vals)]
    return out


def routes_in(name, src, subs=()):
    """Yield (receiver_expr, route, resolved_context_or_None)."""
    types, lists = _var_types(src)
    for _assigned, recv, route in _calls(src):
        base = re.split(r"[\[ ]", recv.split(" and ")[-1].strip().strip("()").strip(),
                        maxsplit=1)[0]
        ctx = _resolve(recv, types, lists) or ROOTS.get((name, base))
        expanded = _expand(route, dict(subs))
        if not expanded:
            yield recv, route, None
        for r in expanded:
            yield recv, r, ctx


def check(verbose=False, tsv=None, modules=MODULES):
    global _CAT
    _CAT = load_catalogue(tsv)
    import importlib

    bad, unresolved, ok = [], [], 0
    seen = set()
    for modname in modules:
        mod = importlib.import_module(modname)
        subs = _subs_for(mod)
        short = modname.rsplit(".", 1)[-1]
        for name in sorted(vars(mod)):
            src = getattr(mod, name)
            if not isinstance(src, str) or len(src) < 60:
                continue
            if "g(" not in src and ":Call(" not in src:
                continue
            for recv, route, ctx in routes_in(name, src, subs):
                where = "%s.%s" % (short, name)
                if (where, recv, route) in seen:
                    continue
                seen.add((where, recv, route))
                if ctx is None:
                    unresolved.append((where, recv, route))
                elif walk(ctx, route, _CAT) is None:
                    bad.append((where, ctx, route))
                else:
                    ok += 1
                    if verbose:
                        print("  OK   %-34s %s.%s" % (where, ctx, route))

    ghosts = []
    for path in LUA_FILES:
        try:
            src = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for name in sorted(set(re.findall(r"[\"'](Cco\w+)[\"']", src))):
            if name not in _CAT:
                ghosts.append((os.path.basename(path), name))
            else:
                ok += 1

    print("routes checked : %d ok, %d BAD, %d unresolved, %d ghost type(s)"
          % (ok, len(bad), len(unresolved), len(ghosts)))
    if ghosts:
        print("\nCONTEXT TYPES THAT DO NOT EXIST:")
        for where, name in ghosts:
            stem = name.replace("CcoCampaign", "").replace("Cco", "")
            near = sorted(c for c in _CAT if stem and stem in c)
            print("  %-18s %-34s did you mean: %s"
                  % (where, name, ", ".join(near[:4]) or "nothing close"))
    if bad:
        print("\nROUTES THAT DO NOT EXIST ON THE CONTEXT THEY ARE CALLED AGAINST:")
        for name, ctx, route in bad:
            print("  %-34s %s . %s" % (name, ctx, route))
            segs = _split_route(route)
            # Re-walk one segment at a time so the blame lands on the segment that
            # actually broke, against the context it was actually applied to.
            for k in range(1, len(segs) + 1):
                if walk(ctx, ".".join(segs[:k]), _CAT) is not None:
                    continue
                at = ctx if k == 1 else (_ctx_after(ctx, segs[:k - 1]) or "?")
                head = re.split(r"[(\[]", segs[k - 1], maxsplit=1)[0]
                other = sorted(c for c, p in _CAT.items() if head in p)
                print("      %r is not on %s; it exists on: %s"
                      % (head, at, ", ".join(other[:6]) or "NO CONTEXT AT ALL"))
                break
    if unresolved:
        print("\nRECEIVER TYPE NOT RESOLVED (add a ROOTS entry or read it):")
        for name, recv, route in unresolved:
            print("  %-34s %-14s %s" % (name, recv[:14], route))
    return bad + ghosts, unresolved


_CAT = {}


def selftest(tsv=None):
    """A checker nobody has seen fail is a checker nobody should trust. These are the
    four shapes that actually occurred in the corpus."""
    cat = load_catalogue(tsv)
    cases = [
        # (context, route, must_resolve)
        ("CcoCampaignBuildingSlot", "BuildingContext.DismantleRefundAmount", True),
        ("CcoCampaignBuildingSlot", "DismantleRefundAmount", False),   # the real bug
        ("CcoCampaignBuildingSlot", "IsDamaged", False),               # the real bug
        ("CcoCampaignBuildingSlot", "CanBeCancelled", False),          # the real bug
        ("CcoCampaignAncillary", "Key", False),                        # item_key = "nil"
        ("CcoCampaignCharacter", "AncillaryList[#].AncillaryRecordContext.Key", True),
        ("CcoCampaignCharacter", "TraitsList.Size", True),             # list -> Size
        ("CcoCampaignCharacter", "IsGarrisoned", True),
        ("CcoCampaignCharacter", "NoSuchPropertyAtAll", False),
        # arguments are routes off the receiver, and are checked as such
        ("CcoCampaignBuildingSlot",
         "PossibleUpgradeWithoutConversionsList[#].CreateCost(SettlementContext)", True),
        ("CcoCampaignBuildingSlot",
         "PossibleUpgradeWithoutConversionsList[#].CreateCost(NotAContext)", False),
    ]
    bad = []
    for ctx, route, want in cases:
        got = walk(ctx, route, cat) is not None
        print("  %-5s %-28s %s" % ("ok" if got == want else "FAIL", ctx, route))
        if got != want:
            bad.append((ctx, route, want, got))
    print("selftest: %d/%d" % (len(cases) - len(bad), len(cases)))
    return bad


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--selftest" in a:
        raise SystemExit(1 if selftest(a[a.index("--tsv") + 1] if "--tsv" in a else None)
                         else 0)
    _bad, _unres = check(verbose="--verbose" in a,
                         tsv=a[a.index("--tsv") + 1] if "--tsv" in a else None)
    print("\n%s" % ("cco routes OK" if not (_bad or _unres) else
                    "%d BAD, %d UNRESOLVED" % (len(_bad), len(_unres))))
    raise SystemExit(1 if (_bad or _unres) else 0)
