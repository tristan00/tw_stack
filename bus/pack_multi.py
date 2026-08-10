from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import common

MOVIE = 4
DEFAULT_GAME = common.GAME_DIR

SCRIPTS = [
    ("twstate", HERE / "mod" / "twstate.lua", "campaign"),
    ("twcontrol", HERE / "mod" / "twcontrol.lua", "campaign"),
    ("twcontrol", HERE / "mod" / "twcontrol.lua", "frontend"),
]
PACK = HERE / "dist" / "tw.pack"


def game_dir(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get("GAME_DIR") or DEFAULT_GAME)


def build() -> Path:
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
    try:
        blob = path.read_bytes()
    except OSError as e:
        sys.stderr.write("pack_multi: is_ours could not read %s -> %s\n" % (path, repr(e)[:60]))
        return False
    return all(("script\\%s\\mod\\%s.lua" % (env, n)).encode() in blob for n, _, env in SCRIPTS)


def cmd_build(a: argparse.Namespace) -> None:
    p = build()
    print("built %s (%d bytes)" % (p, p.stat().st_size))
    for n, s, env in SCRIPTS:
        print("   + script\\%s\\mod\\%s.lua  (%d bytes)" % (env, n, s.stat().st_size))


def cmd_install(a: argparse.Namespace) -> None:
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
    dst = game_dir(a.game) / "data" / PACK.name
    if not dst.exists():
        print("not installed")
        return
    if not is_ours(dst):
        sys.exit("refusing to delete %s -- it is not ours" % dst)
    dst.unlink()
    print("removed %s" % dst)


def cmd_status(a: argparse.Namespace) -> None:
    dst = game_dir(a.game) / "data" / PACK.name
    print("built:     %s" % (PACK if PACK.exists() else "(no)"))
    print("installed: %s" % (dst if dst.exists() else "(no)"))
    for n, s, _ in SCRIPTS:
        print("  %-12s src=%s" % (n, "ok" if s.exists() else "MISSING"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "install", "uninstall", "status"])
    ap.add_argument("--game")
    a = ap.parse_args()
    {"build": cmd_build, "install": cmd_install,
     "uninstall": cmd_uninstall, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
