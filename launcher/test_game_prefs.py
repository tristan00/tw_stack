
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common


def prefs_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return os.path.join(appdata, "The Creative Assembly", "Warhammer3", "scripts",
                        "preferences.script.txt")


def line_endings(raw):
    crlf = raw.count(b"\r\n")
    return crlf, raw.count(b"\n") - crlf


def main():
    p = prefs_path()
    fails = []
    if not p or not os.path.isfile(p):
        print("SKIP: no preferences.script.txt at %s" % (p or "<no APPDATA>"))
        return 0

    with open(p, "rb") as fh:
        raw = fh.read()
    crlf, bare_lf = line_endings(raw)
    print("preferences.script.txt: %s" % p)
    print("  bytes=%d  CRLF=%d  bare-LF=%d" % (len(raw), crlf, bare_lf))

    if bare_lf:
        fails.append(
            "%d line(s) end in bare LF; every copy the game has written is CRLF. "
            "Restore a CRLF copy rather than re-saving this one." % bare_lf)

    want = {"gfx_resolution_scale": "0.5", "gfx_fullscreen": "false"}
    seen = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        key, _, rest = line.partition(" ")
        if key in want:
            seen[key] = rest.split(";")[0].strip()
    for k, v in sorted(want.items()):
        got = seen.get(k)
        print("  %-24s %s" % (k, got))
        if got is None:
            fails.append("%s is missing from the settings file" % k)
        elif got != v:
            fails.append("%s is %r, the collection profile wants %r" % (k, got, v))

    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  - " + f)
        return 1
    print("PASS: the settings file still has the game's own CRLF line endings and the "
          "collection throughput profile is intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
