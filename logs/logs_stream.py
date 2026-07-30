r"""logs capture stream -- byte-exact tails of every log the game writes.

Ported faithfully from record.py:t_logs, including THE distinction that decides whether a
game restart is recorded or silently gutted:

    a log that ALREADY EXISTED when we started  -> skip its history (another session's data),
                                                   tail only bytes appended from now on
    a log CREATED AFTER we started              -> it is entirely OURS: read it from byte 0

WH3 opens a fresh script_log_*.txt per session, so the "created after us" case IS the normal
game-restart-mid-recording workflow; treating a first-seen file as start-at-end (the old bug)
discarded the mod arming + the turn-1 baseline. This stream is game-INDEPENDENT to run and
test: the source directories, poll interval, and the "just before us is still ours" slack are
all parameters, so an offline test drives the whole rule with synthetic files.

Contract with the manager -- run(ctx, log_dirs, ...) where ctx duck-types:
    ctx.emit(row)              append one row to the shared events.jsonl
    ctx.out_dir                the run directory (tails land in <out_dir>/logs/<base>.tail)
    ctx.now() -> float         seconds since the shared T0
    ctx.is_running() -> bool   True while capture should continue
    ctx.on_error(where, exc)   record a caught (never fatal) exception
"""
from __future__ import annotations

import os
import sys
import time

LOGTAIL_EVERY = 3.0     # seconds between tail polls (record.py default)

# R3 campaign-swap: the script_log is where the human-faction identity flows past the recorder, so
# the logs stream is where a new campaign is detected. scan_state_rows (the offline splitter's live
# kernel) yields each human-faction row + its byte offset in a chunk, letting us split a tail chunk
# byte-exactly at a campaign boundary. Imported best-effort: if unavailable, capture is unchanged.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "campaigns"))
try:
    from splitter import scan_state_rows            # noqa: E402
except Exception as e:                              # noqa: BLE001 -- degrade to single-campaign
    scan_state_rows = None
    sys.stderr.write("logs: scan_state_rows import failed (campaign-swap disabled) -> %s\n"
                     % repr(e)[:80])


def _append_tail(ctx, base, data: bytes) -> str:
    """Append `data` to <ctx.out_dir>/logs/<base>.tail (dir read FRESH so it follows a swap) and
    return the destination path. The dir is recomputed every call so that, after a mid-chunk
    campaign-swap re-points ctx.out_dir, the remaining bytes land in the NEW run dir's logs/."""
    d = os.path.join(ctx.out_dir, "logs")
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, base + ".tail")
    with open(dst, "ab") as f:
        f.write(data)
    return dst


def _write_scriptlog_chunk(ctx, src, chunk: bytes, on_state, swap) -> None:
    """Write one script_log tail chunk, splitting it byte-exactly at any campaign boundary.

    Walks the human-faction rows in the chunk; when one starts a new campaign (on_state -> True),
    flushes the bytes BEFORE that row to the current dir, performs the swap (re-points ctx.out_dir),
    then continues writing from the boundary row into the NEW dir. No bytes are dropped or
    duplicated; the boundary row and everything after it land in the new campaign's dir. A chunk
    with no boundary is written whole (the common case). Multiple boundaries in one 3s chunk (never
    seen in practice -- a campaign spans many turns) are each handled in order."""
    base = os.path.basename(src)
    pos = 0
    for faction, subculture, turn, line_off in scan_state_rows(chunk):
        try:
            needs_swap = on_state(faction, subculture, turn)
        except Exception as e:                       # a bad identity row must never break capture
            sys.stderr.write("logs: on_state failed -> %s\n" % repr(e)[:80])
            continue
        if not needs_swap:
            continue
        if line_off > pos:                           # pre-boundary bytes -> the current (old) dir
            dst = _append_tail(ctx, base, chunk[pos:line_off])
            ctx.emit({"t": ctx.now(), "kind": "log_tail", "src": src,
                      "bytes": line_off - pos, "dst": dst})
        pos = line_off
        try:
            swap()                                   # re-points ctx.out_dir -> fresh run dir
        except Exception as e:                       # swap failure must not lose the rest of the chunk
            sys.stderr.write("logs: campaign swap failed -> %s\n" % repr(e)[:80])
    if len(chunk) > pos:                             # remainder -> current dir (new one if we swapped)
        dst = _append_tail(ctx, base, chunk[pos:])
        ctx.emit({"t": ctx.now(), "kind": "log_tail", "src": src,
                  "bytes": len(chunk) - pos, "dst": dst})


def run(ctx, log_dirs, poll_every: float = LOGTAIL_EVERY, own_slack: float = 2.0) -> None:
    """Tail every *.txt / *.log under `log_dirs` until ctx.is_running() flips False. New files
    are captured from byte 0; pre-existing files are tailed from their current end. Tails ->
    <out_dir>/logs/<basename>.tail; each open/tail announced in events.jsonl as
    kind "log_open" / "log_tail" -- record.py parity.

    Args:
        ctx: The manager context (see module docstring).
        log_dirs: Directories to scan for game logs (the manager passes GAME_DIR + APPDATA_LOGS).
        poll_every: Seconds between scans.
        own_slack: A file created up to this many seconds before we started still counts as
            "ours" (from byte 0). record.py uses 2.0; a test can set 0.0 for a crisp cutoff.
    """
    os.makedirs(os.path.join(ctx.out_dir, "logs"), exist_ok=True)
    off = {}
    cutoff = time.time() - own_slack        # "created at/after this" => entirely ours

    # R3 hooks. Present only on the real manager Ctx; a minimal FakeCtx (or a manager without a
    # tracker) lacks them -> no scanning, capture behaves exactly as before. Both hooks + the
    # scanner are required to enable the byte-exact campaign split.
    on_state = getattr(ctx, "on_state", None)
    swap = getattr(ctx, "swap", None)
    split_campaigns = bool(scan_state_rows and on_state and swap)

    def is_ours(src: str) -> bool:
        try:
            return os.path.getctime(src) >= cutoff
        except OSError as e:
            sys.stderr.write("logs: is_ours ctime %s -> %s\n" % (os.path.basename(src), repr(e)[:60]))
            return False

    def targets() -> list:
        out = []
        for base in log_dirs:
            try:
                for f in os.listdir(base):
                    if f.lower().endswith((".txt", ".log")):
                        out.append(os.path.join(base, f))
            except Exception as e:
                sys.stderr.write("logs: cannot list %s -> %s\n" % (base, repr(e)[:60]))
        return out

    while ctx.is_running():
        try:
            for src in targets():
                try:
                    if not os.path.exists(src):
                        continue
                    sz = os.path.getsize(src)
                    start = off.get(src)
                    if start is None:
                        # created since we started -> entirely ours (byte 0); pre-existing ->
                        # skip its history (start at current end), tail appends from here.
                        begin = 0 if is_ours(src) else sz
                        off[src] = begin
                        ctx.emit({"t": ctx.now(), "kind": "log_open", "src": src,
                                  "from_byte": begin, "size": sz, "ours": begin == 0})
                        if begin == sz:
                            continue
                    if sz < off[src]:                 # rotated / truncated -> re-read from 0
                        off[src] = 0
                    if sz > off[src]:
                        with open(src, "rb") as f:    # binary: text-mode seek corrupts offsets
                            f.seek(off[src])
                            chunk = f.read(sz - off[src])
                        # A script_log carries the human-faction identity -> scan it for campaign
                        # boundaries and split byte-exactly across dirs. Any other log (or when the
                        # swap machinery is absent) is written whole. dst dir is read fresh so it
                        # follows a swap that a PRIOR src in this same poll may have triggered.
                        if split_campaigns and "script_log" in os.path.basename(src).lower():
                            _write_scriptlog_chunk(ctx, src, chunk, on_state, swap)
                        else:
                            dst = _append_tail(ctx, os.path.basename(src), chunk)
                            ctx.emit({"t": ctx.now(), "kind": "log_tail", "src": src,
                                      "bytes": len(chunk), "dst": dst})
                        off[src] = sz
                except Exception as e:
                    ctx.on_error("logs:" + str(src), e)
        except Exception as e:
            ctx.on_error("logs", e)
        time.sleep(poll_every)
