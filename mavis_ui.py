"""Shared window chrome for Mavis's Tkinter features.

Matches the look of Mavis's own panels: borderless, slightly translucent,
dark, with a drawn title bar you drag by. Mavis does this with
overrideredirect(True) plus -alpha, and so does this.

This file is COPIED into each feature rather than imported from Mavis. Every
feature is its own public repository and has to run on a machine that has
only Python and this folder - it cannot assume Mavis is importable, or even
installed. A hundred duplicated lines is the price of that, and it is worth
paying.

Standard library only. No pip install, on purpose.
"""
import json
import os
import tkinter as tk
from tkinter import font as tkfont

# The Appstore palette, so a Tk feature and an HTML one look related.
INK      = "#0a0813"   # window background
PANEL    = "#16132a"   # raised surfaces
PANEL_HI = "#1e1a38"   # hover
GLOW     = "#4fd8ff"   # accent
DIM      = "#9b96bd"   # secondary text
TEXT     = "#e8e6f5"   # primary text
GOOD     = "#8fe3a1"
WARN     = "#ffcf6b"
BAD      = "#ff7a6b"
LINE     = "#2a2545"   # hairlines

DEFAULT_ALPHA = 0.92


def state_path(feature, filename="state.json"):
    """Where a feature keeps its own small state.

    Under %LOCALAPPDATA%\\Mavis\\feature-state, never beside the script:
    the feature folder is replaced wholesale when the app is reinstalled, and
    on a locked-down machine it may not be writable at all.
    """
    base = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "Mavis", "feature-state", feature)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, filename)


def load_state(feature, default=None):
    try:
        with open(state_path(feature), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        # A corrupt or absent state file must never stop the app opening.
        return {} if default is None else default


def save_state(feature, data):
    try:
        with open(state_path(feature), "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except Exception:
        pass


class MavisWindow(tk.Tk):
    """A borderless, translucent, draggable window in Mavis's house style.

    Because overrideredirect(True) removes the real title bar, everything it
    used to provide has to be rebuilt: dragging, closing, and remembering
    where the window was. Windows also stops showing the window in the
    taskbar, so if a feature loses its close button the user has no way back
    to it - which is why Escape always closes and the close control is drawn
    before anything else.
    """

    def __init__(self, feature, title, width=520, height=420,
                 alpha=DEFAULT_ALPHA, topmost=False, resizable=True):
        super().__init__()
        self.feature = feature
        self._alpha = alpha

        self.title(title)
        self.configure(bg=INK)
        self.overrideredirect(True)
        self.attributes("-alpha", alpha)
        self.attributes("-topmost", topmost)

        saved = load_state(feature, {}).get("window", {})
        x = saved.get("x")
        y = saved.get("y")
        width = saved.get("w", width)
        height = saved.get("h", height)
        if x is None or not self._on_a_screen(x, y, width, height):
            # Centre on first run, and also whenever the remembered position
            # is off-screen - a monitor that has been unplugged since would
            # otherwise put the window somewhere unreachable, with no title
            # bar to drag it back by.
            x = (self.winfo_screenwidth() - width) // 2
            y = (self.winfo_screenheight() - height) // 3
        self.geometry("%dx%d+%d+%d" % (width, height, x, y))

        # A one-pixel border, since there is no frame to separate the window
        # from whatever is behind it.
        self.outer = tk.Frame(self, bg=LINE)
        self.outer.pack(fill="both", expand=True)
        self.body = tk.Frame(self.outer, bg=INK)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_titlebar(title)

        self.content = tk.Frame(self.body, bg=INK)
        self.content.pack(fill="both", expand=True)

        if resizable:
            self._build_grip()

        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Control-Up>", lambda e: self.nudge_alpha(+0.05))
        self.bind("<Control-Down>", lambda e: self.nudge_alpha(-0.05))
        self.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------ chrome
    def _build_titlebar(self, title):
        bar = tk.Frame(self.body, bg=PANEL, height=34)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.titlebar = bar

        tk.Frame(bar, bg=GLOW, width=3, height=16).pack(side="left", padx=(10, 8))

        label = tk.Label(bar, text=title, bg=PANEL, fg=TEXT,
                         font=("Segoe UI", 10, "bold"))
        label.pack(side="left")

        self.hint = tk.Label(bar, text="", bg=PANEL, fg=DIM, font=("Segoe UI", 8))
        self.hint.pack(side="left", padx=10)

        close = tk.Label(bar, text="✕", bg=PANEL, fg=DIM,
                         font=("Segoe UI", 11), cursor="hand2", padx=12)
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        close.bind("<Enter>", lambda e: close.configure(fg=BAD))
        close.bind("<Leave>", lambda e: close.configure(fg=DIM))

        pin = tk.Label(bar, text="⌖", bg=PANEL, fg=DIM,
                       font=("Segoe UI", 11), cursor="hand2", padx=8)
        pin.pack(side="right")
        pin.bind("<Button-1>", lambda e: self.toggle_topmost(pin))
        self._pin = pin
        self._sync_pin()

        for widget in (bar, label, self.hint):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)

    def _build_grip(self):
        grip = tk.Frame(self.body, bg=INK, height=14, cursor="bottom_right_corner")
        grip.pack(fill="x", side="bottom")
        mark = tk.Label(grip, text="◢", bg=INK, fg=LINE, font=("Segoe UI", 8))
        mark.pack(side="right", padx=4)
        for widget in (grip, mark):
            widget.bind("<Button-1>", self._start_resize)
            widget.bind("<B1-Motion>", self._resize)

    # ------------------------------------------------------------------ drag
    def _start_drag(self, event):
        self._dx = event.x
        self._dy = event.y

    def _drag(self, event):
        # Same arithmetic Mavis's own panels use: the pointer offset within
        # the widget stays constant, so the window follows without jumping.
        self.geometry("+%d+%d" % (self.winfo_x() + event.x - self._dx,
                                  self.winfo_y() + event.y - self._dy))

    def _start_resize(self, event):
        self._rx = event.x_root
        self._ry = event.y_root
        self._rw = self.winfo_width()
        self._rh = self.winfo_height()

    def _resize(self, event):
        width = max(320, self._rw + event.x_root - self._rx)
        height = max(220, self._rh + event.y_root - self._ry)
        self.geometry("%dx%d" % (width, height))

    # ------------------------------------------------------------------ misc
    def _on_a_screen(self, x, y, w, h):
        """Is this rectangle at least partly on the virtual desktop?

        winfo_screenwidth reports the PRIMARY monitor only, so a window
        legitimately parked on a second screen would look off-screen. The
        check is deliberately generous: it only catches positions that are
        nowhere near anything.
        """
        if x is None or y is None:
            return False
        return (-w + 80 < x < self.winfo_screenwidth() + w
                and -40 < y < self.winfo_screenheight() - 40)

    def nudge_alpha(self, delta):
        self._alpha = max(0.35, min(1.0, self._alpha + delta))
        self.attributes("-alpha", self._alpha)
        self.flash("transparency %d%%" % round(self._alpha * 100))

    def toggle_topmost(self, widget=None):
        new = not bool(self.attributes("-topmost"))
        self.attributes("-topmost", new)
        self._sync_pin()
        self.flash("always on top" if new else "not on top")

    def _sync_pin(self):
        on = bool(self.attributes("-topmost"))
        self._pin.configure(fg=GLOW if on else DIM)

    def flash(self, message, ms=1400):
        """A transient note in the title bar. Borderless windows have nowhere
        else to put one, and a dialog for 'transparency 85%' is absurd."""
        self.hint.configure(text=message)
        if getattr(self, "_flash_job", None):
            self.after_cancel(self._flash_job)
        self._flash_job = self.after(ms, lambda: self.hint.configure(text=""))

    def remember(self, extra=None):
        state = load_state(self.feature, {})
        state["window"] = {"x": self.winfo_x(), "y": self.winfo_y(),
                           "w": self.winfo_width(), "h": self.winfo_height()}
        if extra:
            state.update(extra)
        save_state(self.feature, state)

    def close(self):
        self.remember(self.on_close() or None)
        self.destroy()

    def on_close(self):
        """Override to contribute extra state to save. Return a dict or None."""
        return None


# ---------------------------------------------------------------- widgets
def heading(parent, text):
    return tk.Label(parent, text=text, bg=INK, fg=DIM,
                    font=("Segoe UI", 8, "bold"), anchor="w")


def button(parent, text, command, accent=False):
    """A flat button. tk.Button on Windows insists on a raised 3D border that
    looks wrong against a translucent dark panel, so this is a Label."""
    widget = tk.Label(parent, text=text, cursor="hand2", padx=12, pady=6,
                      font=("Segoe UI", 9, "bold" if accent else "normal"),
                      bg=GLOW if accent else PANEL,
                      fg=INK if accent else GLOW)
    widget.bind("<Button-1>", lambda e: command())
    widget.bind("<Enter>", lambda e: widget.configure(
        bg="#7fe4ff" if accent else PANEL_HI))
    widget.bind("<Leave>", lambda e: widget.configure(
        bg=GLOW if accent else PANEL))
    return widget


def mono(size=9):
    return tkfont.Font(family="Consolas", size=size)
