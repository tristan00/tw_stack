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

# THE CAMPAIGN INTRO MOVIES ARE REPLACED WITH AN EMPTY ONE.
SWAP_INTRO_MOVIES = True

STANDIN_PACK = "movies_spell.pack"
STANDIN_PATH = "movies\\spell_previews\\cataclysm\\wh3_main_cataclysm_thunderbolt.ca_vp8"
STANDIN_MAX = 64
INTRO_PREFIX = "movies\\warhammer3\\"
INTRO_MARK = "_intro"
INTRO_EXT = ".ca_vp8"


# The lua runs inside the game and cannot import python, so the bus paths are baked in
# here. Only twcontrol.lua carries the markers; a file without them is passed through.
MARKERS = {b"@@BUS_CMD_PATH@@": common.BUS_CMD_PATH,
           b"@@BUS_OUT_PATH@@": common.BUS_OUT_PATH}


def _substitute(data: bytes, path: Path) -> bytes:
    if b"twcontrol" not in path.name.encode() and not any(m in data for m in MARKERS):
        return data
    for marker, value in MARKERS.items():
        if marker in data:
            data = data.replace(marker, common.posix(value).encode("utf-8"))
        elif path.name == "twcontrol.lua":
            sys.exit("%s has lost the %s marker -- the pack would ship a bus path from "
                     "whatever machine last edited it" % (path, marker.decode()))
    return data


def game_dir(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get("GAME_DIR") or DEFAULT_GAME)


def _pfh5_index(path: Path):
    """[(internal_path, size)], and the offset the data blob starts at."""
    with open(path, "rb") as fh:
        head = fh.read(28)
        if head[:4] != b"PFH5":
            return None, None
        _bm, _dc, dep_size, n_files, index_size, _ts = struct.unpack_from("<IIIIII", head, 4)
        fh.seek(28 + dep_size)
        index = fh.read(index_size)
    ent, off = [], 0
    for _ in range(n_files):
        size = struct.unpack_from("<I", index, off)[0]
        off += 5
        end = index.index(b"\x00", off)
        ent.append((index[off:end].decode("latin1"), size))
        off = end + 1
    return ent, 28 + dep_size + index_size


def _movie_packs(game: Path):
    return sorted((game / "data").glob("movies*.pack"))


def _standin_bytes(game: Path) -> bytes:
    src = game / "data" / STANDIN_PACK
    ent, data0 = _pfh5_index(src) if src.exists() else (None, None)
    if not ent:
        sys.exit("cannot read %s -- the intro-movie stand-in comes out of the game's own "
                 "packs and there is no fallback" % src)
    off = data0
    for name, size in ent:
        if name == STANDIN_PATH:
            if size > STANDIN_MAX:
                sys.exit("%s is %d bytes, expected <=%d -- refusing to ship it as the "
                         "empty-movie stand-in" % (STANDIN_PATH, size, STANDIN_MAX))
            with open(src, "rb") as fh:
                fh.seek(off)
                blob = fh.read(size)
            if blob[:4] != b"CAMV":
                sys.exit("%s does not start with CAMV (got %r) -- not a movie" % (STANDIN_PATH, blob[:4]))
            return blob
        off += size
    sys.exit("%s not found in %s" % (STANDIN_PATH, src))


def intro_movies(game: Path):
    """Every campaign intro in the installed game. Scanned, not hardcoded, so a new DLC's"""
    out = set()
    for p in _movie_packs(game):
        ent, _ = _pfh5_index(p)
        for name, _size in (ent or ()):
            low = name.lower()
            if (low.startswith(INTRO_PREFIX) and low.endswith(INTRO_EXT)
                    and INTRO_MARK in low.rsplit("\\", 1)[-1]):
                out.add(name)
    return sorted(out)


def build() -> Path:
    entries = []
    for name, path, env in SCRIPTS:
        if not path.exists():
            sys.exit("missing script: %s" % path)
        data = _substitute(path.read_bytes(), path)
        internal = "script\\%s\\mod\\%s.lua" % (env, name)
        entries.append((internal, data))

    game = game_dir()
    if SWAP_INTRO_MOVIES and (game / "data" / STANDIN_PACK).exists():
        standin = _standin_bytes(game)
        intros = intro_movies(game)
        if not intros:
            # Silently shipping no overrides would look identical to the optimisation
            # working, and the only symptom would be campaigns quietly costing 100s again.
            sys.exit("found 0 campaign intro movies under %s in %s -- the scan is broken; "
                     "refusing to build a pack that silently drops the override"
                     % (INTRO_PREFIX, game / "data"))
        for internal in intros:
            entries.append((internal, standin))

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
    game = game_dir(a.game)
    if SWAP_INTRO_MOVIES and (game / "data" / STANDIN_PACK).exists():
        intros = intro_movies(game)
        print("   + %d campaign intro movies -> %d-byte empty CAMV"
              % (len(intros), len(_standin_bytes(game))))
        for i in intros:
            print("       %s" % i)


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
