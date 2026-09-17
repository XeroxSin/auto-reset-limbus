"""Check a battle state (battle_state.py output) against a config of wanted conditions.

Config (JSON, see docs/verify-config.md):
    {
      "sinners": ["Heathcliff", "Sinclair"],          # only these are looked for
      "constraints": {
        "<id>": {"constraint_type": "order_absolute" | "order_relative" | "skill_tier",
                 "constraint_info": {...}},
        "<id>": {"constraint_type": "and" | "or",
                 "constraint_info": ["<id>" or {constraint}, ...]},
        "<id>": {"constraint_type": "not", "constraint_info": "<id>" or {constraint}},
        ...
      }
    }

Every constraint is True, False or unknown (a unit is missing, a tier was not
read, or the unit has warnings). Constraints that no and/or/not constraint uses
must all be True. The result is:
    valid        exit 0   stop
    invalid      exit 1   retry the stage
    unreadable   exit 3   read the screen again (--live does, up to 3 reads in
                          total; still unreadable after that counts as invalid)
    config error exit 2

Usage:
    python tools/verify_state.py configs/config.json tests/fixtures/states/battle_state_20260916_162233.json
    python tools/verify_state.py configs/config.json --live
    python tools/verify_state.py configs/config.json --live -d 5
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "assets" / "profiles" / "manifest.json"

SLOTS = ("bottom", "top", "next")        # skills.LAYERS
TIERS = (1, 2, 3)
COLUMNS = ("all", "any", "first", "last")
RELATIONS = ("before", "after")
MAX_READS = 3
REREAD_PAUSE = 1.0                       # icons fade in; also keeps output file names (1 s stamps) apart
UNIT_WARNING = re.compile(r"unit (\d+):")  # battle_state.BattleReader.read warning prefix

STATUS = {True: "valid", False: "invalid", None: "unreadable"}
EXIT = {"valid": 0, "invalid": 1, "unreadable": 3}
EXIT_ERROR = 2


class ConfigError(Exception):
    pass


def fail(path, msg):
    raise ConfigError(f"{path}: {msg}")


# ---- config ------------------------------------------------------------------

def is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def known_sinners():
    return {e["sinner"] for e in json.loads(MANIFEST.read_text(encoding="utf-8"))}


def parse_unit(path, v, ctx):
    if isinstance(v, str):
        v = {"sinner": v}
    if not isinstance(v, dict):
        fail(path, "expected a sinner name or {\"sinner\": ..., \"column\": ...}")
    extra = set(v) - {"sinner", "column"}
    if extra:
        fail(path, f"unknown key(s) {', '.join(sorted(extra))}")
    if v.get("sinner") not in ctx["sinners"]:
        fail(f"{path}.sinner", f"{v.get('sinner')!r} is not in sinners")
    column = v.get("column", "all")
    if not (column in COLUMNS or is_int(column) and column >= 0):
        fail(f"{path}.column", f"expected one of {', '.join(COLUMNS)} or a column index >= 0")
    return {"sinner": v["sinner"], "column": column}


def int_list(ok, what):
    def parse(path, v, ctx):
        vals = v if isinstance(v, list) else [v]
        if not vals or not all(is_int(x) and ok(x) for x in vals):
            fail(path, f"expected {what} or a list of them")
        return sorted(set(vals))
    return parse


def choice(options):
    def parse(path, v, ctx):
        if v not in options:
            fail(path, f"expected one of {', '.join(options)}")
        return v
    return parse


FIELDS = {  # constraint_type -> {constraint_info key: parser}
    "order_absolute": {"unit": parse_unit,
                       "position": int_list(lambda x: x >= 0, "a position >= 0")},
    "order_relative": {"unit": parse_unit, "relation": choice(RELATIONS), "other": parse_unit},
    "skill_tier": {"unit": parse_unit, "slot": choice(SLOTS),
                   "tier": int_list(lambda x: x in TIERS, "a tier 1-3")},
}
LOGIC = ("and", "or", "not")    # constraint_info: the constraints they apply to ("not": exactly one)
TYPES = (*FIELDS, *LOGIC)


def parse_logic(path, cid, ctype, info, ctx, out):
    """Items are constraint ids or inline constraint objects; inline ones are
    stored in `out` as <cid>.<index>. Returns the list of member ids."""
    if ctype == "not":
        if isinstance(info, list):
            if len(info) != 1:
                fail(path, "not takes exactly one constraint")
            paths = [f"{path}[0]"]
        else:
            info, paths = [info], [path]
    else:
        if not isinstance(info, list) or not info:
            fail(path, f"{ctype} takes a non-empty list of constraints (ids or constraint objects)")
        paths = [f"{path}[{i}]" for i in range(len(info))]

    ids = []
    for i, (item, ipath) in enumerate(zip(info, paths)):
        if isinstance(item, str):
            if item not in ctx["ids"]:
                fail(ipath, f"unknown constraint {item!r}")
            ids.append(item)
        elif isinstance(item, dict):
            child = f"{cid}.{i}"
            if child in ctx["ids"]:
                fail(ipath, f"the inline constraint's id {child!r} is already used by another constraint")
            parse_constraint(ipath, child, item, ctx, out)
            ids.append(child)
        else:
            fail(ipath, "expected a constraint id or a constraint object")
    if len(set(ids)) != len(ids):
        fail(path, "the same constraint is listed twice")
    return ids


def parse_constraint(path, cid, c, ctx, out):
    """Validate one constraint and add it (after any inline members) to `out`."""
    if not isinstance(c, dict):
        fail(path, "expected an object")
    extra = set(c) - {"constraint_type", "constraint_info"}
    if extra:
        fail(path, f"unknown key(s) {', '.join(sorted(extra))}")
    ctype = c.get("constraint_type")
    if ctype not in TYPES:
        fail(f"{path}.constraint_type", f"expected one of {', '.join(TYPES)}")
    path += ".constraint_info"
    if "constraint_info" not in c:
        fail(path, "missing")
    info = c["constraint_info"]
    if ctype in LOGIC:
        out[cid] = {"constraint_type": ctype, "constraint_info": parse_logic(path, cid, ctype, info, ctx, out)}
        return
    if not isinstance(info, dict):
        fail(path, "expected an object")
    fields = FIELDS[ctype]
    extra, missing = set(info) - set(fields), [k for k in fields if k not in info]
    if extra:
        fail(path, f"unknown key(s) {', '.join(sorted(extra))}")
    if missing:
        fail(path, f"missing {', '.join(missing)}")
    out[cid] = {"constraint_type": ctype,
                "constraint_info": {k: parse(f"{path}.{k}", info[k], ctx) for k, parse in fields.items()}}


def members(c):
    """Ids an and/or/not constraint applies to (empty for other types)."""
    return c["constraint_info"] if c["constraint_type"] in LOGIC else []


def check_cycles(constraints):
    done, active = set(), []

    def visit(cid):
        if cid in active:
            loop = " -> ".join(active[active.index(cid):] + [cid])
            fail(f"constraints.{cid}.constraint_info", f"loop: {loop}")
        if cid in done:
            return
        active.append(cid)
        for m in members(constraints[cid]):
            visit(m)
        active.pop()
        done.add(cid)

    for cid in constraints:
        visit(cid)


def parse_config(raw):
    """Validate a config dict and return it normalised: unit refs as dicts,
    tiers/positions as lists, and/or/not as lists of ids (inline ones included)."""
    if not isinstance(raw, dict):
        fail("config", "expected an object")
    if "logic" in raw:
        fail("logic", "the logic section was replaced by constraints of type \"and\", \"or\" "
                      "and \"not\" (see the README)")
    extra = set(raw) - {"sinners", "constraints"}
    if extra:
        fail("config", f"unknown key(s) {', '.join(sorted(extra))}")

    sinners = raw.get("sinners")
    if not isinstance(sinners, list) or not sinners or not all(isinstance(s, str) for s in sinners):
        fail("sinners", "expected a non-empty list of sinner names")
    if len(set(sinners)) != len(sinners):
        fail("sinners", "duplicate names")
    unknown = sorted(set(sinners) - known_sinners())
    if unknown:
        fail("sinners", f"unknown sinner(s) {', '.join(unknown)}")

    constraints = raw.get("constraints")
    if not isinstance(constraints, dict) or not constraints:
        fail("constraints", "expected a non-empty object")
    ctx = {"sinners": set(sinners), "ids": set(constraints)}
    out = {}
    for cid, c in constraints.items():
        parse_constraint(f"constraints.{cid}", cid, c, ctx, out)
    check_cycles(out)
    return {"sinners": list(sinners), "constraints": out}


def load_config(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"cannot read config: {e}") from e
    return parse_config(raw)


def roster(config):
    return set(config["sinners"])


# ---- evaluation ----------------------------------------------------------------

def k_and(vals):
    vals = list(vals)
    return False if False in vals else None if None in vals else True


def k_or(vals):
    vals = list(vals)
    return True if True in vals else None if None in vals else False


class State:
    def __init__(self, state):
        self.units = state.get("units", [])
        self.bad, self.all_bad = set(), False   # unit orders with warnings / a warning about the whole read
        for w in state.get("warnings", []):
            m = UNIT_WARNING.match(w)
            if m:
                self.bad.add(int(m[1]))
            else:
                self.all_bad = True

    def over(self, ref, test, reasons):
        """Apply test(unit) -> True/False/None to the columns ref selects, combined by its column rule."""
        cols = [u for u in self.units if u["sinner"] == ref["sinner"]]
        if not cols:
            reasons.append(f"{ref['sinner']} not found")
            return None
        column = ref["column"]
        if column in ("all", "any"):
            picked = cols
        elif column == "first":
            picked = cols[:1]
        elif column == "last":
            picked = cols[-1:]
        else:
            picked = cols[column:column + 1]
            if not picked:
                reasons.append(f"{ref['sinner']} has {len(cols)} column(s), no column {column}")
                return False
        vals = []
        for u in picked:
            if self.all_bad or u["order"] in self.bad:
                reasons.append(f"unit {u['order']} ({u['sinner']}) has warnings")
                vals.append(None)
            else:
                vals.append(test(u))
        return k_or(vals) if column == "any" else k_and(vals)


def fmt(vals):
    return str(vals[0]) if len(vals) == 1 else " or ".join(map(str, vals))


def check_order_absolute(st, info, reasons):
    def test(u):
        if u["order"] in info["position"]:
            return True
        reasons.append(f"{u['sinner']} at order {u['order']}, expected {fmt(info['position'])}")
        return False
    return st.over(info["unit"], test, reasons)


def check_order_relative(st, info, reasons):
    relation = info["relation"]

    def test(u):
        def test_other(o):
            ok = u["order"] < o["order"] if relation == "before" else u["order"] > o["order"]
            if not ok:
                reasons.append(f"{u['sinner']} at order {u['order']} is not {relation} "
                               f"{o['sinner']} at order {o['order']}")
            return ok
        return st.over(info["other"], test_other, reasons)
    return st.over(info["unit"], test, reasons)


def check_skill_tier(st, info, reasons):
    slot = info["slot"]

    def test(u):
        tier = u["skills"].get(slot)
        if tier is None:
            reasons.append(f"unit {u['order']} ({u['sinner']}) {slot} skill not read")
            return None
        if tier in info["tier"]:
            return True
        reasons.append(f"unit {u['order']} ({u['sinner']}) {slot} is tier {tier}, expected {fmt(info['tier'])}")
        return False
    return st.over(info["unit"], test, reasons)


CHECKS = {
    "order_absolute": check_order_absolute,
    "order_relative": check_order_relative,
    "skill_tier": check_skill_tier,
}


def verify(state, config):
    """state: battle_state output; config: from load_config/parse_config.

    Returns {"status": "valid"|"invalid"|"unreadable",
             "constraints": {id: True|False|None},        in config order
             "reasons": {id: [...]},                      only for checks that were not True
             "roots": [ids no and/or/not constraint uses],
             "logic": {and/or/not id: {"type": ..., "members": [ids]}}}
    The roots must all be True for "valid".
    """
    st = State(state)
    constraints = config["constraints"]
    results, reasons = {}, {}

    def value(cid):
        if cid not in results:
            c = constraints[cid]
            info = c["constraint_info"]
            if c["constraint_type"] == "not":
                v = value(info[0])
                results[cid] = None if v is None else not v
            elif c["constraint_type"] in LOGIC:
                results[cid] = (k_and if c["constraint_type"] == "and" else k_or)(value(m) for m in info)
            else:
                r = []
                results[cid] = CHECKS[c["constraint_type"]](st, info, r)
                if results[cid] is not True and r:
                    reasons[cid] = list(dict.fromkeys(r))
        return results[cid]

    results = {cid: value(cid) for cid in constraints}
    used = {m for c in constraints.values() for m in members(c)}
    roots = [cid for cid in constraints if cid not in used]
    logic = {cid: {"type": c["constraint_type"], "members": members(c)}
             for cid, c in constraints.items() if c["constraint_type"] in LOGIC}
    return {"status": STATUS[k_and(results[cid] for cid in roots)], "constraints": results,
            "reasons": reasons, "roots": roots, "logic": logic}


def verify_live(read_state, config, attempts=MAX_READS):
    """read_state(attempt) -> state dict, attempt counting from 0. Re-reads while the
    result is unreadable; still unreadable after `attempts` reads counts as invalid.

    Returns (result, last state); result also has "reads" and, when it gave up, "note".
    """
    for attempt in range(attempts):
        state = read_state(attempt)
        result = verify(state, config)
        if result["status"] != "unreadable":
            break
    else:
        result["status"] = "invalid"
        result["note"] = f"still unreadable after {attempts} reads"
    result["reads"] = attempt + 1
    return result, state


# ---- CLI -----------------------------------------------------------------------

def mark(v):
    return "valid  " if v else "INVALID" if v is False else "unknown"


def report_lines(name, result):
    """Every constraint as a tree (and/or/not members indented under them), then the verdict."""
    lines = [f"{name}:"]
    logic = result["logic"]

    def add(cid, depth):
        pad = "  " * (depth + 1)
        kind = f" ({logic[cid]['type']})" if cid in logic else ""
        lines.append(f"{pad}{mark(result['constraints'][cid])}  {cid}{kind}")
        lines.extend(f"{pad}           {r}" for r in result["reasons"].get(cid, []))
        for m in logic.get(cid, {}).get("members", []):
            add(m, depth + 1)

    for cid in result["roots"]:
        add(cid, 0)
    reads = f" after {result['reads']} read(s)" if "reads" in result else ""
    lines.append(f"  => {result['status'].upper()}{reads}")
    if "note" in result:
        lines.append(f"     {result['note']}")
    return lines


def report(name, result):
    print("\n".join(report_lines(name, result)))


def live(config, args):
    import battle_state as bs
    import capture_screen as cap

    hwnd = bs.find_game(args.title)
    print("loading templates...")
    reader = bs.BattleReader(args.sin_mode, roster(config))
    print("templates loaded")

    def read_state(attempt):
        if attempt == 0:
            img, _ = bs.capture(hwnd, args.delay)
        else:
            time.sleep(REREAD_PAUSE)
            img = cap.grab(cap.game_rect(hwnd))
        when = datetime.now()
        state = {"source": "live", "captured_at": when.isoformat(timespec="seconds")}
        state.update(reader.read(img))
        path = bs.live_output_path(when)
        bs.write_json(path, state)
        print(f"read {attempt + 1}: {len(state['units'])} unit(s) -> {path}")
        bs.print_units(state)
        for w in state["warnings"]:
            print(f"  warning: {w}")
        return state

    result, _ = verify_live(read_state, config)
    report("live", result)
    return result["status"]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", type=Path, help="verify config JSON")
    ap.add_argument("states", nargs="*", type=Path, help="battle_state JSON files to check")
    ap.add_argument("--live", action="store_true", help="read the game screen instead (up to 3 reads)")
    ap.add_argument("-d", "--delay", type=float, default=3.0, help="countdown for --live (default 3)")
    ap.add_argument("-t", "--title", default="LimbusCompany",  # capture_screen.WINDOW_TITLE (not imported: cv2)
                    help="game window title")
    ap.add_argument("--sin-mode", choices=("shape", "color"), default="shape",
                    help="how sins are told apart while reading tiers (default shape)")
    args = ap.parse_args()
    if not args.states and not args.live:
        ap.error("give at least one state file or --live")

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"error: {args.config}: {e}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    statuses = []
    for path in args.states:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"error: {path}: {e}", file=sys.stderr)
            sys.exit(EXIT_ERROR)
        result = verify(state, config)
        report(path.name, result)
        statuses.append(result["status"])
    if args.live:
        try:
            statuses.append(live(config, args))
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(EXIT_ERROR)

    # worst first: unreadable, then invalid, then valid
    sys.exit(max(EXIT[s] for s in statuses))


if __name__ == "__main__":
    main()
