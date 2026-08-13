
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

STEPS = (
    ("typecheck", ["run", "typecheck"]),
    ("contrast", ["run", "check:contrast"]),
    ("build", ["run", "build"]),
)


def main() -> int:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        print("npm is not on PATH -- the client cannot be checked or built.")
        print("Install Node LTS, then: cd ui && npm install")
        return 1
    if not os.path.isdir(os.path.join(HERE, "node_modules")):
        print("ui/node_modules is missing. Run: cd ui && npm install")
        return 1

    failed = []
    for name, args in STEPS:
        r = subprocess.run([npm, *args], cwd=HERE, capture_output=True, text=True,
                           timeout=600, shell=False)
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        last = next((l for l in reversed(tail) if l.strip()), "(no output)")
        if r.returncode != 0:
            failed.append(name)
            print("FAIL %-10s %s" % (name, last[:160]))
            for line in tail[-12:]:
                print("      " + line[:160])
        else:
            print("ok   %-10s %s" % (name, last[:120]))

    if failed:
        print("\n%d client check(s) failed: %s" % (len(failed), ", ".join(failed)))
        return 1
    print("\nclient checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
