from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

_u32 = ctypes.windll.user32

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_ESCAPE = 0x1B
VK_CONTROL = 0x11
VK_A = 0x41
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_END = 0x23
VK_DELETE = 0x2E


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1


def _send(*inputs):
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    return _u32.SendInput(n, arr, ctypes.sizeof(_INPUT))


def screen():
    return _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1)


def move(x, y):
    w, h = screen()
    ax = int(x * 65535 / (w - 1))
    ay = int(y * 65535 / (h - 1))
    mi = _MOUSEINPUT(ax, ay, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
    _send(_INPUT(type=INPUT_MOUSE, mi=mi))


def click(x, y, settle=0.35):
    move(x, y)
    time.sleep(0.08)
    down = _MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
    up = _MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
    _send(_INPUT(type=INPUT_MOUSE, mi=down))
    time.sleep(0.05)
    _send(_INPUT(type=INPUT_MOUSE, mi=up))
    time.sleep(settle)


def key(vk, settle=0.12):
    d = _KEYBDINPUT(vk, 0, 0, 0, None)
    u = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)
    _send(_INPUT(type=INPUT_KEYBOARD, ki=d))
    time.sleep(0.03)
    _send(_INPUT(type=INPUT_KEYBOARD, ki=u))
    time.sleep(settle)


def text(s, per=0.02):
    for ch in s:
        d = _KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, None)
        u = _KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
        _send(_INPUT(type=INPUT_KEYBOARD, ki=d))
        _send(_INPUT(type=INPUT_KEYBOARD, ki=u))
        time.sleep(per)
