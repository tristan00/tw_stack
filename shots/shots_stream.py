r"""shots capture stream -- full-desktop JPEG frames.

run(ctx, ...) writes frames to <out_dir>/shots/NNNNN.jpg and emits a "shot" row for each.
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

SHOT_EVERY = 60.0
SHOT_QUALITY = 90

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
        return "", 0


def run(ctx, grab=None, foreground=None, shot_every: float = SHOT_EVERY,
        quality: int = SHOT_QUALITY) -> None:
    """Capture one frame every `shot_every` seconds plus one shortly after every click, until ctx.is_running() flips False."""
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
                time.sleep(0.18)               # let the UI respond before grabbing what the click opened
            if not clicked and (time.time() - last) < shot_every:
                continue
            n += 1
            # dir read FRESH each frame so frames follow a mid-run campaign swap of ctx.out_dir.
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
