"""Read the 3 skills each unit has available: bottom row, top row, next in line.

Every skill icon is drawn inside a sin- and tier-specific border
(assets/Skill borders/Skill <tier>/<Sin>/<Sin>.png). The crest and outline of
that border are compared shape-only: both the screen and the templates are
turned into chroma (max - min of B, G, R), which keeps colored borders and
drops the gray skill art, without looking at hue. The best of the 21
sin/tier templates wins.

The sin can also be read from color (--sin-mode color): hue is measured only
on the pixels of the matched border (front icons excluded), and each pixel
votes for the sin with the nearest reference hue. If the top two sins are
within COLOR_AMBIGUOUS of each other, the border score decides between them.
The tier always comes from the border. Both reads are always computed and a
disagreement is reported.

Per column the three icons sit at fixed positions relative to the portrait
center cx (1080p, perspective shift toward x = 908):

    layer    y    scale  x offset             visible part of the border
    bottom   858  0.25   -0.061 * (cx - 908)  all
    top      815  0.23   -0.172 * (cx - 908)  upper 55% (bottom icon covers the rest)
    next     777  0.13   -0.227 * (cx - 908)  upper 55%, very faint: bright pixels
                                             from the top icon are removed and the
                                             rest is boosted 4x before matching

Usage:
    python tools/skills.py tests/fixtures/screens/*.png
    python tools/skills.py --live
    python tools/skills.py shot.png --no-identity      # skip the (slower) identity step
    python tools/skills.py shot.png --sin-mode color   # color first, border as tiebreaker
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
    #          x shift per px from center, center y, scales, visible top fraction, faint
    "bottom": dict(shift=-0.061, y=858, scales=(0.24, 0.25, 0.26), visible=1.0, faint=False),
    "top":    dict(shift=-0.172, y=815, scales=(0.22, 0.23, 0.24), visible=0.55, faint=False),
    "next":   dict(shift=-0.227, y=777, scales=(0.12, 0.13, 0.14), visible=0.55, faint=True),
}
FAINT_BRIGHT = 70           # chroma above this belongs to a front icon, not the faint one
FAINT_GAIN = 4
MIN_SCORE = 0.5             # correct reads scored 0.58-0.93 on the fixtures
MIN_MARGIN = 0.05           # over the best *different* sin/tier
COLOR_MIN_CHROMA = {False: 40, True: 8}  # per faint flag: pixels weaker than this carry no hue
COLOR_AMBIGUOUS = 0.25      # vote-share gap below which color defers to the border


def chroma(bgr):
    b = bgr.astype(np.int16)
    return (b.max(axis=2) - b.min(axis=2)).astype(np.uint8)


def faint_view(ch):
    """Keep only dim colored pixels (the next-in-line icon) and boost them."""
    c = ch.astype(np.float32)
    bright = cv2.dilate((c > FAINT_BRIGHT).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    c[bright] = 0
    return np.clip(c * FAINT_GAIN, 0, 255).astype(np.uint8)


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
                tpl = (chroma(small[..., :3]).astype(np.float32) * alpha / 255).astype(np.uint8)
                mask = np.where(alpha > 60, 255, 0).astype(np.uint8)
                mask[int(n * cfg["visible"]):] = 0
                entries.append((key, sc, tpl, mask))
        bank[layer] = entries
    return bank


def sin_hues():
    """Reference hue (OpenCV 0-180) of each sin, circular mean over its three borders."""
    out = {}
    for sin in SINS:
        angles = []
        for tier in TIERS:
            img = cv2.imdecode(np.fromfile(str(BORDER_DIR / f"Skill {tier}" / sin / f"{sin}.png"), np.uint8),
                               cv2.IMREAD_UNCHANGED)
            hsv = cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2HSV)
            sel = (img[..., 3] > 200) & (hsv[..., 1] > 120) & (hsv[..., 2] > 120)
            angles.append(hsv[..., 0][sel].astype(np.float32) * np.pi / 90)
        a = np.concatenate(angles)
        out[sin] = float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())) / 2) % 180
    return out


def color_votes(img, views, placement, faint, exclude, hues):
    """Share of border pixels (weighted by chroma) whose hue is nearest each sin."""
    x, y, mask = placement
    h, w = mask.shape
    ch = views["chroma"][y:y + h, x:x + w]
    sel = (mask > 0) & (ch > COLOR_MIN_CHROMA[faint]) & ~exclude[y:y + h, x:x + w]
    if faint:
        sel &= views["faint"][y:y + h, x:x + w] > 0
    hue = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)[..., 0][sel].astype(np.float32)
    weight = ch[sel].astype(np.float32)
    if weight.sum() == 0:
        return {sin: 0.0 for sin in SINS}
    refs = np.array([hues[sin] for sin in SINS], np.float32)
    d = np.abs(hue[:, None] - refs[None, :])
    nearest = np.minimum(d, 180 - d).argmin(axis=1)
    shares = np.bincount(nearest, weights=weight, minlength=len(SINS)) / weight.sum()
    return dict(zip(SINS, shares.tolist()))


def score_layer(views, cx, layer, entries):
    """Border shape score of every (sin, tier) -> (score, center, mask placed on screen)."""
    cfg = LAYERS[layer]
    src = views["faint" if cfg["faint"] else "chroma"]
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


def read_layer(img, views, cx, layer, entries, hues, exclude, sin_mode="shape"):
    best = score_layer(views, cx, layer, entries)
    if not best:
        return None
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    (shape_sin, _), (_, _, placement) = ranked[0]

    votes = sorted(color_votes(img, views, placement, LAYERS[layer]["faint"], exclude, hues).items(),
                   key=lambda kv: -kv[1])
    color_sin, color_gap = votes[0][0], votes[0][1] - votes[1][1]
    if sin_mode == "color":
        if color_gap >= COLOR_AMBIGUOUS:
            sin, sin_by = color_sin, "color"
        else:
            close = [s for s, v in votes if votes[0][1] - v < COLOR_AMBIGUOUS]
            sin = max(close, key=lambda s: max(best[(s, t)][0] for t in TIERS if (s, t) in best))
            sin_by = "border (color tie: " + "/".join(close) + ")"
    else:
        sin, sin_by = shape_sin, "border"

    tier = max((t for t in TIERS if (sin, t) in best), key=lambda t: best[(sin, t)][0])
    score, center, placement = best[(sin, tier)]
    others = [(k, v) for k, v in ranked if k != (sin, tier)]
    runner = others[0] if others else ((None, None), (-1.0,))
    margin = score - runner[1][0]

    x, y, mask = placement  # this icon's pixels must not count for the icons behind it
    exclude[y:y + mask.shape[0], x:x + mask.shape[1]] |= cv2.dilate(mask, np.ones((5, 5), np.uint8)) > 0
    return {
        "sin": sin,
        "tier": tier,
        "score": round(score, 3),
        "margin": round(margin, 3),
        "runner_up": f"{runner[0][0]} {runner[0][1]}",
        "sin_by": sin_by,
        "shape_sin": shape_sin,
        "color_sin": color_sin,
        "color_gap": round(color_gap, 3),
        "center": center,
        "confident": bool(score >= MIN_SCORE and (margin >= MIN_MARGIN or sin_by == "color")),
    }


def read_skills(img, portraits, bank, sin_mode="shape", hues=None):
    """Add p["skills"] = {"bottom": {...}, "top": {...}, "next": {...}} to each portrait.

    Layers are read front to back so each icon's pixels are excluded from the
    color read of the icons behind it.
    """
    hues = hues or sin_hues()
    ch = chroma(img)
    views = {"chroma": ch, "faint": faint_view(ch)}
    for p in portraits:
        exclude = np.zeros(ch.shape, bool)
        p["skills"] = {layer: read_layer(img, views, p["center"][0], layer, bank[layer], hues, exclude, sin_mode)
                       for layer in LAYERS}
        p["skill_warnings"] = consistency_warnings(p["skills"]) + [
            f"{layer}: border says {sk['shape_sin']}, color says {sk['color_sin']}"
            for layer, sk in p["skills"].items() if sk and sk["shape_sin"] != sk["color_sin"]]
    return portraits


def consistency_warnings(skills):
    """A unit's skill of a given tier always has the same sin."""
    by_tier = {}
    for layer, s in skills.items():
        if s:
            by_tier.setdefault(s["tier"], set()).add(s["sin"])
    return [f"skill {t} read as {' and '.join(sorted(v))}" for t, v in sorted(by_tier.items()) if len(v) > 1]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", type=Path, help="screenshots to scan")
    ap.add_argument("--live", action="store_true", help="capture the game window instead of reading files")
    ap.add_argument("-d", "--delay", type=float, default=3.0, help="countdown for --live (default 3)")
    ap.add_argument("--no-identity", action="store_true", help="skip identifying the units")
    ap.add_argument("--json", action="store_true", help="print machine-readable results only")
    ap.add_argument("--sin-mode", choices=("shape", "color"), default="shape",
                    help="decide the sin by border shape (default) or by color with the border as tiebreaker")
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

    template, borders, hues = load_template(), load_borders(), sin_hues()
    profiles = None
    if not args.no_identity:
        from identify import identify, load_bank
        profiles = load_bank()

    for name, img in frames:
        units = find_portraits(img, template)
        if profiles is not None:
            identify(img, units, profiles)
        read_skills(img, units, borders, args.sin_mode, hues)

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
                print(f"       {layer:<7} {s['sin']:<9} skill {s['tier']}   score {s['score']:.2f}  "
                      f"margin {s['margin']:.2f} (vs {s['runner_up']})  color gap {s['color_gap']:.2f}  "
                      f"sin by {s['sin_by']}{flag}")
            for w in u["skill_warnings"]:
                print(f"       warning: {w}")


if __name__ == "__main__":
    main()
