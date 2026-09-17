"""Retry the stage until the battle state matches a verify config.

Loop:
  1. wait until a battle turn is showing (portraits found, Esc menu closed),
  2. capture, read the state and check it against the config; an unclear read
     is captured again, up to 3 reads in total (see verify_state.py),
  3. valid: stop. invalid: move the cursor out of the way, Esc -> Retry Stage,
     wait 3 s, back to 1.

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
from verify_state import (EXIT, EXIT_ERROR, MAX_READS, REREAD_PAUSE, ConfigError, load_config, report_lines,
                          roster, verify_live)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "configs" / "config.json"
LOG_DIR = ROOT / "output" / "logs"
START_DELAY = 5.0       # time to switch to the game
RESET_WAIT = 3.0        # after clicking Retry Stage, before looking for the next turn
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
        return cap.user32.GetForegroundWindow() == self.hwnd

    def check(self):
        if not cap.user32.IsWindow(self.hwnd):
            raise RuntimeError("the game window was closed")
        if not self.focused():
            raise Paused

    def wait_focus(self):
        if self.focused():
            return
        console.info("paused: the game is not in front (switch back to continue, Ctrl+C to stop)")
        while not self.focused():
            if not cap.user32.IsWindow(self.hwnd):
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
    ap.add_argument("-t", "--title", default=cap.WINDOW_TITLE, help="game window title")
    ap.add_argument("--sin-mode", choices=("shape", "color"), default="shape",
                    help="how sins are told apart while reading tiers (default shape)")
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
    reader = bs.BattleReader(args.sin_mode, roster(config))
    log.info("templates loaded (%d profile windows)", len(reader.profiles))

    check_no = 0

    def read_state(attempt):
        if attempt:
            game.sleep(REREAD_PAUSE)
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
                    log.info("reset %d done; waiting %.1f s", resets, RESET_WAIT)
                    game.sleep(RESET_WAIT)
                wait_for_turn(game, reader.frame_template, button)
                check_no = resets + 1
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
                if not cap.user32.IsWindow(game.hwnd):
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
