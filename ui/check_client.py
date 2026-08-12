"""Run the client's own gates from check.py.

check.py invokes every harness as `<venv python> <argv>`, so the node-side checks need a
Python entry point rather than a special case in the runner. This is that entry point.

It runs three things, and each one exists because of a defect that shipped:

  typecheck      the client's types are generated from the server's OpenAPI document, so
                 a column wired to a field the server does not send is a compile error
                 rather than a blank column nobody notices.

  check:contrast every colour token is measured against WCAG in BOTH themes. The previous
                 stylesheet overrode five of its eight tokens for light mode and left
                 green, amber and red at their dark-tuned values -- 2.54:1, 2.52:1 and
                 3.35:1 against a 4.5:1 requirement, on the theme actually in use.

  build          a client that does not build is a dashboard that serves the last build
                 silently. Failing here is how that gets noticed.

If node or the dependencies are absent the check reports that plainly and fails, rather
than passing by doing nothing -- a check that skips itself when its toolchain is missing
is the failure mode common.require_venv exists to prevent on the Python side.
"""

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
