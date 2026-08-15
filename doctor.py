
from __future__ import annotations

import os
import shutil
import sys

import common


def _src(env_name, key):
    if os.environ.get(env_name):
        return "env %s" % env_name
    if common.CONFIG.get(key):
        return "config.toml"
    return "autodetected/default"


def _row(label, value, ok, source, fix):
    mark = "ok  " if ok else "MISS"
    print("  %s %-10s %-58s %s" % (mark, label, str(value)[:58] or "(unset)", source))
    if not ok and fix:
        print("       -> %s" % fix)
    return ok


def main() -> int:
    print("tw_stack paths\n")
    print("  config: %s" % (common.CONFIG_PATH if os.path.exists(common.CONFIG_PATH)
                            else "%s (absent -- copy config.example.toml)" % common.CONFIG_PATH))
    print()
    ok = True
    ok &= _row("repo", common.ROOT, os.path.isdir(common.ROOT), "from common.py", None)
    ok &= _row("game", common.GAME_DIR,
               bool(common.GAME_DIR) and os.path.isfile(
                   os.path.join(common.GAME_DIR, "Warhammer3.exe")),
               _src("TW_GAME_DIR", "game_dir"),
               "set paths.game_dir in config.toml -- the folder holding Warhammer3.exe")
    ok &= _row("twdata", common.TWDATA, os.path.isdir(common.TWDATA),
               _src("TWDATA", "twdata"),
               "will be created on first run, or set paths.twdata")
    ok &= _row("project", common.RUNNER, os.path.isdir(common.RUNNER),
               "checkout", "the checkout must own .venv")
    ok &= _row("venv", common.VENV_PY, os.path.isfile(common.VENV_PY),
               "derived from runner", "create it: python -m venv <runner>/.venv")
    ok &= _row("bus dir", common.native(common.RUNNER_DATA),
               os.path.isdir(common.native(common.RUNNER_DATA)),
               "derived from runner", "created on first launch")

    print("\ntools")
    for tool, why in (("npm", "the dashboard client is built with it"),
                      ("git", "not required to run, used for provenance")):
        found = shutil.which(tool) or shutil.which(tool + ".cmd")
        print("  %s %-10s %s" % ("ok  " if found else "MISS", tool, found or why))

    print("\npython")
    print("  running  %s (%s)" % (sys.executable, "%d.%d" % sys.version_info[:2]))
    if sys.version_info < (3, 11):
        print("       -> 3.11+ is required (config.toml is read with tomllib)")
        ok = False

    if not os.path.isdir(common.TWDATA):
        print("\n%s does not exist yet. It is created on first run and holds every"
              "\nrun dir, model and log -- nothing generated goes in the checkout."
              % common.TWDATA)
    print("\n%s" % ("all paths resolve" if ok else "something above needs setting"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
