"""Open the Esc menu and click "Retry Stage" with human-like mouse movement.

Steps:
  1. wait for the start delay so you can switch to the game, then until the
     game is actually the foreground window,
  2. unless the menu is already open: move the cursor to an empty spot above
     the menu (so no button pops up hovered), then press Esc,
  3. find the Retry Stage button (3rd button of the menu) by template; it has
     a normal and a highlighted (hovered) look, and the better match wins,
  4. move the cursor from wherever it is into the button and click
     (docs/human-mouse-movement.md: data-driven path, scattered landing point,
     Gaussian hold time),
  5. check that the menu went away.

Input only goes to the game: if another window gets focus, the script stops.
The templates are assets/templates/retry_stage_button.png and
retry_stage_button_highlighted.png (cut from tests/fixtures/menus/settings_menu.png
and settings_menu_highlighted.png, 1920x1080 coordinates). Progress goes to the
logging module; run on its own, this script prints it.

Usage:
    python tools/retry_stage.py                    # 5 s to switch windows, Esc, click Retry Stage
    python tools/retry_stage.py -d 10              # longer delay
    python tools/retry_stage.py --menu-open        # the Esc menu is already showing
    python tools/retry_stage.py --dry-run tests/fixtures/menus/settings_menu.png   # locate only, no input
"""
import argparse
import logging
import sys
import time
from pathlib import Path

import cv2

import capture_screen as cap
from humanmouse import human_input as hi
from portraits import load_screen, to_base

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = [ROOT / "assets" / "templates" / name
             for name in ("retry_stage_button.png", "retry_stage_button_highlighted.png")]
SEARCH = (700, 300, 520, 420)   # x, y, w, h around the Esc menu buttons (1080p)
MIN_SCORE = 0.85                # Retry Stage scores 1.0; other buttons <= 0.69, the other look of Retry Stage 0.23-0.69
TARGET_FRACTION = 0.6           # land inside the middle 60% of the button
PARK = (960, 185, 300, 60)      # cx, cy, w, h: empty scenery above the Esc menu (menu starts at y ~245)
MENU_TIMEOUT = 3.0
START_DELAY = 5.0               # time to switch to the game window
FOCUS_TIMEOUT = 30.0            # after the delay, wait this long for the game to be in front

log = logging.getLogger(__name__)


def load_button():
    """All looks of the Retry Stage button (grayscale templates)."""
    tpls = []
    for path in TEMPLATES:
        tpl = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if tpl is None:
            raise RuntimeError(f"missing template {path}")
        tpls.append(tpl)
    return tpls


def locate(frame, tpls):
    """frame: 1920x1080 BGR. Returns ((cx, cy, w, h), score) in 1080p for the best
    matching look, or (None, best score)."""
    x, y, w, h = SEARCH
    gray = cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
    best = (-1.0, None, None)
    for tpl in tpls:
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED))
        if score > best[0]:
            best = (score, loc, tpl.shape)
    score, (mx, my), (th, tw) = best
    if score < MIN_SCORE:
        return None, score
    return (x + mx + tw / 2, y + my + th / 2, tw, th), score


def grab_frame(hwnd):
    return to_base(cap.grab(cap.game_rect(hwnd)))


def wait_for(hwnd, tpl, present=True, timeout=MENU_TIMEOUT, interval=0.1):
    end = time.monotonic() + timeout
    while True:
        box, score = locate(grab_frame(hwnd), tpl)
        if (box is not None) == present:
            return box, score
        if time.monotonic() >= end:
            return box, score
        time.sleep(interval)


def to_screen(rect, box):
    """1080p (cx, cy, w, h) -> screen (x, y) and target size (w, h)."""
    left, top, w, _ = rect
    s = w / 1920
    cx, cy, bw, bh = box
    return (left + cx * s, top + cy * s), (bw * s * TARGET_FRACTION, bh * s * TARGET_FRACTION)


def dry_run(path, tpl):
    box, score = locate(load_screen(path), tpl)
    if box is None:
        print(f"{path}: Retry Stage not found (best score {score:.3f})")
        return 1
    cx, cy, w, h = box
    print(f"{path}: Retry Stage at ({cx:.0f}, {cy:.0f}), {w}x{h}, score {score:.3f}")
    print(f"  clicks would land inside a {w * TARGET_FRACTION:.0f}x{h * TARGET_FRACTION:.0f} box around the center")
    return 0


def wait_for_focus(hwnd, timeout=FOCUS_TIMEOUT):
    if cap.user32.GetForegroundWindow() == hwnd:
        return
    log.info("waiting for the game window to be in front...")
    end = time.monotonic() + timeout
    while cap.user32.GetForegroundWindow() != hwnd:
        if time.monotonic() >= end:
            raise RuntimeError(f"the game was not brought to the front within {timeout:g} s")
        time.sleep(0.1)
    time.sleep(0.3)  # let the window finish activating before sending input


def retry_stage(hwnd, tpl, menu_open=False):
    box, score = locate(grab_frame(hwnd), tpl)
    if box is None:
        if menu_open:
            raise RuntimeError(f"Retry Stage button not visible (best score {score:.2f})")
        park, psize = to_screen(cap.game_rect(hwnd), PARK)
        hi.move_to(*park, tsize=psize)
        log.info("menu not open (best score %.2f); cursor parked at %s, pressing Esc", score, hi.get_position())
        hi.press("esc")
        box, score = wait_for(hwnd, tpl, present=True)
        if box is None:
            raise RuntimeError(f"Retry Stage button did not appear after Esc (best score {score:.2f})")
    log.info("Retry Stage found at (%.0f, %.0f), score %.3f", box[0], box[1], score)

    target, tsize = to_screen(cap.game_rect(hwnd), box)
    start = hi.get_position()
    hi.click(*target, tsize=tsize)
    end = hi.get_position()
    log.info("moved %s -> %s (target %.0f, %.0f, box %.0fx%.0f) and clicked", start, end, *target, *tsize)

    gone, score = wait_for(hwnd, tpl, present=False, timeout=2.0)
    if gone is not None:
        raise RuntimeError(f"the menu is still open after clicking Retry Stage (score {score:.2f})")
    log.info("Retry Stage clicked; the menu closed (score now %.2f)", score)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-d", "--delay", type=float, default=START_DELAY,
                    help=f"seconds to switch to the game before anything happens (default {START_DELAY:g})")
    ap.add_argument("--menu-open", action="store_true", help="don't press Esc; the menu is already open")
    ap.add_argument("--dry-run", type=Path, metavar="SCREENSHOT",
                    help="only locate the button in a screenshot; no input is sent")
    ap.add_argument("-t", "--title", default=cap.WINDOW_TITLE, help="game window title")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    tpl = load_button()
    if args.dry_run:
        sys.exit(dry_run(args.dry_run, tpl))

    cap.set_dpi_aware()
    hi.WINDOW_TITLE = args.title
    try:
        hwnd = cap.find_window(args.title)
        cap.game_rect(hwnd)
        print(f"Switch to the game now ({args.delay:g} s).")
        cap.countdown(args.delay)
        wait_for_focus(hwnd)
        retry_stage(hwnd, tpl, args.menu_open)
    except hi.FocusLost as e:
        sys.exit(f"stopped: the game lost focus (foreground: {e})")
    except RuntimeError as e:
        sys.exit(f"error: {e}")
    finally:
        hi.mouse_settings.restore()


if __name__ == "__main__":
    main()
