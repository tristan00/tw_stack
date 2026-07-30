r"""input capture stream -- system-wide keyboard / mouse / focus.

Ported faithfully from record.py:t_input (the working v5 recorder). GAME-INDEPENDENT:
it polls Win32 for the whole desktop and never touches the game, so it can be built and
tested with no game running. The ONE addition vs the monolith is testability: the three
OS reads (cursor / foreground / key-state) are injectable `Probes`, defaulting to the real
Win32 calls -- so an offline test can drive deterministic input without synthesising real
system events.

SCOPE (unchanged from record.py): this is a SYSTEM-WIDE recorder -- every key in every
window, every mouse move/click anywhere, plus the focused window title. Never narrowed at
write time; that is the user's explicit call, not the recorder's.

Contract with the manager -- run(ctx) where ctx duck-types:
    ctx.emit(row: dict)        append one row to the shared events.jsonl
    ctx.now() -> float         seconds since the shared T0 clock
    ctx.is_running() -> bool   True while capture should continue
    ctx.shot_req               threading.Event; set on every mouse_down (couples input->shots)
    ctx.on_error(where, exc)   record a caught (never fatal) exception
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

MOVE_HZ = 20                      # max mouse-move samples/sec (matches record.py)

MOUSE = {0x01: "L", 0x02: "R", 0x04: "M", 0x05: "X1", 0x06: "X2"}

# Full VK map. Unknown codes record as vk_<n> -- never a crash, never a guess. (record.py parity)
VK = {0x08: "BACKSPACE", 0x09: "TAB", 0x0D: "ENTER", 0x10: "SHIFT", 0x11: "CTRL", 0x12: "ALT",
      0x13: "PAUSE", 0x14: "CAPS", 0x1B: "ESC", 0x20: "SPACE", 0x21: "PGUP", 0x22: "PGDN",
      0x23: "END", 0x24: "HOME", 0x25: "LEFT", 0x26: "UP", 0x27: "RIGHT", 0x28: "DOWN",
      0x2C: "PRTSC", 0x2D: "INS", 0x2E: "DEL", 0x5B: "LWIN", 0x5C: "RWIN",
      0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`",
      0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'"}
for _i in range(10):
    VK[0x30 + _i] = str(_i)
for _i in range(26):
    VK[0x41 + _i] = chr(ord("A") + _i)
for _i in range(12):
    VK[0x70 + _i] = "F%d" % (_i + 1)
for _i in range(10):
    VK[0x60 + _i] = "NUM%d" % _i

_u32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None


def _real_cursor() -> tuple[int, int]:
    """Current mouse position (x, y) in screen-absolute pixels via Win32 GetCursorPos."""
    p = wintypes.POINT()
    _u32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _real_foreground() -> tuple[str, int]:
    """(title, pid) of the focused window. Pure Win32 -- no game knowledge; ("", 0) on failure."""
    try:
        h = _u32.GetForegroundWindow()
        n = _u32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(h, buf, n + 1)
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return buf.value, pid.value
    except Exception:
        return "", 0  # intentional: Win32 fg query, ~125Hz poll -> ("",0) on transient fail (no per-tick log)


def _real_keystate(vk: int) -> bool:
    """True if virtual-key `vk` is currently pressed (high bit of GetAsyncKeyState)."""
    return bool(_u32.GetAsyncKeyState(vk) & 0x8000)


class Probes:
    """The three injectable OS reads. Defaults are the real Win32 calls (identical behaviour
    to record.py); a test passes fakes to drive deterministic input offline."""

    def __init__(self, cursor=_real_cursor, foreground=_real_foreground, keystate=_real_keystate):
        self.cursor = cursor
        self.foreground = foreground
        self.keystate = keystate


def run(ctx, probes: Probes | None = None) -> None:
    """Poll every key, mouse button, mouse move, and focus change, in every window, until
    ctx.is_running() flips False. Emits kinds "focus", "mouse_down", "mouse_up", "key_down",
    "key_up", "move" -- byte-for-byte the record.py:t_input row shapes. Sets ctx.shot_req on
    every mouse_down so the shots stream can grab the click's result.

    Args:
        ctx: The manager context (see module docstring).
        probes: Injectable OS reads; None uses the real Win32 Probes().
    """
    probes = probes or Probes()
    prev, last_move, last_pos, last_fg = {}, 0.0, None, None
    while ctx.is_running():
        try:
            t = time.time()
            fg = probes.foreground()
            if fg != last_fg:
                ctx.emit({"t": ctx.now(), "kind": "focus", "window": fg[0], "pid": fg[1]})
                last_fg = fg
            for vk, nm in MOUSE.items():
                d = probes.keystate(vk)
                if d and not prev.get(("m", vk)):
                    x, y = probes.cursor()
                    ctx.emit({"t": ctx.now(), "kind": "mouse_down", "button": nm,
                              "screen": [x, y], "window": fg[0]})
                    ctx.shot_req.set()
                elif (not d) and prev.get(("m", vk)):
                    x, y = probes.cursor()
                    ctx.emit({"t": ctx.now(), "kind": "mouse_up", "button": nm,
                              "screen": [x, y], "window": fg[0]})
                prev[("m", vk)] = d
            for vk in VK:
                d = probes.keystate(vk)
                if d and not prev.get(("k", vk)):
                    ctx.emit({"t": ctx.now(), "kind": "key_down",
                              "key": VK.get(vk, "vk_%d" % vk), "window": fg[0]})
                elif (not d) and prev.get(("k", vk)):
                    ctx.emit({"t": ctx.now(), "kind": "key_up",
                              "key": VK.get(vk, "vk_%d" % vk), "window": fg[0]})
                prev[("k", vk)] = d
            if t - last_move >= 1.0 / MOVE_HZ:
                p = probes.cursor()
                if p != last_pos:
                    ctx.emit({"t": ctx.now(), "kind": "move", "screen": [p[0], p[1]]})
                    last_pos = p
                last_move = t
            time.sleep(0.008)
        except Exception as e:                       # never fatal -- a bad poll must not kill capture
            ctx.on_error("input", e)
            time.sleep(0.2)
