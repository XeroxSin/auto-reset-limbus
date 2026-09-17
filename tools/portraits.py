"""Find the identity portraits in the battle action bar and box them.

Portraits sit in hexagonal frames along the bottom of the battle screen, one
per action column. They are found with a masked grayscale template of the
frame (assets/templates/portrait_frame*.png, made by build_portrait_template.py);
the mask drops everything that differs between units or over a fight (art,
HP / sanity numbers, HP ring and ticks, sin stripe), so only the fixed dark
frame is compared.

All coordinates are 1920x1080: screenshots are resized to that first.

Usage:
    python tools/portraits.py tests/fixtures/screens/*.png
    python tools/portraits.py --live -d 3          # capture the game, then detect
    python tools/portraits.py shot.png -o out/ --show
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "assets" / "templates"
DEFAULT_OUT = ROOT / "tests" / "output" / "portraits"

BASE_W, BASE_H = 1920, 1080
SEARCH_BAND = (860, 1080)   # y range of the portrait row
MIN_SCORE = 0.6             # TM_CCOEFF_NORMED; true frames 0.92-0.99, best non-frame ~0.39
MIN_GAP = 80                # px between portrait centers (real spacing ~122)
COLUMN_STEP = 121.75        # measured center-to-center spacing


def load_screen(path):
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"cannot read image: {path}")
    return to_base(img)


def to_base(img):
    if img.shape[:2] != (BASE_H, BASE_W):
        img = cv2.resize(img, (BASE_W, BASE_H), interpolation=cv2.INTER_AREA)
    return img


def load_template():
    tpl = cv2.imread(str(TEMPLATE_DIR / "portrait_frame.png"), cv2.IMREAD_GRAYSCALE)
    mask = cv2.imread(str(TEMPLATE_DIR / "portrait_frame_mask.png"), cv2.IMREAD_GRAYSCALE)
    meta_path = TEMPLATE_DIR / "portrait_frame.json"
    if tpl is None or mask is None or not meta_path.exists():
        raise RuntimeError("portrait template missing - run tools/build_portrait_template.py first")
    return tpl, mask, json.loads(meta_path.read_text())


def match_frames(gray, tpl, mask, min_score=MIN_SCORE, band=SEARCH_BAND):
    """Return [(cx, cy, score)] of frame centers, sorted left to right."""
    th, tw = tpl.shape
    y0, y1 = band
    res = cv2.matchTemplate(gray[y0:y1], tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
    res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)

    hits = []
    while True:
        _, score, _, (x, y) = cv2.minMaxLoc(res)
        if score < min_score:
            break
        hits.append((x + tw // 2, y + y0 + th // 2, float(score)))
        res[:, max(0, x - MIN_GAP):x + MIN_GAP + 1] = -1.0  # one hit per column
    return sorted(hits)


def find_portraits(img, template=None, min_score=MIN_SCORE):
    """Detect portraits in a 1920x1080 BGR frame.

    Returns one dict per portrait, left to right (= action order):
      order, center, score,
      frame: (x, y, w, h) of the whole hexagon frame,
      art:   (x, y, w, h) of the identity art inside it.
    """
    tpl, mask, meta = template or load_template()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    th, tw = tpl.shape
    ax, ay, aw, ah = meta["art_box"]  # relative to frame center
    out = []
    for i, (cx, cy, score) in enumerate(match_frames(gray, tpl, mask, min_score)):
        out.append({
            "order": i,
            "center": (cx, cy),
            "score": round(score, 3),
            "frame": (cx - tw // 2, cy - th // 2, tw, th),
            "art": (cx + ax, cy + ay, aw, ah),
        })
    return out


def spacing_warnings(portraits):
    warns = []
    for a, b in zip(portraits, portraits[1:]):
        gap = b["center"][0] - a["center"][0]
        steps = gap / COLUMN_STEP
        if abs(steps - round(steps)) * COLUMN_STEP > 6:
            warns.append(f"gap between #{a['order']} and #{b['order']} is {gap}px, not a multiple of ~{COLUMN_STEP:g}")
        if abs(a["center"][1] - b["center"][1]) > 6:
            warns.append(f"#{a['order']} and #{b['order']} are at different heights")
    return warns


def draw(img, portraits):
    vis = img.copy()
    for p in portraits:
        fx, fy, fw, fh = p["frame"]
        ax, ay, aw, ah = p["art"]
        cv2.rectangle(vis, (fx, fy), (fx + fw - 1, fy + fh - 1), (0, 200, 255), 1)
        cv2.rectangle(vis, (ax, ay), (ax + aw - 1, ay + ah - 1), (0, 255, 0), 2)
        label = f"#{p['order']} {p['score']:.2f}"
        cv2.putText(vis, label, (fx + 2, fy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(vis, label, (fx + 2, fy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
    return vis


def save_png(path, img):
    ok, data = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError(f"PNG encode failed for {path}")
    data.tofile(str(path))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", type=Path, help="screenshots to scan")
    ap.add_argument("--live", action="store_true", help="capture the game window instead of reading files")
    ap.add_argument("-d", "--delay", type=float, default=3.0, help="countdown for --live (default 3)")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help=f"folder for annotated images (default {DEFAULT_OUT})")
    ap.add_argument("--min-score", type=float, default=MIN_SCORE)
    ap.add_argument("--show", action="store_true", help="open a window with the result")
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

    template = load_template()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, img in frames:
        portraits = find_portraits(img, template, args.min_score)
        vis = draw(img, portraits)
        out_path = args.out / f"{name}_portraits.png"
        save_png(out_path, vis)

        print(f"{name}: {len(portraits)} portrait(s) -> {out_path}")
        print(json.dumps([{k: p[k] for k in ("order", "center", "score", "art")} for p in portraits]))
        for w in spacing_warnings(portraits):
            print(f"  warning: {w}")
        if args.show:
            cv2.imshow(name, vis)
    if args.show:
        cv2.waitKey(0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
