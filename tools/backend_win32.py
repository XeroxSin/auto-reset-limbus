"""Windows backend: GDI screen capture and SendInput, through ctypes.

Capture is a BitBlt of the desktop (the same approach as Charge Grinder), so the
game must be visible and uncovered when the shot fires - whatever is on top of
it is what ends up in the frame.

Input is sent with SendInput as relative mouse counts and keyboard scancodes
(games generally read scancodes, not virtual keys). Charge Grinder's own
bridge.dll (Logitech / Razer virtual devices) is not replicated.
"""
import atexit
import ctypes
from ctypes import wintypes

import numpy as np

NAME = "windows"
KEYS = ("esc", "enter", "space", "tab")

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0002, 0x0008

SPI_GETMOUSE, SPI_SETMOUSE = 0x0003, 0x0004
SPI_GETMOUSESPEED, SPI_SETMOUSESPEED = 0x0070, 0x0071

# Set-1 scancodes
SCANCODES = {"esc": 0x01, "enter": 0x1C, "space": 0x39, "tab": 0x0F}

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


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


def _sig(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = argtypes


_sig(user32.FindWindowW, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
_sig(user32.GetClientRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_sig(user32.ClientToScreen, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.POINT))
_sig(user32.IsIconic, wintypes.BOOL, wintypes.HWND)
_sig(user32.IsWindow, wintypes.BOOL, wintypes.HWND)
_sig(user32.GetForegroundWindow, wintypes.HWND)
_sig(user32.GetDC, wintypes.HDC, wintypes.HWND)
_sig(user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
_sig(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_sig(gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int)
_sig(gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_sig(gdi32.BitBlt, wintypes.BOOL, wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
_sig(gdi32.GetDIBits, ctypes.c_int, wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
     wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT)
_sig(gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
_sig(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)


# --- Window and capture ---------------------------------------------------------

def set_dpi_aware():
    """Without this, display scaling makes Windows report and capture scaled pixels."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def find_window(title):
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        raise RuntimeError(f"Window '{title}' not found - is the game running?")
    return hwnd


def window_exists(win):
    return bool(user32.IsWindow(win))


def is_foreground(win):
    return user32.GetForegroundWindow() == win


def foreground_title():
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def client_rect(win):
    """The window's client area in screen coordinates."""
    if user32.IsIconic(win):
        raise RuntimeError("Game window is minimized")
    rc = wintypes.RECT()
    user32.GetClientRect(win, ctypes.byref(rc))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(win, ctypes.byref(pt))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        raise RuntimeError("Game window has an empty client area")
    return pt.x, pt.y, w, h


def grab(rect):
    """BitBlt a screen rect into a (h, w, 3) BGR array."""
    x, y, w, h = rect
    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        if not gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, x, y, SRCCOPY):
            raise ctypes.WinError(ctypes.get_last_error())
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # negative = top-down rows
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        buf = np.empty((h, w, 4), dtype=np.uint8)
        if gdi32.GetDIBits(mem_dc, bmp, 0, h, buf.ctypes.data, ctypes.byref(bmi), DIB_RGB_COLORS) != h:
            raise RuntimeError("GetDIBits failed")
        return np.ascontiguousarray(buf[:, :, :3])
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


# --- Input ----------------------------------------------------------------------

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
