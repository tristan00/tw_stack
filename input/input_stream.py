from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

MOVE_HZ = 20

MOUSE = {0x01: "L", 0x02: "R", 0x04: "M", 0x05: "X1", 0x06: "X2"}

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
    p = wintypes.POINT()
    _u32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _real_foreground() -> tuple[str, int]:
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


def _real_keystate(vk: int) -> bool:
    return bool(_u32.GetAsyncKeyState(vk) & 0x8000)


class Probes:

    def __init__(self, cursor=_real_cursor, foreground=_real_foreground, keystate=_real_keystate):
        self.cursor = cursor
        self.foreground = foreground
        self.keystate = keystate


def run(ctx, probes: Probes | None = None) -> None:
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
        except Exception as e:
            ctx.on_error("input", e)
            time.sleep(0.2)
