"""twapi.screen -- the PowerShell/vision bridge primitives (ScreenBridge).

Extracted from api.GameManager in R1 (method bodies moved text-identically) so that
Campaign's UI driver no longer has to construct a GameManager just to borrow the
capture/restore/input bridges -- this kills the Campaign -> GameManager circular edge.
GameManager keeps identical-signature delegating methods onto a ScreenBridge, so
launcher code and external callers see no difference.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

# Absolute repo path for the ps/ helper scripts + screenshot churn dir. config.py is the
# one file allowed to hold paths; fall back to its literal value so import/py_compile
# succeed even if config isn't importable (mirrors api.py's config block).
try:
    import config as _config
    _REPO = _config.REPO
except Exception as e:                  # pragma: no cover - fallback mirrors config.py
    _REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.stderr.write("screen: config import failed, using fallback repo path -> %s\n" % repr(e)[:80])

# capture.ps1 prints "... client=(X,Y WxH) -> <path>" on stdout; X,Y are the CLIENT top-left
# in SCREEN pixels and WxH the real client size -> used to map click fractions to screen coords.
_CLIENT_RE = re.compile(r"client=\((-?\d+),(-?\d+)\s+(\d+)x(\d+)\)")

# capture.ps1 ALSO prints "stats=(luma=.. sat=.. busy=..)" per frame -- three cheap content
# statistics used to REJECT screensaver / wallpaper-slideshow overlays that were captured INSTEAD
# of the game (solid black, a mono logo, a hyper-saturated vendor image) and poisoned every vision
# gate in attempt 1. A frame is trusted as REAL GAME CONTENT only inside these bounds. Measured on
# the real frames: black luma=2.6 busy=0.0; mono GW logo sat=5.6; "Intel Core" sat=123; every real
# WH3 frame luma~81 sat~19 busy~11.5 -- so each bound clears the screensaver frames with wide
# margin, and a rejected frame is SAFE (we just re-capture / fail-fast), whereas trusting a
# screensaver frame is FATAL, so the bounds are deliberately generous (few false rejects).
_STATS_RE = re.compile(r"stats=\(luma=([\d.]+)\s+sat=([\d.]+)\s+busy=([\d.]+)\)")
FRAME_LUMA_MIN = 10.0     # below => near-black screensaver          (black=2.6, real~81)
FRAME_SAT_MIN = 9.0       # below => greyscale mono logo / black     (logo=5.6, real~19)
FRAME_SAT_MAX = 75.0      # above => single hyper-saturated overlay  (intel=123, real~19)
FRAME_BUSY_MIN = 2.5      # below => flat solid fill                 (black=0.0, real~11.5)

# capture.ps1 ALSO prints "bands=(top=.. mid=.. bot=..)" -- mean luma of the top/mid/bottom row
# bands of the same 48x27 downscale (P7). A playing cinematic LETTERBOXES the frame: near-black
# bars top+bottom (~12% each; downscale rows 0-2 / 24-26 sit fully inside them) around a lit
# picture (rows 10-16) -- a signature the whole-frame stats cannot see. BAND_MAX is generous vs
# the measured pure black (2.6) yet far under any lit content; MID_MIN requires a real picture
# BETWEEN the bands so a plain black screen (loading) never reads as letterboxed. Measured on the
# live campaign map (turn-2 Cathay): top=86.9 mid=70.4 bot=86.8 -- nowhere near the bounds. UNOBSERVED
# on a real cinematic so far: the first live encounter (Phase B faction intros) lands in the tlog
# with the raw bands for calibration; until then this is CORROBORATION ONLY (never the sole
# trigger for any click -- see twapi/interrupts.py _handle_cutscene).
_BANDS_RE = re.compile(r"bands=\(top=([\d.]+)\s+mid=([\d.]+)\s+bot=([\d.]+)\)")
LETTERBOX_BAND_MAX = 10.0
LETTERBOX_MID_MIN = 25.0


class ScreenBridge:
    """Holds the ps/*.ps1 bridge + frame-stat helpers (capture/restore/input/click-map).

    State it owns: the cached live client rect (self._client) and the last captured
    frame's content stats (self._stats) -- exactly the caching/refresh semantics the
    same code had on GameManager (a caller resets self._client to force a live re-read).
    An optional `log` callable overrides the default _log so GameManager's launch-log
    timestamps keep working unchanged when it delegates here.
    """

    PS_DIR = os.path.join(_REPO, "ps")
    SHOTS_DIR = os.path.join(_REPO, "logs", "launch_shots")   # churn (temp diff frames)

    # Fallback render size ONLY (used if the live client rect can't be read). The game runs
    # borderless at ~2560x1440, but clicks are NEVER computed from this constant -- every click
    # is mapped through the LIVE client rect read from capture.ps1 (see _client_rect/_map_frac).
    SCREEN_W = 2560
    SCREEN_H = 1440

    def __init__(self, log=None) -> None:
        """Initialise the bridge with empty cached rect/stats/bands state.

        Args:
            log: Optional callable taking one message string; when given it
                replaces the default _log so a host object's logging format
                (e.g. GameManager's launch-log timestamps) is kept.

        Returns:
            None
        """
        self._t0 = None
        # Live CLIENT rect (origin_x, origin_y, w, h) in SCREEN pixels; populated from
        # capture.ps1 stdout on every screenshot. None until the first capture.
        self._client = None
        # Content stats (luma, sat, busy) of the LAST captured frame, parsed from capture.ps1;
        # used by _frame_ok to reject screensaver/overlay captures. None until the first capture.
        self._stats = None
        # Letterbox band stats (top, mid, bot mean luma) of the LAST captured frame (P7);
        # used by looks_letterboxed to corroborate a playing cinematic. None until captured.
        self._bands = None
        if log is not None:
            self._log = log

    # ---- logging (mirrors the [startup +Xs] format of the launch logs) ---------------
    def _log(self, msg: str) -> None:
        """Print a timestamped launch-log line.

        Args:
            msg: The message to print after the "[startup +Xs]" prefix.

        Returns:
            None
        """
        t = 0.0 if self._t0 is None else (time.time() - self._t0)
        print("[startup +%5.1fs] %s" % (t, msg), flush=True)

    # ---- powershell bridges ----------------------------------------------------------
    def _run_ps(self, script: str, *args, **kw) -> subprocess.CompletedProcess | None:
        """Run one of the ps/*.ps1 helper scripts, returning the CompletedProcess (or None).

        Args:
            script: Filename of a script inside PS_DIR, e.g. "capture.ps1".
            *args: Positional arguments passed to the script (str()-ified).
            **kw: Only "timeout" is honoured -- subprocess timeout in seconds
                (default 60).

        Returns:
            The subprocess.CompletedProcess (stdout/stderr captured as text),
            or None if the run raised (timeout, missing powershell, ...) --
            errors are logged, never propagated.
        """
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", os.path.join(self.PS_DIR, script)]
        cmd += [str(a) for a in args]
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=kw.get("timeout", 60))
        except Exception as exc:                     # noqa: BLE001 - never let a helper crash launch
            self._log("ps %s failed: %s" % (script, exc))
            return None

    def _input(self, action: str, a=None, b=None, d=None) -> subprocess.CompletedProcess | None:
        """input.ps1: mouse (absolute-pixel, focus-independent) or key (needs foreground).

        Args:
            action: input.ps1 action verb, e.g. "click", "move", "key".
            a: First action argument (e.g. screen x pixel), or None to omit.
            b: Second action argument (e.g. screen y pixel), or None to omit.
            d: Third action argument, or None to omit.

        Returns:
            The CompletedProcess from _run_ps, or None if the helper failed.
        """
        args = [action]
        for v in (a, b, d):
            if v is not None:
                args.append(v)
        return self._run_ps("input.ps1", *args, timeout=30)

    # ---- coordinate mapping: screenshot pixels are CLIENT-relative, clicks are SCREEN-absolute
    def _client_rect(self) -> tuple[int, int, int, int]:
        """Live CLIENT rect (origin_x, origin_y, w, h) in SCREEN pixels.

        capture.ps1 screenshots the CLIENT area: image origin (0,0) == client top-left, whose
        SCREEN position is (origin_x, origin_y) (via ClientToScreen), and the image is
        (w x h) == the real client size. input.ps1 clicks in ABSOLUTE SCREEN pixels
        (SetCursorPos). So a click computed as a fraction (fx,fy) of a screenshot MUST be
        mapped: screen = origin + frac*size. This reads that rect LIVE (never the hardcoded
        2560x1440) so a non-(0,0) origin or a DPI-scaled client size self-corrects, as long as
        capture and cursor share the same units (they do -- same PowerShell host).

        Returns:
            (origin_x, origin_y, w, h) in screen pixels -- the cached rect,
            freshly captured if not cached, or the (0, 0, SCREEN_W, SCREEN_H)
            fallback if the capture failed to yield one.
        """
        if self._client is None:
            self._screenshot_to(os.path.join(self.SHOTS_DIR, "_rect.png"))
        if self._client is None:                     # capture failed -> safe fallback
            return (0, 0, self.SCREEN_W, self.SCREEN_H)
        return self._client

    def _map_frac(self, fx: float, fy: float) -> tuple[int, int]:
        """Map a client-fraction (fx,fy) to an absolute SCREEN pixel via the live client rect.

        Args:
            fx: Horizontal fraction of the client area, 0.0 (left) to 1.0 (right).
            fy: Vertical fraction of the client area, 0.0 (top) to 1.0 (bottom).

        Returns:
            (sx, sy): the rounded absolute screen-pixel coordinates.
        """
        ox, oy, w, h = self._client_rect()
        return int(round(ox + fx * w)), int(round(oy + fy * h))

    def _click_frac(self, fx: float, fy: float) -> subprocess.CompletedProcess | None:
        """Click at a client-fraction position, mapped to absolute screen pixels.

        Args:
            fx: Horizontal client fraction (0.0-1.0).
            fy: Vertical client fraction (0.0-1.0).

        Returns:
            The CompletedProcess from input.ps1, or None if the helper failed.
        """
        sx, sy = self._map_frac(fx, fy)
        return self._input("click", sx, sy)

    def _restore(self) -> subprocess.CompletedProcess | None:
        """Un-minimise + foreground the game window (restore.ps1, which also disables the
        screensaver + display-sleep) before UI operations.

        Returns:
            The CompletedProcess from restore.ps1, or None if the helper failed.
        """
        return self._run_ps("restore.ps1", timeout=30)

    def _nudge(self) -> subprocess.CompletedProcess | None:
        """Move the cursor (NO click) to a neutral empty spot below the menu -- any mouse
        movement dismisses an already-running screensaver, and this touches no UI control.
        Used when a capture was rejected as a screensaver/overlay frame so the next capture
        can grab the real game underneath.

        Returns:
            The CompletedProcess from input.ps1, or None if the helper failed.
        """
        sx, sy = self._map_frac(0.5, 0.90)
        return self._input("move", sx, sy)

    def _screenshot_to(self, path: str) -> str:
        """Capture a screenshot to `path` (capture.ps1). Also parses the LIVE client rect it
        prints ('client=(x,y wxh)') and caches it on self._client so click mapping always uses
        the current on-screen geometry, and the per-frame content stats ('stats=(luma sat busy)')
        onto self._stats so _frame_ok can reject screensaver captures. Returns the path.

        Args:
            path: Destination PNG file path; parent directories are created
                if needed.

        Returns:
            `path`, unchanged (even if the capture itself failed -- caches
            simply stay None in that case).
        """
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

    # ---- screensaver-overlay rejection: only trust a capture that looks like GAME content ------
    def _frame_ok(self) -> bool:
        """True if the LAST captured frame's content stats fall inside the real-game-content
        bounds. Rejects the screensaver/wallpaper-slideshow overlays (solid black, mono logo,
        hyper-saturated vendor image) that CopyFromScreen intermittently grabbed instead of the
        game and that poisoned every vision gate in attempt 1. If capture emitted no stats we do
        NOT block (fall back to the other gates) so a stats-less capture can never hard-stall.

        Returns:
            True if the (luma, sat, busy) stats of the last capture pass all
            FRAME_* bounds, or if no stats were emitted (fail-open); False if
            any bound is violated (frame rejected as screensaver/overlay).
        """
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

    # ---- letterbox signature (P7 cutscene corroboration) ---------------------------------------
    def looks_letterboxed(self, refresh: bool = True) -> bool | None:
        """True if the frame shows the cinematic LETTERBOX signature: near-black top+bottom
        bands around a lit mid band (see the _BANDS_RE commentary for the bounds). `refresh`
        (default) captures a FRESH frame first; the classification then reads the parsed band
        stats. Returns None when band stats are unavailable (capture failed / old capture.ps1
        output) -- callers must treat None as UNKNOWN, never as a detection. Deliberately NOT
        gated on _frame_ok: a letterboxed cinematic frame is exactly the non-standard frame
        this classifies.

        Args:
            refresh: If True (default), capture a fresh frame before
                classifying; if False, reuse the cached band stats (still
                captures once if none are cached).

        Returns:
            True if the letterbox signature is present, False if not, or
            None when band stats are unavailable (UNKNOWN -- never a
            detection).
        """
        if refresh or self._bands is None:
            self._screenshot_to(os.path.join(self.SHOTS_DIR, "_letterbox.png"))
        if self._bands is None:
            return None
        top, mid, bot = self._bands
        return (top <= LETTERBOX_BAND_MAX and bot <= LETTERBOX_BAND_MAX
                and mid >= LETTERBOX_MID_MIN)
