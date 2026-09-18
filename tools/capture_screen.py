"""Capture the Limbus Company game area to PNG after a countdown.

Grabs the largest centered 16:9 rectangle of the game's client area off the
screen, so the game must be visible and uncovered when the shot fires. Frames
are saved lossless at native resolution, with a JSON sidecar to fill in expected
results for tests.

Finding the window and reading its pixels is the operating system's business, so
it lives in backend.py (GDI on Windows, X11 on Linux); everything here works the
same on both.

Usage:
    python tools/capture_screen.py                        # 3 s delay, one shot
    python tools/capture_screen.py -d 5 -l idle_6cols     # 5 s delay, labelled
    python tools/capture_screen.py -n 5 -i 0.3 -l fadein  # burst of 5 frames
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

import backend

WINDOW_TITLE = "LimbusCompany"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "screens"

set_dpi_aware = backend.set_dpi_aware
window_exists = backend.window_exists
is_foreground = backend.is_foreground
grab = backend.grab


def find_window(title=WINDOW_TITLE):
    return backend.find_window(title)


def game_rect(win):
    """Largest centered 16:9 rect of the client area, in screen coordinates."""
    x, y, w, h = backend.client_rect(win)
    if w * 9 > h * 16:
        gw, gh = h * 16 // 9, h
    else:
        gw, gh = w, w * 9 // 16
    return x + (w - gw) // 2, y + (h - gh) // 2, gw, gh


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


def black_frame_hint():
    if backend.NAME == "x11":
        return ("frame is almost black - on Wayland the root window cannot be read; "
                "try LIMBUS_X11_CAPTURE=window or an X11 session")
    return "frame is almost black - check HDR / exclusive fullscreen"


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
        win = find_window(args.title)
        game_rect(win)  # fail before the countdown if the window is unusable
    except RuntimeError as e:
        sys.exit(f"error: {e}")

    args.out.mkdir(parents=True, exist_ok=True)
    print("Switch to the game now.")
    countdown(args.delay)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for k in range(args.count):
        if k:
            time.sleep(args.interval)
        if not window_exists(win):
            sys.exit("error: game window closed")
        try:
            rect = game_rect(win)  # re-read each frame in case the window moved
        except RuntimeError as e:
            sys.exit(f"error: {e}")
        foreground = is_foreground(win)
        img = grab(rect)

        suffix = f"_{k:02d}" if args.count > 1 else ""
        png = args.out / f"{args.label}_{stamp}{suffix}.png"
        save_png(png, img)

        notes = []
        if not foreground:
            notes.append("game was not the foreground window - shot may show other windows")
        if img.mean() < 2:
            notes.append(black_frame_hint())
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
