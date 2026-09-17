"""Copy every *_profile.png from the art dump into assets/profiles.

The source tree is left untouched. The copy keeps the <Sinner>/<Identity>/
layout and writes manifest.json, one entry per portrait, for the identity
matcher to load.

Source layout:  <Sinner>/Identities/<Identity>/<id>_<variant>_profile.png
  variant "normal"   = base art
  variant "gacksung" = uptie 3 art

Usage:
    python tools/collect_profiles.py
    python tools/collect_profiles.py --src "Identity & EGO Art" --dst assets/profiles
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUFFIX = "_profile.png"
NAME_RE = re.compile(r"^(?P<id>\d+)_(?P<variant>[a-z]+)_profile\.png$")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=ROOT / "Identity & EGO Art")
    ap.add_argument("--dst", type=Path, default=ROOT / "assets" / "profiles")
    args = ap.parse_args()

    if not args.src.is_dir():
        sys.exit(f"error: source folder not found: {args.src}")

    entries, skipped = [], []
    for src in sorted(args.src.rglob(f"*{SUFFIX}")):
        rel = src.relative_to(args.src)
        m = NAME_RE.match(src.name)
        # expected: <Sinner>/Identities/<Identity>/<file>
        if not m or len(rel.parts) != 4 or rel.parts[1] != "Identities":
            skipped.append(str(rel))
            continue
        sinner, _, identity, _ = rel.parts
        out_rel = Path(sinner, identity, src.name)
        out = args.dst / out_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        entries.append({
            "id": int(m["id"]),
            "sinner": sinner,
            "identity": identity,
            "variant": m["variant"],
            "path": out_rel.as_posix(),
        })

    (args.dst / "manifest.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    ids = {e["id"] for e in entries}
    print(f"copied {len(entries)} portraits ({len(ids)} identities) to {args.dst}")
    for rel in skipped:
        print(f"  skipped (unexpected path/name): {rel}")


if __name__ == "__main__":
    main()
