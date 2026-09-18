"""Capture the battle screen and write the unit order, identities and skill tiers to JSON.

Combines capture_screen (game window grab), portraits (action bar columns),
identify (which identity is in each column) and skills (bottom row, top row
and next-in-line skill). Only the tier of each skill is read; sins are not
used at all.

Output:
    {
      "source": "live" | "<screenshot file>",
      "captured_at": "...",
      "units": [
        {"order": 0, "sinner": "Ishmael", "identity": "Jeong_s Office Rep",
         "skills": {"bottom": 1, "top": 2, "next": 1}},
        ...
      ],
      "warnings": ["..."]          # low-confidence reads, empty when all is well
    }

Templates are loaded first, then the countdown starts, so the capture happens
right when the countdown ends. Every read goes to its own JSON file in output/:

    output/battle_state_<YYYYMMDD_HHMMSS>.json     live captures
    output/<screenshot name>_state.json            saved screenshots

Usage:
    python tools/battle_state.py                          # load, 3 s countdown, capture
    python tools/battle_state.py -d 5
    python tools/battle_state.py -o state.json            # write to a specific file instead
    python tools/battle_state.py --save-screenshot        # also keep the capture as a test fixture
    python tools/battle_state.py -c configs/config.json  # only look for that config's sinners
    python tools/battle_state.py tests/fixtures/screens/screen_20260916_145805.png
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import capture_screen as cap
from identify import identify, load_bank
from portraits import find_portraits, load_screen, load_template, to_base
from skills import LAYERS, load_borders, read_skills

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output"

log = logging.getLogger(__name__)


class BattleReader:
    """Loads all templates once; read() can then be called on every frame."""

    def __init__(self, sinners=None):
        """sinners: optional set of sinner names; other units are reported as not listed
        (sinner / identity / skills null) and their skills are not read."""
        self.restricted = sinners is not None
        self.frame_template = load_template()
        self.profiles = load_bank(sinners=sinners)
        self.borders = load_borders()

    def read(self, img):
        """img: BGR frame of the game area (any 16:9 size). Returns the state dict (without source)."""
        img = to_base(img)
        units = find_portraits(img, self.frame_template)
        identify(img, units, self.profiles, self.restricted)
        read_skills(img, [u for u in units if u["listed"]], self.borders)
        log_units(units)

        out, warnings = [], []
        for u in units:
            if not u["listed"]:
                out.append({"order": u["order"], "sinner": None, "identity": None,
                            "skills": dict.fromkeys(LAYERS)})
                continue
            tiers = {}
            for layer in LAYERS:
                s = u["skills"][layer]
                tiers[layer] = s["tier"] if s else None
                if s is None:
                    warnings.append(f"unit {u['order']}: {layer} skill not found")
                elif not s["confident"]:
                    warnings.append(f"unit {u['order']}: {layer} skill low confidence "
                                    f"(score {s['score']:.2f}, margin {s['margin']:.2f})")
            if not u["confident"]:
                warnings.append(f"unit {u['order']}: identity low confidence "
                                f"(score {u['id_score']:.2f}, margin {u['margin']:.2f})")
            out.append({
                "order": u["order"],
                "sinner": u["sinner"],
                "identity": u["identity"],
                "skills": tiers,
            })
        if not units:
            warnings.append("no portraits found - is a battle turn waiting for input?")
        return {"units": out, "warnings": warnings}


def log_units(units):
    """Scores behind a read, for the log file (debug level)."""
    log.debug("%d portrait(s) found", len(units))
    for u in units:
        if not u["listed"]:
            log.debug("  unit %d at x=%d: not listed (best score %.3f)", u["order"], u["center"][0], u["id_score"])
            continue
        log.debug("  unit %d at x=%d: %s / %s (%s) score %.3f margin %.3f%s", u["order"], u["center"][0],
                  u["sinner"], u["identity"], u["variant"], u["id_score"], u["margin"],
                  "" if u["confident"] else "  LOW CONFIDENCE")
        for layer in LAYERS:
            s = u["skills"][layer]
            if s is None:
                log.debug("    %-6s not found", layer)
                continue
            log.debug("    %-6s skill %d  score %.3f margin %.3f (vs %s, border %s)%s",
                      layer, s["tier"], s["score"], s["margin"], s["runner_up"], s["border"],
                      "" if s["confident"] else "  LOW CONFIDENCE")


def find_game(title):
    cap.set_dpi_aware()
    hwnd = cap.find_window(title)
    cap.game_rect(hwnd)  # fail early if the window is unusable
    return hwnd


def capture(hwnd, delay):
    print("Switch to the game now.")
    cap.countdown(delay)
    rect = cap.game_rect(hwnd)
    if not cap.is_foreground(hwnd):
        print("warning: game was not the foreground window", file=sys.stderr)
    return cap.grab(rect), rect


def live_output_path(when):
    return OUT_DIR / f"battle_state_{when.strftime('%Y%m%d_%H%M%S')}.json"


def save_fixture(img, rect, when):
    stamp = when.strftime("%Y%m%d_%H%M%S")
    cap.DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    png = cap.DEFAULT_OUT / f"state_{stamp}.png"
    cap.save_png(png, img)
    meta = {"image": png.name, "captured_at": when.isoformat(timespec="milliseconds"),
            "window_rect": list(rect), "comp": rect[2] / 1920,
            "expected": []}  # fill in once the read has been checked by eye
    png.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return png


def write_json(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def print_units(state):
    for u in state["units"]:
        if u["sinner"] is None:
            print(f"  {u['order']}  (not listed)")
            continue
        s = u["skills"]
        print(f"  {u['order']}  {u['sinner']:<12} {u['identity']:<45} "
              f"bottom {s['bottom']}  top {s['top']}  next {s['next']}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", type=Path, help="read these screenshots instead of capturing")
    ap.add_argument("-d", "--delay", type=float, default=3.0, help="countdown before capturing (default 3)")
    ap.add_argument("-o", "--out", type=Path,
                    help="output JSON file (live) or folder (screenshots) instead of output/")
    ap.add_argument("-t", "--title", default=cap.WINDOW_TITLE, help="game window title")
    ap.add_argument("--save-screenshot", action="store_true",
                    help="keep the live capture in tests/fixtures/screens for later testing")
    ap.add_argument("-c", "--config", type=Path,
                    help="only look for the sinners listed in this verify config")
    args = ap.parse_args()

    sinners = None
    if args.config:
        from verify_state import ConfigError, load_config, roster
        try:
            sinners = roster(load_config(args.config))
        except ConfigError as e:
            sys.exit(f"error: {args.config}: {e}")

    hwnd = None
    if not args.images:
        try:
            hwnd = find_game(args.title)
        except RuntimeError as e:
            sys.exit(f"error: {e}")

    print("loading templates...")
    reader = BattleReader(sinners)
    print("templates loaded")

    jobs = []  # (source name, capture time, image, window rect, output path)
    if hwnd is not None:
        try:
            img, rect = capture(hwnd, args.delay)
        except RuntimeError as e:
            sys.exit(f"error: {e}")
        when = datetime.now()
        jobs.append(("live", when, img, rect, args.out or live_output_path(when)))
    for path in args.images:
        out_dir = args.out or OUT_DIR
        jobs.append((path.name, datetime.now(), load_screen(path), None, out_dir / f"{path.stem}_state.json"))

    for source, when, img, rect, out_path in jobs:
        state = {"source": source, "captured_at": when.isoformat(timespec="seconds")}
        if rect is not None and args.save_screenshot:
            state["screenshot"] = str(save_fixture(img, rect, when))
        state.update(reader.read(img))
        write_json(out_path, state)

        print(f"{source}: {len(state['units'])} unit(s) -> {out_path}")
        print_units(state)
        for w in state["warnings"]:
            print(f"  warning: {w}")


if __name__ == "__main__":
    main()
