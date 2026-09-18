"""Linux/X11 backend: window lookup over X, screen capture with mss, input with XTEST.

Mirrors what the Windows backend does with the Win32 API:

  window lookup   the EWMH _NET_CLIENT_LIST, matched on the window name, with a
                  walk of the window tree as a fallback for bare window managers
  foreground      _NET_ACTIVE_WINDOW, falling back to the input focus
  capture         mss grabs the rect off the root window, so - exactly like the
                  BitBlt on Windows - whatever is on top of the game is what
                  ends up in the frame
  input           XTEST fake_input: relative pointer counts, real button numbers
                  and keycodes, so the game sees them as ordinary device input

The game runs under Proton, so its window is an ordinary X11 (or XWayland)
window and none of this needs to know about Wine.

Wayland: a pure Wayland session has no way to read another window's pixels
without a portal, and XTEST cannot reach native Wayland clients. Under XWayland
the game window itself is an X window and input works, but grabbing the root
window can come back black, because XWayland has no composited root. If frames
are black, set LIMBUS_X11_CAPTURE=window to read the game window's own pixels
with XGetImage instead (slower, but it does not depend on the root), or log into
an X11 session.

Needs python-xlib and mss; pip install -r requirements.txt picks them up on Linux.
"""
import atexit
import logging
import os
import shutil
import subprocess

import numpy as np
from Xlib import X, XK, Xatom, display, error

NAME = "x11"
KEYS = ("esc", "enter", "space", "tab")

KEYSYMS = {"esc": XK.XK_Escape, "enter": XK.XK_Return, "space": XK.XK_space, "tab": XK.XK_Tab}
BUTTONS = {"left": 1, "right": 3}

# "root" grabs the screen (what Windows does); "window" reads the game window's
# own pixels, for XWayland or a compositor that leaves the root empty.
CAPTURE = os.environ.get("LIMBUS_X11_CAPTURE", "root")

XTEST_POINTER = "Virtual core XTEST pointer"
ACCEL_PROFILE = "Device Accel Profile"   # -1 = no acceleration

log = logging.getLogger(__name__)

_display = None
_screenshotter = None
_keycodes = {}
_game_window = None   # set by find_window, used by the window capture mode


def _dpy():
    """The X connection, opened on first use."""
    global _display
    if _display is None:
        if not os.environ.get("DISPLAY"):
            raise RuntimeError(
                "$DISPLAY is not set - this tool needs X11. On a Wayland session, log in with "
                "an X11/Xorg session, or run under XWayland with DISPLAY pointing at it.")
        try:
            _display = display.Display()
        except Exception as e:                       # DisplayError, ConnectionError, ...
            raise RuntimeError(f"cannot open the X display {os.environ['DISPLAY']}: {e}") from None
        if not _display.has_extension("XTEST"):
            raise RuntimeError("the X server has no XTEST extension, so input cannot be sent")
    return _display


def _root():
    return _dpy().screen().root


def set_dpi_aware():
    """X reports real pixels, so there is nothing to opt out of here."""


# --- Window lookup ---------------------------------------------------------------

def _window_name(win):
    d = _dpy()
    for atom in (d.get_atom("_NET_WM_NAME"), Xatom.WM_NAME):
        try:
            prop = win.get_full_property(atom, X.AnyPropertyType)
        except error.XError:
            continue
        if prop is None or not prop.value:
            continue
        value = prop.value
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).decode("utf-8", "replace").rstrip("\x00")
        return str(value)
    return ""


def _client_list():
    """Top-level windows, the closest thing X has to the list of HWNDs."""
    d, root = _dpy(), _root()
    try:
        prop = root.get_full_property(d.get_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
    except error.XError:
        prop = None
    if prop is not None and prop.value:
        return [d.create_resource_object("window", wid) for wid in prop.value]
    return _walk(root, depth=3)   # no EWMH: look through the tree instead


def _walk(win, depth):
    out = []
    if depth < 0:
        return out
    try:
        children = win.query_tree().children
    except error.XError:
        return out
    for child in children:
        out.append(child)
        out.extend(_walk(child, depth - 1))
    return out


def find_window(title):
    """The window whose name is `title`, preferring an exact match the way
    FindWindowW does, then any window with the title inside its name."""
    global _game_window
    exact, partial = [], []
    for win in _client_list():
        name = _window_name(win)
        if name == title:
            exact.append(win)
        elif name and title in name:
            partial.append(win)
    found = exact or partial
    if not found:
        raise RuntimeError(f"Window '{title}' not found - is the game running?")
    if len(found) > 1:
        log.info("%d windows match '%s'; using the first", len(found), title)
    _game_window = found[0]
    return _game_window


def window_exists(win):
    try:
        win.get_attributes()
    except error.XError:       # BadWindow once the game has gone
        return False
    return True


def _active_window_id():
    d, root = _dpy(), _root()
    try:
        prop = root.get_full_property(d.get_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
    except error.XError:
        prop = None
    if prop is not None and prop.value:
        return prop.value[0]
    try:                      # no EWMH: the focused window, which may be a child
        focus = d.get_input_focus().focus
    except error.XError:
        return None
    return getattr(focus, "id", None)


def _toplevel_id(wid):
    """Walk up from a window id until just below the root, so a focused child
    window still identifies the top-level window it belongs to."""
    d, root = _dpy(), _root()
    win = d.create_resource_object("window", wid)
    for _ in range(8):
        try:
            tree = win.query_tree()
        except error.XError:
            return wid
        parent = tree.parent          # a bare 0 once we are at the root
        if not parent or getattr(parent, "id", 0) == root.id:
            return win.id
        win = parent
    return win.id


def is_foreground(win):
    active = _active_window_id()
    if active is None or active == X.NONE:
        return False
    return active == win.id or _toplevel_id(active) == win.id


def foreground_title():
    active = _active_window_id()
    if active is None or active == X.NONE:
        return ""
    return _window_name(_dpy().create_resource_object("window", active))


def _is_minimized(win):
    d = _dpy()
    try:
        prop = win.get_full_property(d.get_atom("WM_STATE"), X.AnyPropertyType)
    except error.XError:
        return False
    return bool(prop and prop.value and prop.value[0] == 3)   # 3 = IconicState


def client_rect(win):
    """The window's drawable area in screen coordinates. X has no separate client
    rect: the window a window manager lists is already the client, with the title
    bar living in a frame around it."""
    if _is_minimized(win):
        raise RuntimeError("Game window is minimized")
    try:
        geom = win.get_geometry()
        origin = _root().translate_coords(win, 0, 0)   # window (0, 0) in root coordinates
    except error.XError as e:
        raise RuntimeError(f"cannot read the game window geometry: {e}") from None
    if geom.width <= 0 or geom.height <= 0:
        raise RuntimeError("Game window has an empty client area")
    return origin.x, origin.y, geom.width, geom.height


# --- Capture ---------------------------------------------------------------------

def _mss():
    global _screenshotter
    if _screenshotter is None:
        try:
            import mss
        except ImportError:
            raise RuntimeError("mss is not installed - pip install -r requirements.txt") from None
        _screenshotter = mss.mss()
    return _screenshotter


def _grab_root(rect):
    x, y, w, h = rect
    shot = _mss().grab({"left": x, "top": y, "width": w, "height": h})
    buf = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return np.ascontiguousarray(buf[:, :, :3])   # BGRA -> BGR


def _grab_window(rect):
    """Read the game window's own pixels. Works where the root window is empty
    (XWayland), at the cost of pushing the whole image over the X connection."""
    if _game_window is None:
        raise RuntimeError("no game window yet; find_window has not been called")
    x, y, w, h = rect
    origin = _root().translate_coords(_game_window, 0, 0)
    img = _game_window.get_image(x - origin.x, y - origin.y, w, h, X.ZPixmap, 0xFFFFFFFF)
    buf = np.frombuffer(bytes(img.data), dtype=np.uint8)
    if buf.size != w * h * 4:
        raise RuntimeError(
            f"the X server returned {buf.size} bytes for a {w}x{h} window grab, expected "
            f"{w * h * 4}; the display is probably not 32-bit colour")
    return np.ascontiguousarray(buf.reshape(h, w, 4)[:, :, :3])


def grab(rect):
    """A screen rect as a (h, w, 3) BGR array."""
    return _grab_window(rect) if CAPTURE == "window" else _grab_root(rect)


# --- Input -----------------------------------------------------------------------

def _keycode(key):
    if key not in _keycodes:
        code = _dpy().keysym_to_keycode(KEYSYMS[key])
        if not code:
            raise RuntimeError(f"the keyboard layout has no key for '{key}'")
        _keycodes[key] = code
    return _keycodes[key]


def send_mouse_rel(dx, dy):
    d = _dpy()
    d.xtest_fake_input(X.MotionNotify, 1, x=int(dx), y=int(dy))   # detail 1 = relative
    d.flush()


def send_mouse_button(button, down):
    d = _dpy()
    d.xtest_fake_input(X.ButtonPress if down else X.ButtonRelease, BUTTONS[button])
    d.sync()


def send_key(key, down):
    d = _dpy()
    d.xtest_fake_input(X.KeyPress if down else X.KeyRelease, _keycode(key))
    d.sync()


def get_position():
    p = _root().query_pointer()
    return p.root_x, p.root_y


def virtual_screen_bounds():
    """The X screen spans every monitor, so its geometry is the virtual desktop."""
    geom = _root().get_geometry()
    return 0, 0, geom.width, geom.height


class _MouseSettings:
    """X accelerates XTEST motion as well, so one relative count is not one pixel.
    Turns acceleration off on the XTEST pointer while the tool runs, the same idea
    as the Windows guard, and puts it back on exit.

    This needs the xinput command. Without it the tool still works: move_to()
    measures how far the cursor actually went and corrects the counts-to-pixels
    curve as it goes (movement/pointer_gain.py)."""

    def __init__(self):
        self._saved = None

    def _device_id(self):
        out = self._xinput("list", "--id-only", XTEST_POINTER)
        return out.strip().splitlines()[0].strip() if out else None

    @staticmethod
    def _xinput(*args):
        if not shutil.which("xinput"):
            return None
        try:
            return subprocess.run(("xinput",) + args, capture_output=True, text=True,
                                  timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None

    def _read_profile(self, dev):
        for line in (self._xinput("list-props", dev) or "").splitlines():
            if line.strip().startswith(ACCEL_PROFILE):
                return line.rsplit(":", 1)[1].strip()
        return None

    def apply(self):
        if self._saved is not None:
            return
        dev = self._device_id()
        if dev is None:
            log.info("xinput not available; leaving pointer acceleration alone "
                     "(the movement code measures and corrects for it)")
            self._saved = ()          # remember that we tried, so we only log once
            return
        current = self._read_profile(dev)
        if current is None:
            log.info("the XTEST pointer has no '%s' property; leaving acceleration alone",
                     ACCEL_PROFILE)
            self._saved = ()
            return
        self._xinput("set-prop", dev, ACCEL_PROFILE, "-1")
        self._saved = (dev, current)
        log.debug("pointer acceleration off on device %s (was %s)", dev, current)

    def restore(self):
        if not self._saved:
            self._saved = None
            return
        dev, previous = self._saved
        self._saved = None
        self._xinput("set-prop", dev, ACCEL_PROFILE, previous)
        log.debug("pointer acceleration on device %s restored to %s", dev, previous)


mouse_settings = _MouseSettings()
atexit.register(mouse_settings.restore)
