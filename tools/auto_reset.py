"""Retry the stage until the battle state matches a verify config.

Loop:
  1. wait until a battle turn is showing (portraits found, Esc menu closed),
  2. capture and read the state:
       readable -> check it against the config (see verify_state.py),
       unclear  -> drag the battlefield upward, so the skill icons end up over
                   plain ground instead of busy stage art, wait, and capture
                   again; up to 3 reads in total,
  3. valid: stop. invalid: move the cursor out of the way, Esc -> Retry Stage,
     wait, back to 1.

The wait after a reset and before a re-capture is the same, 3 s by default
(-w/--wait).

There is no reset limit: press Ctrl+C in this terminal to stop. Whenever the
game is not the foreground window, everything pauses; once it is back in
front, the current step starts over.

The terminal shows pauses, captures and every check (each constraint, each
logic group, then the verdict). Everything else - identity and skill scores,
button scores, cursor moves, pointer gain updates, the raw states - goes to
output/logs/auto_reset_<YYYYMMDD_HHMMSS>.log.

Usage:
    python tools/auto_reset.py                                # uses configs/config.json
    python tools/auto_reset.py configs/other.json
    python tools/auto_reset.py -d 10                          # longer start delay
    python tools/auto_reset.py -w 5                           # wait 5 s after a reset / before a re-capture
    python tools/auto_reset.py --no-pan                       # never drag the battlefield
    python tools/auto_reset.py --save                         # also write every read to output/
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import battle_state as bs
import capture_screen as cap
import retry_stage as rs
from humanmouse import human_input as hi
from portraits import find_portraits, to_base
from verify_state import (EXIT, EXIT_ERROR, MAX_READS, ConfigError, load_config, report_lines, roster,
                          verify_live)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "configs" / "config.json"
LOG_DIR = ROOT / "output" / "logs"
START_DELAY = 5.0       # time to switch to the game
WAIT = 3.0              # after clicking Retry Stage, and before a re-capture
PAN_FROM = (960, 745)   # 1080p: empty ground under the party, clear of the units and the skill rows
PAN_DY = 140            # how far up to drag the battlefield
POLL = 0.5              # between checks for the battle turn
SETTLE = 0.5            # after the turn shows up, so the skill icons finish fading in
RESUME_SETTLE = 0.5     # after the game comes back to the front
STATUS_EVERY = 10.0     # "still waiting" log interval

log = logging.getLogger("auto_reset")
console = logging.getLogger("auto_reset.console")   # shown in the terminal and written to the log


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"auto_reset_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    to_file = logging.FileHandler(path, encoding="utf-8")
    to_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(to_file)
    to_screen = logging.StreamHandler(sys.stdout)
    to_screen.setFormatter(logging.Formatter("%(message)s"))
    console.addHandler(to_screen)
    return path


class Paused(Exception):
    """The game lost focus; the current step starts over once it is back."""


class Game:
    def __init__(self, hwnd):
        self.hwnd = hwnd

    def focused(self):
        return cap.is_foreground(self.hwnd)

    def check(self):
        if not cap.window_exists(self.hwnd):
            raise RuntimeError("the game window was closed")
        if not self.focused():
            raise Paused

    def wait_focus(self):
        if self.focused():
            return
        console.info("paused: the game is not in front (switch back to continue, Ctrl+C to stop)")
        while not self.focused():
            if not cap.window_exists(self.hwnd):
                raise RuntimeError("the game window was closed")
            time.sleep(0.2)
        console.info("resumed")
        time.sleep(RESUME_SETTLE)

    def sleep(self, seconds):
        """time.sleep that pauses (raises Paused) as soon as the game loses focus."""
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            self.check()
            time.sleep(min(0.1, left))
        self.check()

    def grab(self):
        self.check()
        rect = cap.game_rect(self.hwnd)
        img = to_base(cap.grab(rect))
        self.check()  # another window may have covered the game during the grab
        return img


def wait_for_turn(game, template, button):
    start = last = time.monotonic()
    log.info("waiting for the battle turn")
    while True:
        frame = game.grab()
        box, score = rs.locate(frame, button)
        portraits = find_portraits(frame, template)
        if box is None and portraits:
            break
        if time.monotonic() - last >= STATUS_EVERY:
            last = time.monotonic()
            log.info("still waiting for the battle turn (%.0f s; menu score %.2f, %d portrait(s))",
                     last - start, score, len(portraits))
        game.sleep(POLL)
    log.info("battle turn showing after %.1f s (%d portrait(s))", time.monotonic() - start, len(portraits))
    game.sleep(SETTLE)


def to_screen(rect, x, y):
    """1080p point -> screen point, for the game area at `rect`."""
    left, top, w, _ = rect
    s = w / 1920
    return left + x * s, top + y * s


def pan_up(game, dy):
    """Drag the battlefield upward. The skill icons stay where they are, but what
    sits behind them becomes plain ground, which the faint next-in-line icon
    reads far better on."""
    game.check()
    rect = cap.game_rect(game.hwnd)
    start = to_screen(rect, *PAN_FROM)
    end = to_screen(rect, PAN_FROM[0], PAN_FROM[1] - dy)
    try:
        hi.drag(*start, *end)
    except hi.FocusLost as e:
        log.info("focus lost while panning (foreground: %s)", e)
        raise Paused from None
    log.info("panned the battlefield up %d px (%s -> %s)", dy,
             tuple(map(round, start)), tuple(map(round, end)))


def reset(game, button):
    game.check()
    try:
        rs.retry_stage(game.hwnd, button)
    except hi.FocusLost as e:
        log.info("focus lost during the reset (foreground: %s)", e)
        raise Paused from None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", type=Path, nargs="?", default=DEFAULT_CONFIG,
                    help="verify config JSON (default configs/config.json)")
    ap.add_argument("-d", "--delay", type=float, default=START_DELAY,
                    help=f"seconds to switch to the game before starting (default {START_DELAY:g})")
    ap.add_argument("-w", "--wait", type=float, default=WAIT,
                    help=f"seconds to wait after a reset and before a re-capture (default {WAIT:g})")
    ap.add_argument("--pan-dy", type=int, default=PAN_DY,
                    help=f"how far to drag the battlefield up after an unclear read (default {PAN_DY})")
    ap.add_argument("--no-pan", action="store_true", help="never drag the battlefield")
    ap.add_argument("-t", "--title", default=cap.WINDOW_TITLE, help="game window title")
    ap.add_argument("--save", action="store_true", help="also write every read to output/ like battle_state.py")
    args = ap.parse_args()

    log_path = setup_logging()
    console.info(f"log: {log_path}")
    log.info("config %s, options %s", args.config, vars(args))
    try:
        config = load_config(args.config)
    except ConfigError as e:
        console.error(f"error: {args.config}: {e}")
        sys.exit(EXIT_ERROR)
    log.debug("config: %s", json.dumps(config))

    cap.set_dpi_aware()
    hi.WINDOW_TITLE = args.title
    try:
        game = Game(bs.find_game(args.title))
        button = rs.load_button()
    except RuntimeError as e:
        console.error(f"error: {e}")
        sys.exit(EXIT_ERROR)
    log.info("game window %s, rect %s; loading templates", game.hwnd, cap.game_rect(game.hwnd))
    reader = bs.BattleReader(roster(config))
    log.info("templates loaded (%d profile windows)", len(reader.profiles))

    check_no, panned = 0, False

    def read_state(attempt):
        nonlocal panned
        if attempt:
            if not args.no_pan and not panned:
                console.info(f"check {check_no}: read was unclear, dragging the view up")
                pan_up(game, args.pan_dy)
                panned = True
            game.sleep(args.wait)
            console.info(f"check {check_no}: capturing again (read {attempt + 1} of {MAX_READS})")
        else:
            console.info(f"check {check_no}: capturing")
        img = game.grab()
        when = datetime.now()
        state = {"source": "live", "captured_at": when.isoformat(timespec="seconds")}
        state.update(reader.read(img))
        log.info("read %d: %s", attempt + 1, json.dumps(state, ensure_ascii=False))
        if args.save:
            path = bs.live_output_path(when)
            bs.write_json(path, state)
            log.info("saved %s", path)
        return state

    resets, pending_reset = 0, False
    console.info(f"Switch to the game now ({args.delay:g} s). Press Ctrl+C here to stop.")
    try:
        cap.countdown(args.delay)
        while True:
            try:
                game.wait_focus()
                if pending_reset:
                    log.info("reset %d", resets + 1)
                    reset(game, button)
                    pending_reset = False
                    resets += 1
                    log.info("reset %d done; waiting %.1f s", resets, args.wait)
                    game.sleep(args.wait)
                wait_for_turn(game, reader.frame_template, button)
                check_no, panned = resets + 1, False
                result, _ = verify_live(read_state, config)
                log.debug("result: %s", json.dumps(result))
                for line in report_lines(f"check {check_no}", result):
                    console.info(line)
                if result["status"] == "valid":
                    console.info(f"done after {resets} reset(s)")
                    sys.exit(EXIT["valid"])
                console.info("   retrying the stage")
                pending_reset = True
            except Paused:
                log.info("paused; restarting the current step once the game is back")
                continue
            except RuntimeError as e:
                if not cap.window_exists(game.hwnd):
                    console.error(f"error: {e}")
                    sys.exit(EXIT_ERROR)
                log.exception("step failed")
                console.warning(f"   {e}; trying again")
                time.sleep(1.0)
    except KeyboardInterrupt:
        console.info(f"\nstopped by user after {resets} reset(s)")
        sys.exit(130)
    finally:
        hi.mouse_settings.restore()
        logging.shutdown()


if __name__ == "__main__":
    main()
