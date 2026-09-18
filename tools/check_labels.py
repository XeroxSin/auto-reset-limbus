"""Score the skill reader against the labelled screenshots in data/labelled/.

Each labelled sample is a PNG plus a JSON of the same name:

    {
      "note": "red stage, camera panned up",
      "units": {"Sinclair": {"bottom": 1, "top": 2, "next": 1}, ...}
    }

Units are matched to their label by identified sinner, so the column order in
the file doesn't matter. Tiers are 1, 2 or 3; leave a slot out if you're unsure.

Usage:
    python tools/check_labels.py                     # score every labelled sample
    python tools/check_labels.py -v                  # list every unit
    python tools/check_labels.py --new shot.png      # add shot.png with the labels filled
                                                     # in from the current read, for you to fix
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from identify import identify, load_bank
from portraits import find_portraits, load_screen, load_template
from skills import LAYERS, load_borders, read_skills

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "labelled"


class Reader:
    def __init__(self):
        self.template = load_template()
        self.bank = load_bank()
        self.borders = load_borders()

    def read(self, img):
        units = identify(img, find_portraits(img, self.template), self.bank)
        read_skills(img, units, self.borders)
        return units


def samples():
    return sorted(p for p in DATA.glob("*.json"))


def score(reader, verbose=False):
    hits = {layer: [0, 0] for layer in LAYERS}       # layer -> [correct, total]
    worst = []
    for meta_path in samples():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        img_path = meta_path.with_suffix(".png")
        if not img_path.exists():
            print(f"{meta_path.name}: no {img_path.name}, skipped", file=sys.stderr)
            continue
        units = reader.read(load_screen(img_path))
        per = {layer: [0, 0] for layer in LAYERS}
        for u in units:
            want = meta["units"].get(u["sinner"])
            if want is None:
                continue
            for layer in LAYERS:
                if layer not in want:
                    continue
                got = u["skills"][layer]
                tier = got["tier"] if got else None
                ok = tier == want[layer]
                per[layer][0] += ok
                per[layer][1] += 1
                hits[layer][0] += ok
                hits[layer][1] += 1
                if not ok:
                    worst.append(f"{meta_path.stem}/{u['sinner']} {layer}: read {tier}, "
                                 f"labelled {want[layer]}" + (f" (score {got['score']:.2f})" if got else ""))
                if verbose:
                    mark = "ok  " if ok else "MISS"
                    print(f"  {mark} {u['sinner']:<12} {layer:<6} read {tier} labelled {want[layer]}"
                          + (f"  score {got['score']:.2f} margin {got['margin']:.2f}" if got else ""))
        line = "  ".join(f"{layer} {per[layer][0]}/{per[layer][1]}" for layer in LAYERS)
        print(f"{meta_path.stem:<28} {line}    {meta.get('note', '')}")
    return hits, worst


def add(reader, path):
    DATA.mkdir(parents=True, exist_ok=True)
    dst = DATA / f"{path.stem}.png"
    shutil.copyfile(path, dst)
    units = reader.read(load_screen(dst))
    meta = {"note": "", "source": path.name,
            "units": {u["sinner"]: {layer: (u["skills"][layer]["tier"] if u["skills"][layer] else None)
                                    for layer in LAYERS} for u in units if u["sinner"]}}
    meta_path = dst.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {dst}\nwrote {meta_path}  <- the tiers are what the reader saw; fix any that are wrong")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", type=Path, metavar="SHOT", help="copy a screenshot in and pre-fill its labels")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every unit, not just totals")
    args = ap.parse_args()

    reader = Reader()
    if args.new:
        add(reader, args.new)
        return
    if not samples():
        sys.exit(f"no labelled samples in {DATA} - add one with --new")

    hits, worst = score(reader, args.verbose)
    print()
    for layer in LAYERS:
        ok, n = hits[layer]
        print(f"{layer:<7} {ok:3}/{n}  {ok / n * 100:5.1f}%" if n else f"{layer:<7} no labels")
    total_ok = sum(v[0] for v in hits.values())
    total_n = sum(v[1] for v in hits.values())
    print(f"{'all':<7} {total_ok:3}/{total_n}  {total_ok / total_n * 100:5.1f}%" if total_n else "")
    if worst:
        print("\nmisses:")
        for w in worst:
            print(f"  {w}")
    sys.exit(0 if not worst else 1)


if __name__ == "__main__":
    main()
