"""Identify the unit in each battle portrait, left to right.

In battle, the portrait art is the identity's profile.png (256x256) shrunk to
about 81 px, with the same center. The center 44x44 of each on-screen
portrait is compared with the center of every profile (normal and gacksung
art, scales 79-82, +-4 px shift). The best-scoring identity folder wins.
Everything happens in memory; no crops are written.

Usage:
    python tools/identify.py tests/fixtures/screens/*.png
    python tools/identify.py tests/fixtures/screens/*.png --write-expected
    python tools/identify.py --live
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from portraits import find_portraits, load_screen, load_template, to_base

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "assets" / "profiles"

SCALES = (79, 80, 81, 82)   # profile size (px) that matches the in-game art at 1080p
HALF = 22                   # compare the center 44x44 of the portrait
SHIFT = 4                   # allowed misalignment, px
MIN_SCORE = 0.7             # correct matches scored 0.84-0.98 on the fixtures
MIN_MARGIN = 0.1            # correct matches beat the next identity by 0.21+
LISTED_MIN_SCORE = 0.8      # restricted bank: below this the unit is not a listed sinner
                            # (wrong identities scored <= 0.71 on the fixtures, correct ones 0.84+)


def load_bank(manifest=PROFILE_DIR / "manifest.json", candidates=None, sinners=None):
    """Center windows of every profile at every scale.

    candidates: optional set of (sinner, identity) to limit the search to.
    sinners: optional set of sinner names; only their identities are loaded.
    """
    entries = json.loads(Path(manifest).read_text(encoding="utf-8"))
    r = HALF + SHIFT
    bank = []
    for e in entries:
        key = (e["sinner"], e["identity"])
        if candidates is not None and key not in candidates:
            continue
        if sinners is not None and e["sinner"] not in sinners:
            continue
        rgba = cv2.imdecode(np.fromfile(str(PROFILE_DIR / e["path"]), np.uint8), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            raise RuntimeError(f"cannot read {e['path']}")
        alpha = rgba[..., 3:4].astype(np.float32) / 255
        bgr = (rgba[..., :3] * alpha).astype(np.uint8)  # the game draws the art over black
        for s in SCALES:
            small = cv2.resize(bgr, (s, s), interpolation=cv2.INTER_AREA)
            c = s // 2
            bank.append((e, small[c - r:c + r, c - r:c + r]))
    if not bank:
        raise RuntimeError("no profiles to compare against")
    return bank


def identify(img, portraits, bank, restricted=False):
    """Add sinner / identity / variant / id / score / margin / listed to each portrait dict.

    restricted: the bank only holds some sinners. A portrait that matches none of
    them well enough gets sinner / identity / variant / id = None and listed = False.
    """
    for p in portraits:
        cx, cy = p["center"]
        patch = img[cy - HALF:cy + HALF, cx - HALF:cx + HALF]
        best = {}  # (sinner, identity) -> (score, entry)
        for e, win in bank:
            score = cv2.minMaxLoc(cv2.matchTemplate(win, patch, cv2.TM_CCOEFF_NORMED))[1]
            key = (e["sinner"], e["identity"])
            if key not in best or score > best[key][0]:
                best[key] = (score, e)
        ranked = sorted(best.values(), key=lambda t: -t[0])
        score, e = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else -1.0
        if restricted and score < LISTED_MIN_SCORE:
            p.update({"sinner": None, "identity": None, "variant": None, "id": None,
                      "id_score": round(float(score), 3), "margin": None,
                      "listed": False, "confident": True})
            continue
        p.update({
            "sinner": e["sinner"],
            "identity": e["identity"],
            "variant": e["variant"],
            "id": e["id"],
            "id_score": round(float(score), 3),
            "margin": round(float(score - runner_up), 3),
            "listed": True,
            "confident": bool(score >= MIN_SCORE and score - runner_up >= MIN_MARGIN),
        })
    return portraits


def write_expected(png, units):
    sidecar = Path(png).with_suffix(".json")
    meta = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {"image": Path(png).name}
    meta["expected"] = [{"order": u["order"], "sinner": u["sinner"], "identity": u["identity"]} for u in units]
    sidecar.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return sidecar


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", type=Path, help="screenshots to scan")
    ap.add_argument("--live", action="store_true", help="capture the game window instead of reading files")
    ap.add_argument("-d", "--delay", type=float, default=3.0, help="countdown for --live (default 3)")
    ap.add_argument("--write-expected", action="store_true",
                    help="replace 'expected' in each screenshot's JSON with the units found")
    args = ap.parse_args()

    frames = []
    if args.live:
        import capture_screen as cap
        cap.set_dpi_aware()
        try:
            hwnd = cap.find_window()
            cap.countdown(args.delay)
            frames.append((None, "live", to_base(cap.grab(cap.game_rect(hwnd)))))
        except RuntimeError as e:
            sys.exit(f"error: {e}")
    for path in args.images:
        frames.append((path, path.stem, load_screen(path)))
    if not frames:
        ap.error("give at least one image or --live")

    template, bank = load_template(), load_bank()
    for path, name, img in frames:
        units = identify(img, find_portraits(img, template), bank)
        print(f"{name}: {len(units)} unit(s)")
        for u in units:
            flag = "" if u["confident"] else "   <-- low confidence, check by eye"
            print(f"  {u['order']}  {u['sinner']:<12} {u['identity']:<45} "
                  f"{u['variant']:<8} score {u['id_score']:.3f}  margin {u['margin']:.3f}{flag}")
        if args.write_expected and path is not None:
            if all(u["confident"] for u in units):
                print(f"  wrote {write_expected(path, units)}")
            else:
                print("  not writing expected: some units are low confidence")


if __name__ == "__main__":
    main()
