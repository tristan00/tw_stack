r"""The game's own settings file still looks like the game wrote it.

WHY THIS EXISTS -- it cost a full night of collection, and nothing caught it.

preferences.script.txt is Warhammer3's own settings file, and the run depends on the
throughput profile inside it: every gfx_*_quality at 0, gfx_resolution_scale 0.5,
gfx_fullscreen false. A tool edited that file with a python one-liner that read it in
text mode (universal newlines, so CRLF -> LF) and wrote it back with newline="", which
does no re-translation. Every one of the 213 lines lost its \r. Not one VALUE changed --
the file diffs clean against its own backup once you normalise line endings, which is
exactly why three separate passes over it found nothing wrong.

What it cost, measured off the game's own script_log timestamps, campaign-script start
to "environment is Campaign UI":

    03:18   12.2s      healthy
    03:25   12.1s      healthy
    ---- preferences.script.txt rewritten CRLF -> LF ----
    03:46   never reached the campaign UI
    03:49  102.1s      arrived, but past the launcher's deadline
    03:53   died at 4.1s

bus_launcher.start_campaign allows 120s for the mod to log "started". At 12s that is a
ten-fold margin; at 102s it is a coin flip. Three campaigns in a row failed to load, the
session's consecutive-failure breaker fired, and the batch stopped. The visible symptom
was "campaign did not load ('started' never logged)", which points at the launcher, the
mod and the bus -- everywhere except the settings file.

THE INVARIANT, and why it is this one. The game writes this file itself
(write_preferences_at_exit), and every copy it has ever written is CRLF: the July backup
is 213/213 CRLF, the pre-edit backup is 213/213 CRLF. So bare LF in that file does not
mean "a line ending the game dislikes", it means THE GAME DID NOT WRITE THIS -- some
other tool did, and did it lossily. That is the checkable fact, and it is the one that
was true here.

This check deliberately does NOT repair the file. The values in it are a tuning decision
and a silent rewrite is how the damage happened in the first place; a loud failure with
the byte counts in it is the whole point.
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
            "%d line(s) end in bare LF. The game writes this file as CRLF, so it was "
            "rewritten by something other than the game -- and a rewrite that lost the "
            "\\r is one that read the file with universal newlines and wrote it back "
            "with newline=''. Campaign loads went 12s -> 102s the last time this "
            "happened, which is past the 120s 'started' deadline in "
            "bus_launcher.start_campaign. Restore a CRLF copy; do not just re-save it."
            % bare_lf)

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
