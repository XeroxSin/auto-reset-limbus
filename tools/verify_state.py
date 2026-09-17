"""Check a battle state (battle_state.py output) against a config of wanted conditions.

Config (JSON, see docs/verify-config.md):
    {
      "sinners": ["Heathcliff", "Sinclair"],          # only these are looked for
      "constraints": {
        "<id>": {"constraint_type": "order_absolute" | "order_relative" | "skill_tier",
                 "constraint_info": {...}},
        ...
      },
      "logic": [{"or": ["<id>", "<id>"]}, ...]        # optional; unreferenced ids are ANDed
    }

Every constraint is True, False or unknown (a unit is missing, a tier was not
read, or the unit has warnings). The result is:
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


def parse_unit(path, v, sinners):
    if isinstance(v, str):
        v = {"sinner": v}
    if not isinstance(v, dict):
        fail(path, "expected a sinner name or {\"sinner\": ..., \"column\": ...}")
    extra = set(v) - {"sinner", "column"}
    if extra:
        fail(path, f"unknown key(s) {', '.join(sorted(extra))}")
    if v.get("sinner") not in sinners:
        fail(f"{path}.sinner", f"{v.get('sinner')!r} is not in sinners")
    column = v.get("column", "all")
    if not (column in COLUMNS or is_int(column) and column >= 0):
        fail(f"{path}.column", f"expected one of {', '.join(COLUMNS)} or a column index >= 0")
    return {"sinner": v["sinner"], "column": column}


def int_list(ok, what):
    def parse(path, v, sinners):
        vals = v if isinstance(v, list) else [v]
        if not vals or not all(is_int(x) and ok(x) for x in vals):
            fail(path, f"expected {what} or a list of them")
        return sorted(set(vals))
    return parse


def choice(options):
    def parse(path, v, sinners):
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


def parse_constraint(path, c, sinners):
    if not isinstance(c, dict):
        fail(path, "expected an object")
    extra = set(c) - {"constraint_type", "constraint_info"}
    if extra:
        fail(path, f"unknown key(s) {', '.join(sorted(extra))}")
    ctype = c.get("constraint_type")
    if ctype not in FIELDS:
        fail(f"{path}.constraint_type", f"expected one of {', '.join(FIELDS)}")
    path += ".constraint_info"
    info = c.get("constraint_info")
    if not isinstance(info, dict):
        fail(path, "expected an object")
    fields = FIELDS[ctype]
    extra, missing = set(info) - set(fields), [k for k in fields if k not in info]
    if extra:
        fail(path, f"unknown key(s) {', '.join(sorted(extra))}")
    if missing:
        fail(path, f"missing {', '.join(missing)}")
    return {"constraint_type": ctype,
            "constraint_info": {k: parse(f"{path}.{k}", info[k], sinners) for k, parse in fields.items()}}


def parse_expr(path, e, constraints):
    if isinstance(e, str):
        if e not in constraints:
            fail(path, f"unknown constraint {e!r}")
        return e
    if not isinstance(e, dict) or len(e) != 1:
        fail(path, "expected a constraint id or one of {\"and\": [...]}, {\"or\": [...]}, {\"not\": ...}")
    (op, arg), = e.items()
    if op == "not":
        return {"not": parse_expr(f"{path}.not", arg, constraints)}
    if op not in ("and", "or"):
        fail(path, f"unknown operator {op!r} (use and, or, not)")
    if not isinstance(arg, list) or not arg:
        fail(f"{path}.{op}", "expected a non-empty list")
    return {op: [parse_expr(f"{path}.{op}[{i}]", x, constraints) for i, x in enumerate(arg)]}


def parse_config(raw):
    """Validate a config dict and return it normalised (unit refs as dicts, tiers/positions as lists)."""
    if not isinstance(raw, dict):
        fail("config", "expected an object")
    extra = set(raw) - {"sinners", "constraints", "logic"}
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
    constraints = {cid: parse_constraint(f"constraints.{cid}", c, set(sinners))
                   for cid, c in constraints.items()}

    logic = raw.get("logic", [])
    if not isinstance(logic, list):
        fail("logic", "expected a list")
    logic = [parse_expr(f"logic[{i}]", e, constraints) for i, e in enumerate(logic)]
    return {"sinners": list(sinners), "constraints": constraints, "logic": logic}


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


def referenced(expr):
    if isinstance(expr, str):
        return {expr}
    (op, arg), = expr.items()
    return referenced(arg) if op == "not" else set().union(*map(referenced, arg))


def evaluate(expr, results):
    if isinstance(expr, str):
        return results[expr]
    (op, arg), = expr.items()
    if op == "not":
        v = evaluate(arg, results)
        return None if v is None else not v
    return (k_and if op == "and" else k_or)(evaluate(e, results) for e in arg)


def expr_text(expr):
    if isinstance(expr, str):
        return expr
    (op, arg), = expr.items()
    return f"not {expr_text(arg)}" if op == "not" else f"{op}({', '.join(map(expr_text, arg))})"


def verify(state, config):
    """state: battle_state output; config: from load_config/parse_config.

    Returns {"status": "valid"|"invalid"|"unreadable",
             "constraints": {id: True|False|None},
             "logic": [(text, True|False|None), ...],   one per logic entry
             "reasons": {id: [...]}}   (only for constraints that were not True)
    """
    st = State(state)
    results, reasons = {}, {}
    for cid, c in config["constraints"].items():
        r = []
        results[cid] = CHECKS[c["constraint_type"]](st, c["constraint_info"], r)
        if results[cid] is not True and r:
            reasons[cid] = list(dict.fromkeys(r))
    used = set().union(*map(referenced, config["logic"]))
    logic = [(expr_text(e), evaluate(e, results)) for e in config["logic"]]
    terms = [v for _, v in logic] + [v for cid, v in results.items() if cid not in used]
    return {"status": STATUS[k_and(terms)], "constraints": results, "logic": logic, "reasons": reasons}


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
    """Every constraint, then every logic group, then the verdict."""
    lines = [f"{name}:"]
    for cid, v in result["constraints"].items():
        lines.append(f"  {mark(v)}  {cid}")
        lines += [f"             {r}" for r in result["reasons"].get(cid, [])]
    for text, v in result.get("logic", []):
        lines.append(f"  {mark(v)}  logic: {text}")
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
