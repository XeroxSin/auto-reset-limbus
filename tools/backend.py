"""The OS layer: find the game window, grab pixels, send input.

Everything above this module is platform-neutral. The vision code (portraits,
identify, skills), the rules in verify_state and the movement model all work on
arrays and numbers, so they need no port. Only the handful of primitives below
differ between operating systems; each platform implements them in its own
backend module and this one picks the right implementation and re-exports it
under one set of names.

The contract:

    set_dpi_aware()                 once at start-up, before any coordinate is read
    find_window(title) -> win       raises RuntimeError when there is no such window
    window_exists(win) -> bool
    is_foreground(win) -> bool
    foreground_title() -> str       "" when nothing is focused
    client_rect(win) -> (x, y, w, h)    client area in screen pixels
    grab(rect) -> ndarray           (h, w, 3) BGR of that screen rect

    get_position() -> (x, y)
    virtual_screen_bounds() -> (min_x, min_y, max_x, max_y)
    send_mouse_rel(dx, dy)          relative counts, the way a mouse sends them
    send_mouse_button(button, down) button: "left" | "right"
    send_key(key, down)             key: one of KEYS
    mouse_settings                  .apply() / .restore(): 1:1 pointer motion

`win` is an opaque handle - an HWND on Windows, an Xlib window on X11. Callers
hold on to it and pass it back in; only the backend looks inside it.

client_rect raises RuntimeError when the window is minimized or has an empty
client area, so callers can report it the same way on either platform.
"""
import sys

if sys.platform == "win32":
    import backend_win32 as _impl
elif sys.platform.startswith("linux"):
    import backend_x11 as _impl
else:
    raise RuntimeError(f"no backend for {sys.platform}; Windows and Linux/X11 are supported")

NAME = _impl.NAME
KEYS = _impl.KEYS

set_dpi_aware = _impl.set_dpi_aware
find_window = _impl.find_window
window_exists = _impl.window_exists
is_foreground = _impl.is_foreground
foreground_title = _impl.foreground_title
client_rect = _impl.client_rect
grab = _impl.grab

get_position = _impl.get_position
virtual_screen_bounds = _impl.virtual_screen_bounds
send_mouse_rel = _impl.send_mouse_rel
send_mouse_button = _impl.send_mouse_button
send_key = _impl.send_key
mouse_settings = _impl.mouse_settings
