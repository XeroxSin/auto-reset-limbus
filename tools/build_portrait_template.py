"""Build the portrait-frame template used by portraits.py.

Starting from one hand-measured portrait center, find the other frames in the
same screenshot, take the per-pixel median of all of them (the shared frame),
and mask out every pixel that varies between them (art, numbers, HP ticks, sin
stripe) plus the HP ring, whose fill changes with HP. Needs a battle screenshot
with at least 3 portraits.

Writes assets/templates/portrait_frame.png, portrait_frame_mask.png and
portrait_frame.json (art box relative to the frame center).

Usage:
    python tools/build_portrait_template.py tests/fixtures/screens/screen_20260916_145805.png
    python tools/build_portrait_template.py shot.png --seed 665 994
"""
import argparse
import json
import sys

import cv2
import numpy as np

from portraits import TEMPLATE_DIR, load_screen, match_frames, save_png

HALF = 60            # template is 120x120 around the frame center
VAR_THRESHOLD = 25   # per-pixel std (0-255) above which a pixel is "unit-specific"
NUMBERS_TOP = 28     # rows below center+this hold HP / sanity numbers: always masked
ART_BOTTOM = 33      # the art heptagon reaches this far down, past the top of the sanity badge
RING_HSV = ((0, 120, 120), (25, 255, 255))  # orange HP ring; it drains with HP, so it is masked


def seed_mask():
    m = np.full((2 * HALF, 2 * HALF), 255, np.uint8)
    cv2.circle(m, (HALF, HALF), 42, 0, -1)                  # art
    cv2.rectangle(m, (0, HALF + NUMBERS_TOP), (2 * HALF, 2 * HALF), 0, -1)  # numbers
    return m


def crop(img, cx, cy):
    return img[cy - HALF:cy + HALF, cx - HALF:cx + HALF]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--seed", type=int, nargs=2, default=(665, 994), metavar=("X", "Y"),
                    help="center of one portrait frame in 1080p pixels (default 665 994)")
    args = ap.parse_args()

    img = load_screen(args.image)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    hits = match_frames(gray, crop(gray, *args.seed), seed_mask(), min_score=0.8)
    if len(hits) < 3:
        sys.exit(f"error: only {len(hits)} frame(s) found from the seed; need 3+ to build a median")
    print("frames used:", [(x, y, round(s, 3)) for x, y, s in hits])

    stack = np.stack([crop(img, x, y) for x, y, _ in hits]).astype(np.float32)
    median = np.median(stack, axis=0).astype(np.uint8)
    varies = stack.std(axis=0).mean(axis=2) > VAR_THRESHOLD

    # drop 1-2 px lines (sub-pixel misalignment along frame edges), keep real blobs
    varies = cv2.morphologyEx(varies.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    # art box = largest varying blob above the HP digits
    n, _, stats, _ = cv2.connectedComponentsWithStats(varies[:HALF + ART_BOTTOM])
    if n < 2:
        sys.exit("error: no varying art region found")
    art = stats[1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))]
    art_box = [int(art[0]) - HALF, int(art[1]) - HALF, int(art[2]), int(art[3])]

    mask = np.where(cv2.dilate(varies, np.ones((3, 3), np.uint8)) > 0, 0, 255).astype(np.uint8)
    x, y, w, h = art_box
    cv2.rectangle(mask, (x + HALF - 3, y + HALF - 3), (x + HALF + w + 2, y + HALF + h + 2), 0, -1)
    mask[HALF + NUMBERS_TOP:] = 0
    ring = cv2.inRange(cv2.cvtColor(median, cv2.COLOR_BGR2HSV), *RING_HSV)
    mask[cv2.dilate(ring, np.ones((5, 5), np.uint8)) > 0] = 0

    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    save_png(TEMPLATE_DIR / "portrait_frame.png", cv2.cvtColor(median, cv2.COLOR_BGR2GRAY))
    save_png(TEMPLATE_DIR / "portrait_frame_mask.png", mask)
    (TEMPLATE_DIR / "portrait_frame.json").write_text(json.dumps({
        "source": str(args.image),
        "size": [2 * HALF, 2 * HALF],
        "art_box": art_box,
        "frames_used": len(hits),
        "mask_coverage": round(float((mask > 0).mean()), 3),
    }, indent=2))
    print(f"art box (rel. to center): {art_box}; mask keeps {(mask > 0).mean():.0%} of pixels")
    print(f"saved template to {TEMPLATE_DIR}")


if __name__ == "__main__":
    main()
