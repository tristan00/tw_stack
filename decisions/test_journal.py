from __future__ import annotations

"""Exercise the record/read path with no game attached.

`_io_lock = threading.Lock()` was dropped by a refactor while `import threading` and both
`with _io_lock:` blocks survived. Nothing imports-and-calls this module outside a live
run, so the NameError sat latent until a campaign loaded, and then every campaign died on
the first journal write with 0 decisions recorded. A five-campaign run and a game launch
were spent finding a missing one-liner.

This calls the append/read functions directly against a temp run dir, which is all it
would have taken. It also name-checks the modules the run loop depends on, because a
NameError in a rarely-taken branch is exactly what this class of refactor produces.

    python -m decisions.test_journal
"""

import ast
import builtins
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import common  # noqa: E402

FAILED = []


def check(cond, what, detail=""):
    print("  %-4s %-52s %s" % ("ok" if cond else "FAIL", what, detail))
    if not cond:
        FAILED.append(what)


def _module_globals_defined(path):
    """Names a module reads at module scope or inside its functions that are neither
    defined there, imported, builtins, nor local. Catches the _io_lock shape."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__",
                                    "__spec__", "__loader__", "__builtins__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                defined.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                defined.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            defined.update(node.names)
    missing = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in defined:
                missing.add(node.id)
    return sorted(missing)


def main():
    d = tempfile.mkdtemp(prefix="journaltest_")
    try:
        from decisions import journal as J

        run = os.path.join(d, "run")
        os.makedirs(run)

        # the exact call that died: an append under the lock
        J._append(run, "events.jsonl", {"kind": "smoke", "n": 1})
        J._append(run, "events.jsonl", {"kind": "smoke", "n": 2})
        p = os.path.join(run, "events.jsonl")
        check(os.path.exists(p), "_append writes without raising")
        check(len(open(p, encoding="utf-8").read().strip().splitlines()) == 2,
              "_append wrote both rows")

        rows, off = J._read_rows(p, 0)
        check(len(rows) == 2 and rows[0]["kind"] == "smoke", "_read_rows reads them back")
        check(off > 0, "_read_rows returns a usable offset")

        # every public entry point the run loop touches must at least resolve its names
        for mod in ("decisions/journal.py", "decisions/collect.py",
                    "decisions/decisions_stream.py", "decisions/store.py",
                    "advisor/loop.py", "advisor/policy.py", "advisor/strategies.py"):
            path = os.path.join(common.ROOT, mod.replace("/", os.sep))
            if not os.path.exists(path):
                continue
            missing = _module_globals_defined(path)
            check(not missing, "no undefined names in %s" % mod,
                  ("missing: %s" % ", ".join(missing[:6])) if missing else "")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n%s" % ("journal OK" if not FAILED else "%d FAILED" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
