r"""pack_multi.py -- build ONE pack containing SEVERAL mod scripts.

    python tw\pack_multi.py build     # mod/twstate.lua + mod/twcontrol.lua -> dist/tw.pack
    python tw\pack_multi.py install
    python tw\pack_multi.py uninstall
    python tw\pack_multi.py status

WHY THIS EXISTS
  The project rule is ALL GAME-FILE CHANGES IN ONE PACK -- a previous agent left a disruptive mod
  installed and it cost a run. But we now need TWO scripts live at once:
      twstate.lua   -- passive per-turn state dump (the build order's source of truth)
      twcontrol.lua -- the command bus (so a script can DRIVE the campaign)
  pack.py packs exactly one lua. Rather than install two packs (breaking the rule) or paste the
  two scripts together (making both unreadable and coupling their failure modes), this writes a
  single PFH5 archive with two entries. Same rule, no compromise.

PFH5 layout (verified against the working single-file packer, pack.py):
    'PFH5' + u32 bitmask + u32 dep_count + u32 dep_size + u32 file_count + u32 index_size + u32 ts
    then, per file:  u32 uncompressed_size, u8 compressed_flag, NUL-terminated backslash path
    then the raw bytes of every file, in the same order as the index.
A "Movie"-type pack (bitmask 4) loads just by sitting in data\ -- no mod manager, no Workshop.
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOVIE = 4
sys.path.insert(0, str(HERE)); import config
DEFAULT_GAME = config.GAME_DIR

# Every script that must be live at once, as (name, source, environment). The environment picks the
# script\<env>\mod\ path the game auto-loads it from. twcontrol ships to BOTH campaign AND frontend so
# the command bus is available in the menu/lord-select screens too (deterministic, screenshot-free
# launch); it is environment-aware (uses the real-time timer when cm is absent).
SCRIPTS = [
    ("twstate", HERE / "mod" / "twstate.lua", "campaign"),
    ("twcontrol", HERE / "mod" / "twcontrol.lua", "campaign"),
    ("twcontrol", HERE / "mod" / "twcontrol.lua", "frontend"),
]
PACK = HERE / "dist" / "tw.pack"


def game_dir(explicit: str | None = None) -> Path:
    """Resolve the game directory: explicit arg, then GAME_DIR env, then config.

    Args:
        explicit: A directory path given on the command line, or None.

    Returns:
        The chosen game directory as a Path.
    """
    return Path(explicit or os.environ.get("GAME_DIR") or DEFAULT_GAME)


def build() -> Path:
    """Write the PFH5 Movie pack containing every script in SCRIPTS.

    Returns:
        The path of the written pack (dist/tw.pack). Exits the process if a
        source script is missing.
    """
    entries = []
    for name, path, env in SCRIPTS:
        if not path.exists():
            sys.exit("missing script: %s" % path)
        data = path.read_bytes()
        internal = "script\\%s\\mod\\%s.lua" % (env, name)
        entries.append((internal, data))

    index = b"".join(
        struct.pack("<I", len(data)) + b"\x00" + internal.encode() + b"\x00"
        for internal, data in entries
    )
    header = b"PFH5" + struct.pack("<IIIIII", MOVIE, 0, 0, len(entries), len(index), 0)
    blob = b"".join(data for _, data in entries)
    PACK.parent.mkdir(exist_ok=True)
    PACK.write_bytes(header + index + blob)
    return PACK


def is_ours(path: Path) -> bool:
    """Only ever delete a pack we could have written: our exact internal paths inside.

    Args:
        path: The installed pack file to inspect.

    Returns:
        True if the file contains every one of our internal script paths;
        False otherwise, or if the file cannot be read.
    """
    try:
        blob = path.read_bytes()
    except OSError as e:
        sys.stderr.write("pack_multi: is_ours could not read %s -> %s\n" % (path, repr(e)[:60]))
        return False
    return all(("script\\%s\\mod\\%s.lua" % (env, n)).encode() in blob for n, _, env in SCRIPTS)


def cmd_build(a: argparse.Namespace) -> None:
    """Build the pack and print its size and contents.

    Args:
        a: Parsed CLI args (unused here beyond the shared handler signature).

    Returns:
        None.
    """
    p = build()
    print("built %s (%d bytes)" % (p, p.stat().st_size))
    for n, s, env in SCRIPTS:
        print("   + script\\%s\\mod\\%s.lua  (%d bytes)" % (env, n, s.stat().st_size))


def cmd_install(a: argparse.Namespace) -> None:
    """Build the pack and copy it into the game's data\\ directory.

    Refuses to overwrite a pack that is not ours; exits if the destination is
    locked (game running).

    Args:
        a: Parsed CLI args; a.game optionally overrides the game directory.

    Returns:
        None.
    """
    p = build()
    dst = game_dir(a.game) / "data" / p.name
    if dst.exists() and not is_ours(dst):
        sys.exit("refusing to overwrite %s -- it is not ours" % dst)
    try:
        shutil.copy2(p, dst)
    except PermissionError:
        sys.exit("%s is locked -- close the game first" % dst)
    print("installed %s" % dst)
    print("VERIFY BY OUTCOME in lua_mod_log.txt -- 'loaded successfully' is NOT 'ran':")
    for n, _, _ in SCRIPTS:
        print("   %s() executed successfully" % n)


def cmd_uninstall(a: argparse.Namespace) -> None:
    """Remove the installed pack from the game's data\\ directory, if it is ours.

    Args:
        a: Parsed CLI args; a.game optionally overrides the game directory.

    Returns:
        None.
    """
    dst = game_dir(a.game) / "data" / PACK.name
    if not dst.exists():
        print("not installed")
        return
    if not is_ours(dst):
        sys.exit("refusing to delete %s -- it is not ours" % dst)
    dst.unlink()
    print("removed %s" % dst)


def cmd_status(a: argparse.Namespace) -> None:
    """Print whether the pack is built, installed, and whether sources exist.

    Args:
        a: Parsed CLI args; a.game optionally overrides the game directory.

    Returns:
        None.
    """
    dst = game_dir(a.game) / "data" / PACK.name
    print("built:     %s" % (PACK if PACK.exists() else "(no)"))
    print("installed: %s" % (dst if dst.exists() else "(no)"))
    for n, s, _ in SCRIPTS:
        print("  %-12s src=%s" % (n, "ok" if s.exists() else "MISSING"))


def main() -> None:
    """Parse the CLI (build|install|uninstall|status [--game DIR]) and dispatch.

    Returns:
        None.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "install", "uninstall", "status"])
    ap.add_argument("--game")
    a = ap.parse_args()
    {"build": cmd_build, "install": cmd_install,
     "uninstall": cmd_uninstall, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
