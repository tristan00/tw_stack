from __future__ import annotations


import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import common
MOD_DIR = os.path.join(_HERE, "mod")
LUA_STRING_MODULES = ("decisions.collect", "launcher.cco_actions", "launcher.cm_actions",
                      "launcher.click_actions")


def _runtime():
    try:
        from lupa import LuaRuntime
    except ImportError:
        return None
    return LuaRuntime(unpack_returned_tuples=True)


def _compiles(lua, src, name):
    try:
        lua.execute("return function(s, n) return load(s, n) end")
        loader = lua.eval("function(s, n) local f, e = load(s, n) return f ~= nil, e end")
        ok, err = loader(src, name)
        return bool(ok), (None if ok else str(err))
    except Exception as e:
        return False, repr(e)[:200]


def _fragments():
    import importlib
    for fn in sorted(os.listdir(MOD_DIR)):
        if fn.endswith(".lua"):
            with open(os.path.join(MOD_DIR, fn), "r", encoding="utf-8",
                      errors="replace") as fh:
                yield fn, fh.read()
    for modname in LUA_STRING_MODULES:
        mod = importlib.import_module(modname)
        short = modname.rsplit(".", 1)[-1]
        for name in sorted(vars(mod)):
            v = getattr(mod, name)
            if not isinstance(v, str) or len(v) < 40:
                continue
            if not name.startswith("_LUA") and "cco(" not in v and "cm:" not in v:
                continue
            src = v
            if "%(" in src or ("%s" in src and "%%" not in src):
                try:
                    src = _fill(src)
                except (KeyError, ValueError, TypeError):
                    continue
            yield "%s.%s" % (short, name), src


class _Any(dict):
    def __missing__(self, k):
        return "0" if k in ("cqi", "slot", "cap", "fac", "faction_cqi") else "x"


def _fill(src):
    if "%(" in src:
        return src % _Any()
    return src.replace("%d", "0").replace("%s", "x")


def main():
    lua = _runtime()
    if lua is None:
        print("lupa is not installed -- skipping (pip install lupa)")
        return 0
    for probe in ("local x = ", "if true then", "for i=1,3 do print(i)",
                  "local t = {1,2", "function f( end"):
        if _compiles(lua, probe, "probe")[0] or _compiles(lua, "return " + probe,
                                                          "probe")[0]:
            print("SELFTEST FAILED: lua accepted %r -- this checker proves nothing" % probe)
            return 1
    bad = []
    n = 0
    for label, src in _fragments():
        ok, err = _compiles(lua, src, label)
        if not ok:
            ok2, _ = _compiles(lua, "return " + src, label)
            ok = ok2
        n += 1
        if not ok:
            bad.append((label, err))
    print("lua fragments compiled: %d ok, %d BROKEN" % (n - len(bad), len(bad)))
    for label, err in bad:
        print("  %-40s %s" % (label, (err or "")[:150]))
    return 1 if bad else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
