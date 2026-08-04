r"""vision.py -- read the screen without the bus: is the picture moving?"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time

PS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ps")
CAPTURE = os.path.join(PS_DIR, "capture.ps1")

_STATS_RE = re.compile(r"stats=\(luma=([\d.]+)\s+sat=([\d.]+)\s+busy=([\d.]+)\)")
_BANDS_RE = re.compile(r"bands=\(top=([\d.]+)\s+mid=([\d.]+)\s+bot=([\d.]+)\)")

MOVE_EPS = 0.15

FRAME_LUMA_MIN = 10.0
FRAME_SAT_MIN = 9.0
FRAME_SAT_MAX = 75.0
FRAME_BUSY_MIN = 2.5


def is_game_frame(f):
    """True if the frame's stats fall inside the game-content bounds."""
    return (f is not None
            and f["luma"] >= FRAME_LUMA_MIN
            and FRAME_SAT_MIN <= f["sat"] <= FRAME_SAT_MAX
            and f["busy"] >= FRAME_BUSY_MIN)


def frame(path=None, timeout=40):
    """One capture. Returns {luma,sat,busy,top,mid,bot,png} or None if the capture failed."""
    png = path or os.path.join(tempfile.gettempdir(), "twvision_%d.png" % int(time.time() * 1000))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", CAPTURE, png],
                           capture_output=True, text=True, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        sys.stderr.write("vision: capture failed -> %s\n" % repr(e)[:120])
        return None
    out = (r.stdout or "") + (r.stderr or "")
    s, b = _STATS_RE.search(out), _BANDS_RE.search(out)
    if not s:
        sys.stderr.write("vision: no stats in capture output -> %s\n" % out[:160].replace("\n", " "))
        return None
    d = {"luma": float(s.group(1)), "sat": float(s.group(2)), "busy": float(s.group(3)),
         "png": png}
    if b:
        d.update(top=float(b.group(1)), mid=float(b.group(2)), bot=float(b.group(3)))
    return d


def moving(samples=3, gap=1.0):
    """(bool|None, [frames]). None means the capture was unusable, not "not moving"."""
    frames = []
    for i in range(samples):
        f = frame()
        if not is_game_frame(f):
            return None, frames
        frames.append(f)
        if i + 1 < samples:
            time.sleep(gap)
    for a, b in zip(frames, frames[1:]):
        for k in ("luma", "sat", "busy"):
            if abs(a[k] - b[k]) > MOVE_EPS:
                return True, frames
    return False, frames


def verdict(samples=3, gap=1.0):
    """A one-line, log-safe summary for the stuck record."""
    mv, frames = moving(samples, gap)
    if not frames:
        return {"picture": "uncapturable"}
    last = frames[-1]
    return {"picture": ("moving" if mv else "frozen") if mv is not None else "uncapturable",
            "n_frames": len(frames),
            "luma": round(last["luma"], 2), "sat": round(last["sat"], 2),
            "busy": round(last["busy"], 2),
            "spread": {k: round(max(f[k] for f in frames) - min(f[k] for f in frames), 3)
                       for k in ("luma", "sat", "busy")},
            "png": last["png"]}


if __name__ == "__main__":
    import json
    print(json.dumps(verdict(), indent=2))
