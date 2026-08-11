r"""Assert two things about the game's own settings file, preferences.script.txt.

1. Line endings are CRLF. Every copy the game has written is 213/213 CRLF (the July
   backup, the 2026-08-11 03:30 backup). On 2026-08-11 the file was found 213/213 bare
   LF, with no value changed -- it diffs clean against its own backup once line endings
   are normalised. A tool had read it in text mode (universal newlines) and written it
   back with newline="".

2. gfx_resolution_scale and gfx_fullscreen still hold the collection profile's values.

Observed on 2026-08-11, campaign-script start to "environment is Campaign UI", off the
game's script_log; the LF rewrite landed between 03:25 and 03:46:

    03:18   12.2s
    03:25   12.1s
    03:46   no campaign UI
    03:49  102.1s
    03:53   log ends at 4.1s

bus_launcher.start_campaign allows 120s for the mod to log "started"; the three launches
after 03:45 failed on that timeout. Whether the line endings caused the slow loads is not
established here -- the two are recorded together because they coincided.

Does not repair the file: the values are a tuning decision.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common


def prefs_path():
    """Where the game keeps its settings. APPDATA is the only machine-dependent part."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return os.path.join(appdata, "The Creative Assembly", "Warhammer3", "scripts",
                        "preferences.script.txt")


def line_endings(raw):
    """(crlf, bare_lf) counts for a file read in binary."""
    crlf = raw.count(b"\r\n")
    return crlf, raw.count(b"\n") - crlf


def main():
    p = prefs_path()
    fails = []
    if not p or not os.path.isfile(p):
        # Not a failure: a checkout on a machine with no game installed still runs the
        # rest of check.py, and inventing a pass here would be worse than saying so.
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

    # The throughput profile the run is tuned around. If these are not what the run
    # expects, campaign load and per-turn cost both move, so say which one drifted
    # rather than only reporting the line endings.
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
