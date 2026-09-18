"""Human-like mouse/keyboard input, driven by Charge Grinder's movement model.

Follows docs/human-mouse-movement.md: closed-loop moves planned by
movement/builder.py (endpoint scatter inside the target box, data-driven
duration, sub-movements, tremor), played back in real time as relative counts,
Gaussian press/hold times, and a focus fail-safe.

Sending the counts is the operating system's business and lives in backend.py:
SendInput on Windows, XTEST on Linux. Charge Grinder's own bridge.dll (Logitech
/ Razer virtual devices) is not replicated on either.

The movement/ package is copied from Charge Grinder 3.5.0 (GPL-3.0).
"""
import random
import time

import numpy as np

import backend

from .movement.builder import build_trajectory
from .movement.inertia import get_inherited_velocity, update_inertia
from .movement.pointer_gain import execute_trajectory, update_pointer_scale

WINDOW_TITLE = "LimbusCompany"

send_mouse_rel = backend.send_mouse_rel
send_mouse_button = backend.send_mouse_button
send_key = backend.send_key
get_position = backend.get_position
virtual_screen_bounds = backend.virtual_screen_bounds
foreground_title = backend.foreground_title
mouse_settings = backend.mouse_settings   # the backend restores it at exit


# --- Fail-safe ------------------------------------------------------------------

class FocusLost(Exception):
    pass


def fail_safe_check():
    if WINDOW_TITLE not in foreground_title():
        mouse_settings.restore()
        raise FocusLost(foreground_title())


# --- Timing helpers (Charge Grinder's SAFE profile) -----------------------------

def _gauss_ms(median, iqr, lo, hi):
    return max(lo, min(hi, random.gauss(median, max(1.0, iqr / 1.349)))) / 1000.0


def click_hold():
    return _gauss_ms(90.0, 18.0, 38.0, 220.0)


def key_hold():
    return _gauss_ms(100.0, 31.0, 32.0, 260.0)


def jitter(base, lo=0.95, hi=1.2):
    return base * random.uniform(lo, hi) if base > 0 else base


# --- Movement -------------------------------------------------------------------

def _within(a, b, size):
    return abs(a[0] - b[0]) <= size[0] / 2 and abs(a[1] - b[1]) <= size[1] / 2


def move_to(x, y, tsize=(5.0, 5.0), curve=1.0, n_sub=None, duration=None, inertia=False,
            emit=send_mouse_rel, check=fail_safe_check):
    """Move to a screen-pixel target. tsize = (w, h) of the clickable area; the
    landing point is scattered inside it. emit/check can be swapped for dry runs."""
    check()
    mouse_settings.apply()
    min_x, min_y, max_x, max_y = virtual_screen_bounds()
    end = (min(max(int(round(x)), min_x), max_x - 1), min(max(int(round(y)), min_y), max_y - 1))
    start = get_position()
    if _within(start, end, tsize):
        return

    path, times = None, None
    for _ in range(6):  # a few correction attempts, as a person would make
        traj = build_trajectory(
            start, end,
            duration_override=duration,
            target_width=tsize[0], target_height=tsize[1],
            initial_velocity=get_inherited_velocity() if inertia else None,
            curviness=curve,
            n_submovements=n_sub,
        )
        path = np.asarray(traj["points"], dtype=float)
        path[:, 0] = np.clip(path[:, 0], min_x, max_x - 1)
        path[:, 1] = np.clip(path[:, 1], min_y, max_y - 1)
        times = traj["times"]

        raw_delta = execute_trajectory(None, path, times, emit_func=lambda _dev, dx, dy: emit(dx, dy))
        start = get_position()
        if not np.any(np.abs(raw_delta) > 15.0) or _within(start, end, tsize):
            break
        # Cursor missed: learn the counts->pixels gain curve and re-plan from here
        update_pointer_scale(raw_delta, path[0], start)
        check()

    if path is not None:
        update_inertia(path, times)


def click(x=None, y=None, tsize=(5.0, 5.0), button="left", delay=0.03):
    fail_safe_check()
    delay = jitter(delay)
    if x is not None and y is not None:
        time.sleep(jitter(delay + 0.02))
        move_to(x, y, tsize=tsize)
    fail_safe_check()
    send_mouse_button(button, True)
    time.sleep(click_hold())
    send_mouse_button(button, False)
    time.sleep(random.uniform(delay, delay + 0.05))


def drag(x1, y1, x2, y2, tsize=(5.0, 5.0), button="left", delay=0.03):
    """Press at (x1, y1), move to (x2, y2) with the button held, release there."""
    fail_safe_check()
    move_to(x1, y1, tsize=tsize)
    fail_safe_check()
    send_mouse_button(button, True)
    try:
        time.sleep(click_hold())            # settle before moving, as a hand would
        move_to(x2, y2, tsize=tsize)
        time.sleep(click_hold())
    finally:
        send_mouse_button(button, False)
    time.sleep(random.uniform(delay, delay + 0.05))


def press(key, delay=0.09):
    time.sleep(jitter(delay))
    fail_safe_check()
    send_key(key, True)
    time.sleep(key_hold())
    send_key(key, False)
