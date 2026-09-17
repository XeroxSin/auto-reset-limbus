"""Capture the Limbus Company game area to PNG after a countdown.

Grabs the largest centered 16:9 rectangle of the game's client area with a
GDI BitBlt of the desktop (same approach as Charge Grinder), so the game must
be visible and uncovered when the shot fires. Frames are saved lossless at
native resolution, with a JSON sidecar to fill in expected results for tests.

Usage:
    python tools/capture_screen.py                        # 3 s delay, one shot
    python tools/capture_screen.py -d 5 -l idle_6cols     # 5 s delay, labelled
    python tools/capture_screen.py -n 5 -i 0.3 -l fadein  # burst of 5 frames
"""
import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

WINDOW_TITLE = "LimbusCompany"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "screens"

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

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


def set_dpi_aware():
    """Without this, display scaling makes Windows report and capture scaled pixels."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def find_window(title=WINDOW_TITLE):
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        raise RuntimeError(f"Window '{title}' not found - is the game running?")
    return hwnd


def game_rect(hwnd):
    """Largest centered 16:9 rect of the client area, in screen coordinates."""
    if user32.IsIconic(hwnd):
        raise RuntimeError("Game window is minimized")
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        raise RuntimeError("Game window has an empty client area")
    if w * 9 > h * 16:
        gw, gh = h * 16 // 9, h
    else:
        gw, gh = w, w * 9 // 16
    return pt.x + (w - gw) // 2, pt.y + (h - gh) // 2, gw, gh


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


def save_png(path, img):
    # imencode + tofile handles non-ASCII paths, which cv2.imwrite does not on Windows
    ok, data = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError(f"PNG encode failed for {path}")
    data.tofile(str(path))


def countdown(seconds):
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        print(f"\rCapturing in {left:4.1f}s ", end="", flush=True)
        time.sleep(min(0.1, left))
    print("\rCapturing...        ")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-d", "--delay", type=float, default=3.0, help="seconds before the first shot (default 3)")
    ap.add_argument("-n", "--count", type=int, default=1, help="number of frames to take (default 1)")
    ap.add_argument("-i", "--interval", type=float, default=0.5, help="seconds between frames (default 0.5)")
    ap.add_argument("-l", "--label", default="screen", help="file name prefix, e.g. idle_6cols")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help=f"output folder (default {DEFAULT_OUT})")
    ap.add_argument("-t", "--title", default=WINDOW_TITLE, help="game window title")
    ap.add_argument("--no-json", action="store_true", help="skip the JSON sidecar")
    args = ap.parse_args()

    set_dpi_aware()
    try:
        hwnd = find_window(args.title)
        game_rect(hwnd)  # fail before the countdown if the window is unusable
    except RuntimeError as e:
        sys.exit(f"error: {e}")

    args.out.mkdir(parents=True, exist_ok=True)
    print("Switch to the game now.")
    countdown(args.delay)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for k in range(args.count):
        if k:
            time.sleep(args.interval)
        if not user32.IsWindow(hwnd):
            sys.exit("error: game window closed")
        try:
            rect = game_rect(hwnd)  # re-read each frame in case the window moved
        except RuntimeError as e:
            sys.exit(f"error: {e}")
        foreground = user32.GetForegroundWindow() == hwnd
        img = grab(rect)

        suffix = f"_{k:02d}" if args.count > 1 else ""
        png = args.out / f"{args.label}_{stamp}{suffix}.png"
        save_png(png, img)

        notes = []
        if not foreground:
            notes.append("game was not the foreground window - shot may show other windows")
        if img.mean() < 2:
            notes.append("frame is almost black - check HDR / exclusive fullscreen")
        if rect[2] != 1920:
            notes.append(f"captured at {rect[2]}x{rect[3]}; tests will scale to 1920x1080")

        if not args.no_json:
            meta = {
                "image": png.name,
                "captured_at": datetime.now().isoformat(timespec="milliseconds"),
                "window_rect": list(rect),
                "comp": rect[2] / 1920,
                "foreground": foreground,
                "expected": [],  # units left to right; fill with tools/identify.py --write-expected
            }
            png.with_suffix(".json").write_text(json.dumps(meta, indent=2))

        print(f"saved {png}  ({rect[2]}x{rect[3]})")
        for n in notes:
            print(f"  warning: {n}")


if __name__ == "__main__":
    main()
