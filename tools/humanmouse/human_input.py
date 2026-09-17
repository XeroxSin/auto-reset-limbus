"""Human-like mouse/keyboard input for Windows, driven by Charge Grinder's movement model.

Follows docs/human-mouse-movement.md: closed-loop moves planned by
movement/builder.py (endpoint scatter inside the target box, data-driven
duration, sub-movements, tremor), played back in real time as relative counts,
Gaussian press/hold times, and a focus fail-safe. Input is sent with SendInput;
Charge Grinder's own bridge.dll (Logitech / Razer virtual devices) is not
replicated.

The movement/ package is copied from Charge Grinder 3.5.0 (GPL-3.0).
"""
import atexit
import ctypes
import random
import time
from ctypes import wintypes

import numpy as np

from .movement.builder import build_trajectory
from .movement.inertia import get_inherited_velocity, update_inertia
from .movement.pointer_gain import execute_trajectory, update_pointer_scale

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0002, 0x0008

SPI_GETMOUSE, SPI_SETMOUSE = 0x0003, 0x0004
SPI_GETMOUSESPEED, SPI_SETMOUSESPEED = 0x0070, 0x0071

# Set-1 scancodes (games generally read scancodes, not virtual keys)
SCANCODES = {"esc": 0x01, "enter": 0x1C, "space": 0x39, "tab": 0x0F}

WINDOW_TITLE = "LimbusCompany"


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamH", wintypes.DWORD), ("wParamL", wintypes.DWORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)


def _send(inp):
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def send_mouse_rel(dx, dy):
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dx=int(dx), dy=int(dy), dwFlags=MOUSEEVENTF_MOVE)
    _send(inp)


def send_mouse_button(button, down):
    flags = {("left", True): MOUSEEVENTF_LEFTDOWN, ("left", False): MOUSEEVENTF_LEFTUP,
             ("right", True): MOUSEEVENTF_RIGHTDOWN, ("right", False): MOUSEEVENTF_RIGHTUP}[(button, down)]
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dwFlags=flags)
    _send(inp)


def send_key(key, down):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wScan=SCANCODES[key], dwFlags=KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP))
    _send(inp)


def get_position():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def virtual_screen_bounds():
    x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    return x, y, x + user32.GetSystemMetrics(78), y + user32.GetSystemMetrics(79)


def foreground_title():
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


# --- Pointer settings guard (1:1 relative motion) --------------------------------

class _MouseSettings:
    """Turns off 'Enhance pointer precision' and sets speed 10/20 so one relative
    count = one pixel. fWinIni=0: not persisted, so a crash can't leave it changed
    past logoff."""

    def __init__(self):
        self._saved = None

    def apply(self):
        if self._saved is not None:
            return
        accel = (ctypes.c_int * 3)()
        speed = ctypes.c_int()
        user32.SystemParametersInfoW(SPI_GETMOUSE, 0, accel, 0)
        user32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, ctypes.byref(speed), 0)
        self._saved = (list(accel), speed.value)
        user32.SystemParametersInfoW(SPI_SETMOUSE, 0, (ctypes.c_int * 3)(0, 0, 0), 0)
        user32.SystemParametersInfoW(SPI_SETMOUSESPEED, 0, ctypes.c_void_p(10), 0)

    def restore(self):
        if self._saved is None:
            return
        accel, speed = self._saved
        user32.SystemParametersInfoW(SPI_SETMOUSE, 0, (ctypes.c_int * 3)(*accel), 0)
        user32.SystemParametersInfoW(SPI_SETMOUSESPEED, 0, ctypes.c_void_p(speed), 0)
        self._saved = None


mouse_settings = _MouseSettings()
atexit.register(mouse_settings.restore)


# --- Fail-safe ------------------------------------------------------------------

class FocusLost(Exception):
    pass


def fail_safe_check():
    if WINDOW_TITLE not in foreground_title():
        mouse_settings.restore()
        raise FocusLost(foreground_title())


# --- Timing helpers (Charge Grinder's SAFE profile) -----------------------------

def _gauss_ms(median, iqr, lo, hi):
    return max(lo, min(hi, random.gauss(median, max(1.0, iqr / 1.349)))) / 1000.0


def click_hold():
    return _gauss_ms(90.0, 18.0, 38.0, 220.0)


def key_hold():
    return _gauss_ms(100.0, 31.0, 32.0, 260.0)


def jitter(base, lo=0.95, hi=1.2):
    return base * random.uniform(lo, hi) if base > 0 else base


# --- Movement -------------------------------------------------------------------

def _within(a, b, size):
    return abs(a[0] - b[0]) <= size[0] / 2 and abs(a[1] - b[1]) <= size[1] / 2


def move_to(x, y, tsize=(5.0, 5.0), curve=1.0, n_sub=None, duration=None, inertia=False,
            emit=send_mouse_rel, check=fail_safe_check):
    """Move to a screen-pixel target. tsize = (w, h) of the clickable area; the
    landing point is scattered inside it. emit/check can be swapped for dry runs."""
    check()
    mouse_settings.apply()
    min_x, min_y, max_x, max_y = virtual_screen_bounds()
    end = (min(max(int(round(x)), min_x), max_x - 1), min(max(int(round(y)), min_y), max_y - 1))
    start = get_position()
    if _within(start, end, tsize):
        return

    path, times = None, None
    for _ in range(6):  # a few correction attempts, as a person would make
        traj = build_trajectory(
            start, end,
            duration_override=duration,
            target_width=tsize[0], target_height=tsize[1],
            initial_velocity=get_inherited_velocity() if inertia else None,
            curviness=curve,
            n_submovements=n_sub,
        )
        path = np.asarray(traj["points"], dtype=float)
        path[:, 0] = np.clip(path[:, 0], min_x, max_x - 1)
        path[:, 1] = np.clip(path[:, 1], min_y, max_y - 1)
        times = traj["times"]

        raw_delta = execute_trajectory(None, path, times, emit_func=lambda _dev, dx, dy: emit(dx, dy))
        start = get_position()
        if not np.any(np.abs(raw_delta) > 15.0) or _within(start, end, tsize):
            break
        # Cursor missed: learn the counts->pixels gain curve and re-plan from here
        update_pointer_scale(raw_delta, path[0], start)
        check()

    if path is not None:
        update_inertia(path, times)


def click(x=None, y=None, tsize=(5.0, 5.0), button="left", delay=0.03):
    fail_safe_check()
    delay = jitter(delay)
    if x is not None and y is not None:
        time.sleep(jitter(delay + 0.02))
        move_to(x, y, tsize=tsize)
    fail_safe_check()
    send_mouse_button(button, True)
    time.sleep(click_hold())
    send_mouse_button(button, False)
    time.sleep(random.uniform(delay, delay + 0.05))


def press(key, delay=0.09):
    time.sleep(jitter(delay))
    fail_safe_check()
    send_key(key, True)
    time.sleep(key_hold())
    send_key(key, False)
