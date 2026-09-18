"""Dump the screen patch that the next-in-line skill is read from, one file per unit.

For every portrait found in a screenshot (or a live capture), this writes the
search window used for the "next" layer, as three panels blown up 6x:

    raw      what the screen shows there
    Cb       blue-difference chroma: what the matcher actually compares
    Cr       red-difference chroma, for comparison

The yellow box is where the icon is predicted to be (the template size at the
middle scale); the matcher may slide it by SEARCH px in each direction.

Files land in output/next_crops/<screenshot>/<order>_<sinner>.png.

Usage:
    python tools/next_crops.py tests/fixtures/screens/*.png
    python tools/next_crops.py shot.png --layer top
    python tools/next_crops.py --live -d 3
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from identify import identify, load_bank
from portraits import find_portraits, load_screen, load_template, save_png, to_base
from skills import LAYERS, ROW_CENTER_X, SEARCH, cb

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output" / "next_crops"
ZOOM = 6


def window(layer, cx):
    """(x0, y0, w, h) searched for this layer, and the predicted icon box inside it."""
    cfg = LAYERS[layer]
    px = round(cx + cfg["shift"] * (cx - ROW_CENTER_X))
    n = round(256 * cfg["scales"][len(cfg["scales"]) // 2])   # border art is 256 px square
    x0, y0 = px - n // 2 - SEARCH, cfg["y"] - n // 2 - SEARCH
    return (x0, y0, n + 2 * SEARCH, n + 2 * SEARCH), (SEARCH, SEARCH, n, round(n * cfg["visible"]))


def panel(view, box, label, mark):
    x0, y0, w, h = box
    patch = view[y0:y0 + h, x0:x0 + w]
    if patch.ndim == 2:
        patch = cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)
    big = cv2.resize(patch, (w * ZOOM, h * ZOOM), interpolation=cv2.INTER_NEAREST)
    mx, my, mw, mh = (v * ZOOM for v in mark)
    cv2.rectangle(big, (mx, my), (mx + mw - 1, my + mh - 1), (0, 220, 255), 1)
    strip = np.zeros((18, big.shape[1], 3), np.uint8)
    cv2.putText(strip, label, (3, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([strip, big])


def dump(name, img, layer, bank):
    units = identify(img, find_portraits(img, load_template()), bank)
    ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    views = [("raw", img), ("Cb", cb(img)), ("Cr", ycc[..., 1])]
    out = OUT_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    for u in units:
        box, mark = window(layer, u["center"][0])
        panels = [panel(v, box, f"{lbl}  {u['sinner']} x={u['center'][0]}", mark) for lbl, v in views]
        sep = np.full((panels[0].shape[0], 4, 3), 60, np.uint8)
        sheet = np.hstack([p for pair in zip(panels, [sep] * len(panels)) for p in pair][:-1])
        path = out / f"{u['order']}_{str(u['sinner']).replace(' ', '_')}.png"
        save_png(path, sheet)
        print(f"  {path}")
    return len(units)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", type=Path, help="screenshots to cut up")
    ap.add_argument("--live", action="store_true", help="capture the game window instead")
    ap.add_argument("-d", "--delay", type=float, default=3.0, help="countdown for --live (default 3)")
    ap.add_argument("--layer", choices=tuple(LAYERS), default="next", help="which icon row (default next)")
    args = ap.parse_args()

    frames = []
    if args.live:
        import capture_screen as cap
        cap.set_dpi_aware()
        try:
            hwnd = cap.find_window()
            cap.countdown(args.delay)
            frames.append(("live", to_base(cap.grab(cap.game_rect(hwnd)))))
        except RuntimeError as e:
            sys.exit(f"error: {e}")
    for path in args.images:
        frames.append((path.stem, load_screen(path)))
    if not frames:
        ap.error("give at least one image or --live")

    bank = load_bank()
    for name, img in frames:
        print(f"{name}:")
        dump(name, img, args.layer, bank)


if __name__ == "__main__":
    main()
