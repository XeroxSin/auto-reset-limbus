"""Read the 3 skills each unit has available: bottom row, top row, next in line.

Only the tier (Skill 1, 2 or 3) is read; which sin a skill belongs to is not
used and is not reported.

Every skill icon is drawn inside a sin- and tier-specific border
(assets/Skill borders/Skill <tier>/<Sin>/<Sin>.png), and the tiers differ in
the shape of the crest and outline. All 21 borders are matched and the best
one decides the tier, whatever its sin.

Matching happens on the Cb channel (YCrCb). Cb keeps the borders apart from the
background even when a stage washes the whole screen in one colour: on red
stages a chroma or gray view loses the faint next-in-line icon entirely, while
its outline still stands out in Cb. Over 26 labelled units, next-in-line tiers
went from 9/26 (chroma with the front icons suppressed) to 24/26, and the two
front rows stayed at 26/26.

Per column the three icons sit at fixed positions relative to the portrait
center cx (1080p, perspective shift toward x = 908):

    layer    y    scale  x offset             visible part of the border
    bottom   858  0.25   -0.061 * (cx - 908)  all
    top      815  0.23   -0.172 * (cx - 908)  upper 55% (bottom icon covers the rest)
    next     777  0.13   -0.227 * (cx - 908)  upper 55%, and much fainter than the
                                             two rows in front of it

Usage:
    python tools/skills.py tests/fixtures/screens/*.png
    python tools/skills.py --live
    python tools/skills.py shot.png --no-identity      # skip the (slower) identity step
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from portraits import find_portraits, load_screen, load_template, to_base

ROOT = Path(__file__).resolve().parent.parent
BORDER_DIR = ROOT / "assets" / "Skill borders"
SINS = ("Wrath", "Lust", "Sloth", "Gluttony", "Gloom", "Pride", "Envy")
TIERS = (1, 2, 3)

ROW_CENTER_X = 908          # columns shift toward this x with height (perspective)
SEARCH = 8                  # px of slack around each predicted icon center
LAYERS = {
    #          x shift per px from center, center y, scales, visible top fraction,
    #          score a read must reach, margin it must beat the best other tier by
    "bottom": dict(shift=-0.061, y=858, scales=(0.24, 0.25, 0.26), visible=1.0,
                   min_score=0.45, min_margin=0.05),
    "top":    dict(shift=-0.172, y=815, scales=(0.22, 0.23, 0.24), visible=0.55,
                   min_score=0.45, min_margin=0.05),
    # the next-in-line icon is fainter, so it clears a lower bar
    "next":   dict(shift=-0.227, y=777, scales=(0.12, 0.13, 0.14), visible=0.55,
                   min_score=0.4, min_margin=0.03),
}


def cb(bgr):
    """Blue-difference chroma (YCrCb): the view every match is made on."""
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)[..., 2]


def load_borders():
    """{layer: [((sin, tier), scale, template, mask), ...]} built in memory."""
    raw = {}
    for tier in TIERS:
        for sin in SINS:
            path = BORDER_DIR / f"Skill {tier}" / sin / f"{sin}.png"
            img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)
            if img is None or img.shape[2] != 4:
                raise RuntimeError(f"cannot read RGBA border: {path}")
            raw[(sin, tier)] = img

    bank = {}
    for layer, cfg in LAYERS.items():
        entries = []
        for key, img in raw.items():
            for sc in cfg["scales"]:
                n = round(img.shape[0] * sc)
                small = cv2.resize(img, (n, n), interpolation=cv2.INTER_AREA)
                alpha = small[..., 3]
                art = (small[..., :3].astype(np.float32) * (alpha[..., None] / 255)).astype(np.uint8)
                tpl = cb(art)
                mask = np.where(alpha > 60, 255, 0).astype(np.uint8)
                mask[int(n * cfg["visible"]):] = 0
                entries.append((key, sc, tpl, mask))
        bank[layer] = entries
    return bank


def score_layer(src, cx, layer, entries):
    """Border shape score of every (sin, tier) -> (score, center, mask placed on screen)."""
    cfg = LAYERS[layer]
    px = round(cx + cfg["shift"] * (cx - ROW_CENTER_X))
    best = {}
    for key, sc, tpl, mask in entries:
        h, w = tpl.shape
        x0, y0 = px - SEARCH - w // 2, cfg["y"] - SEARCH - h // 2
        roi = src[y0:y0 + h + 2 * SEARCH, x0:x0 + w + 2 * SEARCH]
        if roi.shape[0] < h or roi.shape[1] < w:
            continue
        res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, score, _, (lx, ly) = cv2.minMaxLoc(res)
        if score > best.get(key, (-2.0,))[0]:
            best[key] = (float(score), (x0 + lx + w // 2, y0 + ly + h // 2), (x0 + lx, y0 + ly, mask))
    return best


def read_layer(src, cx, layer, entries):
    """Best border at this icon's spot -> its tier. The sin of the winning template
    is kept only for the log; the runner-up is the best border of another tier."""
    best = score_layer(src, cx, layer, entries)
    if not best:
        return None
    cfg = LAYERS[layer]
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    (sin, tier), (score, center, _) = ranked[0]
    runner = next((kv for kv in ranked if kv[0][1] != tier), ((None, None), (-1.0,)))
    margin = score - runner[1][0]
    return {
        "tier": tier,
        "score": round(score, 3),
        "margin": round(margin, 3),
        "border": f"{sin} {tier}",
        "runner_up": f"skill {runner[0][1]}",
        "center": center,
        "confident": bool(score >= cfg["min_score"] and margin >= cfg["min_margin"]),
    }


def read_skills(img, portraits, bank):
    """Add p["skills"] = {"bottom": {...}, "top": {...}, "next": {...}} to each portrait."""
    src = cb(img)
    for p in portraits:
        p["skills"] = {layer: read_layer(src, p["center"][0], layer, bank[layer]) for layer in LAYERS}
    return portraits


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", type=Path, help="screenshots to scan")
    ap.add_argument("--live", action="store_true", help="capture the game window instead of reading files")
    ap.add_argument("-d", "--delay", type=float, default=3.0, help="countdown for --live (default 3)")
    ap.add_argument("--no-identity", action="store_true", help="skip identifying the units")
    ap.add_argument("--json", action="store_true", help="print machine-readable results only")
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

    template, borders = load_template(), load_borders()
    profiles = None
    if not args.no_identity:
        from identify import identify, load_bank
        profiles = load_bank()

    for name, img in frames:
        units = find_portraits(img, template)
        if profiles is not None:
            identify(img, units, profiles)
        read_skills(img, units, borders)

        if args.json:
            print(json.dumps({"image": name, "units": [
                {k: u[k] for k in ("order", "sinner", "identity", "skills") if k in u} for u in units]}))
            continue

        print(f"{name}: {len(units)} unit(s)")
        for u in units:
            who = f"{u['sinner']} - {u['identity']}" if "identity" in u else "(identity skipped)"
            print(f"  {u['order']}  {who}")
            for layer, s in u["skills"].items():
                if s is None:
                    print(f"       {layer:<7} not found")
                    continue
                flag = "" if s["confident"] else "   <-- low confidence"
                print(f"       {layer:<7} skill {s['tier']}   score {s['score']:.2f}  "
                      f"margin {s['margin']:.2f} (vs {s['runner_up']}, border {s['border']}){flag}")


if __name__ == "__main__":
    main()
