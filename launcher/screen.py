"""ScreenBridge -- the PowerShell capture/restore/input primitives."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

try:
    import config as _config
    _REPO = _config.REPO
except Exception as e:                  # pragma: no cover
    _REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.stderr.write("screen: config import failed, using fallback repo path -> %s\n" % repr(e)[:80])

# capture.ps1 stdout: "client=(X,Y WxH)", "stats=(luma sat busy)", "bands=(top mid bot)"
_CLIENT_RE = re.compile(r"client=\((-?\d+),(-?\d+)\s+(\d+)x(\d+)\)")

_STATS_RE = re.compile(r"stats=\(luma=([\d.]+)\s+sat=([\d.]+)\s+busy=([\d.]+)\)")
FRAME_LUMA_MIN = 10.0
FRAME_SAT_MIN = 9.0
FRAME_SAT_MAX = 75.0
FRAME_BUSY_MIN = 2.5

_BANDS_RE = re.compile(r"bands=\(top=([\d.]+)\s+mid=([\d.]+)\s+bot=([\d.]+)\)")
LETTERBOX_BAND_MAX = 10.0
LETTERBOX_MID_MIN = 25.0


class ScreenBridge:
    """The ps/*.ps1 capture/restore/input bridge plus frame-stat helpers."""

    PS_DIR = os.path.join(_REPO, "ps")
    SHOTS_DIR = os.path.join(_REPO, "logs", "launch_shots")

    # fallback only -- clicks map through the live client rect
    SCREEN_W = 2560
    SCREEN_H = 1440

    def __init__(self, log=None) -> None:
        self._t0 = None
        self._client = None
        self._stats = None
        self._bands = None
        if log is not None:
            self._log = log

    def _log(self, msg: str) -> None:
        t = 0.0 if self._t0 is None else (time.time() - self._t0)
        print("[startup +%5.1fs] %s" % (t, msg), flush=True)

    def _run_ps(self, script: str, *args, **kw) -> subprocess.CompletedProcess | None:
        """Run a ps/*.ps1 helper. Returns the CompletedProcess, or None if it raised."""
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", os.path.join(self.PS_DIR, script)]
        cmd += [str(a) for a in args]
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=kw.get("timeout", 60))
        except Exception as exc:                     # noqa: BLE001
            self._log("ps %s failed: %s" % (script, exc))
            return None

    def _input(self, action: str, a=None, b=None, d=None) -> subprocess.CompletedProcess | None:
        """input.ps1: mouse at absolute pixels, or a key (needs the window in the foreground)."""
        args = [action]
        for v in (a, b, d):
            if v is not None:
                args.append(v)
        return self._run_ps("input.ps1", *args, timeout=30)

    def _client_rect(self) -> tuple[int, int, int, int]:
        """Live client rect (origin_x, origin_y, w, h) in screen pixels; capture it if uncached."""
        if self._client is None:
            self._screenshot_to(os.path.join(self.SHOTS_DIR, "_rect.png"))
        if self._client is None:
            return (0, 0, self.SCREEN_W, self.SCREEN_H)
        return self._client

    def _map_frac(self, fx: float, fy: float) -> tuple[int, int]:
        """Map a client-area fraction to an absolute screen pixel."""
        ox, oy, w, h = self._client_rect()
        return int(round(ox + fx * w)), int(round(oy + fy * h))

    def _click_frac(self, fx: float, fy: float) -> subprocess.CompletedProcess | None:
        """Click at a client-area fraction."""
        sx, sy = self._map_frac(fx, fy)
        return self._input("click", sx, sy)

    def _restore(self) -> subprocess.CompletedProcess | None:
        """Un-minimise and foreground the game window."""
        return self._run_ps("restore.ps1", timeout=30)

    def _nudge(self) -> subprocess.CompletedProcess | None:
        """Move the cursor to a neutral spot without clicking."""
        sx, sy = self._map_frac(0.5, 0.90)
        return self._input("move", sx, sy)

    def _screenshot_to(self, path: str) -> str:
        """Capture to `path`, caching the client rect and frame stats it prints. Returns `path`."""
        self._stats = None
        self._bands = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError as exc:
            self._log("WARN could not create shot dir for %s: %s" % (path, repr(exc)[:60]))
        r = self._run_ps("capture.ps1", path, timeout=30)
        if r is not None:
            out = (r.stdout or "") + "\n" + (r.stderr or "")
            m = _CLIENT_RE.search(out)
            if m:
                self._client = (int(m.group(1)), int(m.group(2)),
                                int(m.group(3)), int(m.group(4)))
            s = _STATS_RE.search(out)
            if s:
                self._stats = (float(s.group(1)), float(s.group(2)), float(s.group(3)))
            b = _BANDS_RE.search(out)
            if b:
                self._bands = (float(b.group(1)), float(b.group(2)), float(b.group(3)))
        return path

    def _frame_ok(self) -> bool:
        """True if the last frame's stats are inside the FRAME_* bounds. True when no stats."""
        s = self._stats
        if s is None:
            return True
        luma, sat, busy = s
        if luma < FRAME_LUMA_MIN:
            return False
        if sat < FRAME_SAT_MIN:
            return False
        if sat > FRAME_SAT_MAX:
            return False
        if busy < FRAME_BUSY_MIN:
            return False
        return True

    def looks_letterboxed(self, refresh: bool = True) -> bool | None:
        """True if the band stats show dark top+bottom around a lit middle. None = unknown."""
        if refresh or self._bands is None:
            self._screenshot_to(os.path.join(self.SHOTS_DIR, "_letterbox.png"))
        if self._bands is None:
            return None
        top, mid, bot = self._bands
        return (top <= LETTERBOX_BAND_MAX and bot <= LETTERBOX_BAND_MAX
                and mid >= LETTERBOX_MID_MIN)
