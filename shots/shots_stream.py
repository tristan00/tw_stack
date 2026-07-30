r"""shots capture stream -- full-desktop JPEG frames.

Ported faithfully from record.py:t_shots. GAME-INDEPENDENT: it grabs the whole desktop (not
the game window), one frame every `shot_every` seconds AND one 0.18s after every click
(signalled via ctx.shot_req, which the input stream raises). The frame grabber and the
foreground reader are injectable (defaults = real PIL / Win32) so an offline test can feed a
tiny in-memory image and never touch the real screen or the game.

Contract with the manager -- run(ctx, ...) where ctx duck-types:
    ctx.emit(row)              append one row to the shared events.jsonl
    ctx.out_dir                the run directory (frames land in <out_dir>/shots/)
    ctx.now() -> float         seconds since the shared T0
    ctx.is_running() -> bool   True while capture should continue
    ctx.shot_req               threading.Event; set by the input stream on every click
    ctx.on_error(where, exc)   record a caught (never fatal) exception
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

SHOT_EVERY = 60.0       # seconds between idle frames (1/60s default -- was 2.5, far too many)
SHOT_QUALITY = 90       # high: offline click-resolution has to read small UI text

_u32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None


def _real_foreground() -> tuple[str, int]:
    """(title, pid) of the focused window via Win32; ("", 0) on any failure."""
    try:
        h = _u32.GetForegroundWindow()
        n = _u32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(h, buf, n + 1)
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return buf.value, pid.value
    except Exception:
        return "", 0  # intentional: Win32 fg query, per-shot poll -> ("",0) on transient fail (no per-poll log)


def run(ctx, grab=None, foreground=None, shot_every: float = SHOT_EVERY,
        quality: int = SHOT_QUALITY) -> None:
    """Capture frames until ctx.is_running() flips False. One frame every `shot_every` sec plus
    one 0.18s after every click. Frames -> <out_dir>/shots/NNNNN.jpg; each announced in
    events.jsonl as {"t","kind":"shot","n","file","size","window","trigger"} -- record.py parity.

    Args:
        ctx: The manager context (see module docstring).
        grab: Zero-arg callable returning a PIL Image; None uses PIL ImageGrab.grab (full desktop).
        foreground: Zero-arg callable returning (title, pid); None uses the real Win32 reader.
        shot_every: Seconds between idle frames.
        quality: JPEG quality.
    """
    if grab is None:
        try:
            from PIL import ImageGrab
            grab = ImageGrab.grab
        except Exception as e:
            ctx.on_error("shots-import", e)
            return
    foreground = foreground or _real_foreground
    n, last = 0, 0.0
    os.makedirs(os.path.join(ctx.out_dir, "shots"), exist_ok=True)
    while ctx.is_running():
        try:
            clicked = ctx.shot_req.wait(0.1)
            if clicked:
                ctx.shot_req.clear()
                time.sleep(0.18)               # let the UI respond, then capture what the click opened
            if not clicked and (time.time() - last) < shot_every:
                continue
            n += 1
            # dir read FRESH each frame so a mid-run campaign-swap (ctx.out_dir re-pointed) lands
            # subsequent frames in the new run dir; n stays monotonic across the swap.
            d = os.path.join(ctx.out_dir, "shots")
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "%05d.jpg" % n)
            img = grab()
            img.convert("RGB").save(p, "JPEG", quality=quality, subsampling=0)
            last = time.time()
            ctx.emit({"t": ctx.now(), "kind": "shot", "n": n, "file": p,
                      "size": list(img.size), "window": foreground()[0],
                      "trigger": "click" if clicked else "interval"})
        except Exception as e:
            ctx.on_error("shots", e)
            time.sleep(0.5)
