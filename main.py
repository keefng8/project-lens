"""Project Lens - a heads-up display for everything you are building.

Pick a project, or have Mavis name one, and get the whole picture at a
glance: what it is written in, what state git is in, what it depends on,
what you left a TODO about, and what you touched most recently.

Being spoken to
---------------
If Mavis passes a project name - as MAVIS_ARG_PROJECT in the environment, or
as the first argument - this opens straight onto that project. Without one it
opens on whatever you worked on last, which is almost always the right guess.

Scanning happens on a worker thread. A Tk window that freezes for two seconds
while it walks a directory looks broken, and the fix is not to scan less, it
is to stop doing it on the thread that draws.

Standard library only.
"""
import os
import queue
import sys
import threading
import time
import tkinter as tk

import mavis_ui as ui
import projects

FEATURE = "project-lens"

DEFAULT_ROOTS = [
    # Forward slashes deliberately: a raw "G:\\" is a doubled separator that
    # propagates into every path found beneath it.
    "G:/",
    "D:/",
    os.path.join(os.path.expanduser("~"), "Documents"),
    os.path.join(os.path.expanduser("~"), "source", "repos"),
    os.path.join(os.path.expanduser("~"), "Projects"),
]

LANGUAGE_COLOURS = {
    "Python": "#4FD8FF", "JavaScript": "#ffcf6b", "TypeScript": "#7aa2ff",
    "HTML": "#ff9f6b", "CSS": "#b79cff", "Markdown": "#9b96bd",
    "JSON": "#8fe3a1", "YAML": "#8fe3a1", "PowerShell": "#6be0ff",
    "C#": "#a4e36b", "Java": "#ff7a6b", "Go": "#6bd5e0", "Rust": "#e0956b",
    "Shell": "#8fe3a1", "SQL": "#ffb86b", "Vue": "#8fe3a1", "PHP": "#9c8fff",
}
FALLBACK_COLOUR = "#6a648f"


def relative_time(stamp):
    seconds = max(0, time.time() - stamp)
    for size, name in ((60, "second"), (60, "minute"), (24, "hour"),
                       (7, "day"), (4.35, "week"), (12, "month")):
        if seconds < size:
            value = int(seconds)
            return "%d %s%s ago" % (value, name, "" if value == 1 else "s")
        seconds /= size
    return "%d years ago" % int(seconds)


class Lens(ui.MavisWindow):
    def __init__(self, wanted=None):
        super().__init__(FEATURE, "Project Lens", width=1020, height=660,
                         alpha=0.95)
        self.results = queue.Queue()
        self.projects = []
        self.current = None
        self.report = None
        self.wanted = wanted

        state = ui.load_state(FEATURE, {})
        self.roots = state.get("roots", DEFAULT_ROOTS)

        self._build()
        self._discover()
        self.after(80, self._drain)

    # ------------------------------------------------------------------ chrome
    def _build(self):
        body = tk.Frame(self.content, bg=ui.INK)
        body.pack(fill="both", expand=True)

        # --- left: the list ------------------------------------------------
        left = tk.Frame(body, bg=ui.INK, width=270)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        self.search = tk.Entry(left, bg="#07060f", fg=ui.TEXT, relief="flat",
                               insertbackground=ui.GLOW, font=("Segoe UI", 10))
        self.search.pack(fill="x", padx=12, pady=(12, 8), ipady=6)
        self.search.bind("<KeyRelease>", lambda e: self._render_list())

        holder = tk.Frame(left, bg=ui.INK)
        holder.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.canvas = tk.Canvas(holder, bg=ui.INK, highlightthickness=0)
        scroll = tk.Scrollbar(holder, orient="vertical", command=self.canvas.yview,
                              width=8, troughcolor=ui.INK, bg=ui.LINE,
                              relief="flat", borderwidth=0)
        self.listframe = tk.Frame(self.canvas, bg=ui.INK)
        self.listframe.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.listitem = self.canvas.create_window((0, 0), window=self.listframe,
                                                  anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self.listitem, width=e.width))
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>",
                      lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        # A hairline between the panes, in Mavis's accent.
        tk.Frame(body, bg=ui.LINE, width=1).pack(side="left", fill="y")

        # --- right: the detail --------------------------------------------
        right = tk.Frame(body, bg=ui.INK)
        right.pack(side="left", fill="both", expand=True)

        head = tk.Frame(right, bg=ui.INK)
        head.pack(fill="x", padx=18, pady=(14, 0))
        self.title_label = tk.Label(head, text="", bg=ui.INK, fg=ui.TEXT,
                                    font=("Segoe UI", 17, "bold"), anchor="w")
        self.title_label.pack(fill="x")
        self.path_label = tk.Label(head, text="", bg=ui.INK, fg=ui.DIM,
                                   font=("Consolas", 8), anchor="w")
        self.path_label.pack(fill="x")

        self.detail = tk.Frame(right, bg=ui.INK)
        self.detail.pack(fill="both", expand=True, padx=18, pady=10)

        self.busy = tk.Label(right, text="", bg=ui.INK, fg=ui.GLOW,
                             font=("Segoe UI", 9))
        self.busy.pack(side="bottom", anchor="w", padx=18, pady=(0, 8))

    # ------------------------------------------------------------------ scan
    def _discover(self):
        self.busy.configure(text="looking for projects\u2026")

        def work():
            try:
                found = projects.find_projects(self.roots, max_depth=3)
                self.results.put(("projects", found))
            except Exception as error:          # a scan must never kill the app
                self.results.put(("error", str(error)))

        threading.Thread(target=work, daemon=True).start()

    def _analyse(self, entry):
        self.current = entry
        self.report = None
        self.title_label.configure(text=entry["name"])
        self.path_label.configure(text=entry["path"])
        self.busy.configure(text="reading %s\u2026" % entry["name"])
        for child in self.detail.winfo_children():
            child.destroy()
        self._render_list()

        path = entry["path"]

        def work():
            try:
                report = projects.analyse(path)
                report["kinds"] = entry.get("kinds", [])
                self.results.put(("report", report))
            except Exception as error:
                self.results.put(("error", str(error)))

        threading.Thread(target=work, daemon=True).start()

    def _drain(self):
        """Move worker results onto the UI thread.

        Tk is not thread-safe: touching a widget from the worker produces
        crashes that are intermittent and impossible to reproduce. A queue
        polled from the main loop is the boring, correct way.
        """
        try:
            while True:
                kind, payload = self.results.get_nowait()
                if kind == "projects":
                    self.projects = payload
                    self._render_list()
                    self.busy.configure(text="%d projects" % len(payload))
                    self._choose_initial()
                elif kind == "report":
                    self.report = payload
                    self.busy.configure(text="")
                    self._render_detail()
                elif kind == "error":
                    self.busy.configure(text="could not read that: " + payload[:70])
        except queue.Empty:
            pass
        self.after(90, self._drain)

    def _choose_initial(self):
        if not self.projects:
            return
        pick = None
        if self.wanted:
            want = self.wanted.lower().replace(" ", "").replace("-", "")
            for entry in self.projects:
                flat = entry["name"].lower().replace(" ", "").replace("-", "")
                if want == flat or want in flat:
                    pick = entry
                    break
        self._analyse(pick or self.projects[0])

    # ------------------------------------------------------------------ list
    def _render_list(self):
        for child in self.listframe.winfo_children():
            child.destroy()
        query = self.search.get().lower().strip()
        shown = [p for p in self.projects
                 if not query or query in p["name"].lower()
                 or query in p["path"].lower()]

        if not shown:
            tk.Label(self.listframe, text="Nothing found.", bg=ui.INK,
                     fg=ui.LINE, font=("Segoe UI", 9)).pack(pady=20)
            return

        for entry in shown:
            chosen = self.current and entry["path"] == self.current["path"]
            bg = ui.PANEL_HI if chosen else ui.PANEL
            frame = tk.Frame(self.listframe, bg=bg, cursor="hand2")
            frame.pack(fill="x", pady=(0, 4))
            tk.Frame(frame, bg=ui.GLOW if chosen else ui.LINE, width=3).pack(
                side="left", fill="y")
            inner = tk.Frame(frame, bg=bg)
            inner.pack(side="left", fill="both", expand=True, padx=9, pady=6)
            name = tk.Label(inner, text=entry["name"][:28], bg=bg,
                            fg=ui.TEXT if chosen else ui.DIM,
                            font=("Segoe UI", 9, "bold" if chosen else "normal"),
                            anchor="w")
            name.pack(fill="x")
            meta = tk.Label(inner, text=", ".join(entry["kinds"])[:34], bg=bg,
                            fg=ui.DIM, font=("Segoe UI", 7), anchor="w")
            meta.pack(fill="x")
            for widget in (frame, inner, name, meta):
                widget.bind("<Button-1>", lambda e, x=entry: self._analyse(x))

    # ---------------------------------------------------------------- detail
    def _panel(self, parent, title):
        wrap = tk.Frame(parent, bg=ui.INK)
        wrap.pack(fill="x", pady=(0, 12))
        header = tk.Frame(wrap, bg=ui.INK)
        header.pack(fill="x")
        tk.Frame(header, bg=ui.GLOW, width=10, height=2).pack(side="left", pady=(6, 0))
        tk.Label(header, text="  " + title, bg=ui.INK, fg=ui.GLOW,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Frame(wrap, bg=ui.LINE, height=1).pack(fill="x", pady=(3, 7))
        return wrap

    def _stat_row(self, parent, pairs):
        row = tk.Frame(parent, bg=ui.INK)
        row.pack(fill="x")
        for label, value, colour in pairs:
            cell = tk.Frame(row, bg=ui.INK)
            cell.pack(side="left", padx=(0, 26))
            tk.Label(cell, text=value, bg=ui.INK, fg=colour or ui.TEXT,
                     font=("Consolas", 15, "bold"), anchor="w").pack(anchor="w")
            tk.Label(cell, text=label, bg=ui.INK, fg=ui.DIM,
                     font=("Segoe UI", 7), anchor="w").pack(anchor="w")

    def _render_detail(self):
        for child in self.detail.winfo_children():
            child.destroy()
        r = self.report
        if not r:
            return

        # --- overview -----------------------------------------------------
        panel = self._panel(self.detail, "OVERVIEW")
        git = r.get("git")
        self._stat_row(panel, [
            ("files", "{:,}".format(r["files"]), None),
            ("lines of code", "{:,}".format(r["lines"]), ui.GLOW),
            ("on disk", projects.human_bytes(r["bytes"]), None),
            ("todos", str(len(r["todos"])),
             ui.WARN if r["todos"] else ui.DIM),
            ("uncommitted", str(git["dirty"]) if git else "\u2014",
             ui.WARN if git and git["dirty"] else ui.GOOD if git else ui.DIM),
        ])
        if r.get("truncated"):
            tk.Label(panel, bg=ui.INK, fg=ui.WARN, font=("Segoe UI", 8),
                     anchor="w", text="Large project \u2014 counts are from the "
                                      "first few thousand files.").pack(fill="x", pady=(6, 0))
        if r.get("readme"):
            tk.Label(panel, text=r["readme"][:300], bg=ui.INK, fg=ui.DIM,
                     font=("Segoe UI", 9), wraplength=640, justify="left",
                     anchor="w").pack(fill="x", pady=(8, 0))

        # --- languages ----------------------------------------------------
        if r["languages"]:
            panel = self._panel(self.detail, "MADE OF")
            total = sum(r["languages"].values())
            bar = tk.Frame(panel, bg=ui.PANEL, height=10)
            bar.pack(fill="x")
            bar.pack_propagate(False)
            ordered = sorted(r["languages"].items(), key=lambda kv: -kv[1])
            # Running offset rather than list.index(): two languages with the
            # same file count are equal tuples, so index() returns the first
            # of them and stacks both segments in one place.
            offset = 0.0
            for language, count in ordered:
                share = count / total
                if share >= 0.012:
                    segment = tk.Frame(bar, bg=LANGUAGE_COLOURS.get(language, FALLBACK_COLOUR))
                    segment.place(relwidth=share, relheight=1, relx=offset)
                offset += share
            legend = tk.Frame(panel, bg=ui.INK)
            legend.pack(fill="x", pady=(7, 0))
            for language, count in ordered[:7]:
                cell = tk.Frame(legend, bg=ui.INK)
                cell.pack(side="left", padx=(0, 16))
                tk.Frame(cell, bg=LANGUAGE_COLOURS.get(language, FALLBACK_COLOUR),
                         width=8, height=8).pack(side="left", pady=3)
                tk.Label(cell, text="  %s %d" % (language, count), bg=ui.INK,
                         fg=ui.DIM, font=("Segoe UI", 8)).pack(side="left")

        # --- git ----------------------------------------------------------
        if git:
            panel = self._panel(self.detail, "GIT")
            line = tk.Frame(panel, bg=ui.INK)
            line.pack(fill="x")
            tk.Label(line, text=git["branch"], bg=ui.INK, fg=ui.GLOW,
                     font=("Consolas", 11, "bold")).pack(side="left")
            state = ("clean" if not git["dirty"]
                     else "%d uncommitted change%s" % (git["dirty"],
                                                       "" if git["dirty"] == 1 else "s"))
            tk.Label(line, text="   " + state, bg=ui.INK,
                     fg=ui.GOOD if not git["dirty"] else ui.WARN,
                     font=("Segoe UI", 9)).pack(side="left")
            if git.get("total_commits"):
                tk.Label(line, text="   %s commits" % "{:,}".format(git["total_commits"]),
                         bg=ui.INK, fg=ui.DIM, font=("Segoe UI", 9)).pack(side="left")
            if git.get("remote"):
                tk.Label(panel, text=git["remote"], bg=ui.INK, fg=ui.DIM,
                         font=("Consolas", 8), anchor="w").pack(fill="x", pady=(3, 0))

            if git.get("activity"):
                self._sparkline(panel, git["activity"])

            for commit in git["commits"][:5]:
                row = tk.Frame(panel, bg=ui.INK)
                row.pack(fill="x", pady=1)
                tk.Label(row, text=commit["sha"], bg=ui.INK, fg=ui.GLOW,
                         font=("Consolas", 8), width=9, anchor="w").pack(side="left")
                tk.Label(row, text=commit["subject"][:62], bg=ui.INK, fg=ui.TEXT,
                         font=("Segoe UI", 8), anchor="w").pack(side="left")
                tk.Label(row, text="  " + commit["when"], bg=ui.INK, fg=ui.DIM,
                         font=("Segoe UI", 8)).pack(side="right")

        # --- dependencies -------------------------------------------------
        if r["dependencies"]:
            panel = self._panel(self.detail, "DEPENDS ON")
            for source, names in r["dependencies"].items():
                row = tk.Frame(panel, bg=ui.INK)
                row.pack(fill="x", pady=1)
                tk.Label(row, text="%s (%d)" % (source, len(names)), bg=ui.INK,
                         fg=ui.GLOW, font=("Segoe UI", 8, "bold"),
                         width=14, anchor="w").pack(side="left")
                tk.Label(row, text=", ".join(names[:10]) +
                         (" \u2026" if len(names) > 10 else ""),
                         bg=ui.INK, fg=ui.DIM, font=("Consolas", 8),
                         anchor="w", wraplength=520, justify="left").pack(side="left")

        # --- todos --------------------------------------------------------
        if r["todos"]:
            panel = self._panel(self.detail, "LEFT TO DO")
            for todo in r["todos"][:6]:
                row = tk.Frame(panel, bg=ui.INK)
                row.pack(fill="x", pady=1)
                tk.Label(row, text=todo["kind"], bg=ui.INK, fg=ui.WARN,
                         font=("Consolas", 8, "bold"), width=7,
                         anchor="w").pack(side="left")
                tk.Label(row, text=todo["text"][:66] or "(no note)", bg=ui.INK,
                         fg=ui.TEXT, font=("Segoe UI", 8), anchor="w").pack(side="left")
                tk.Label(row, text="%s:%d" % (todo["file"][-34:], todo["line"]),
                         bg=ui.INK, fg=ui.DIM, font=("Consolas", 7)).pack(side="right")
            if len(r["todos"]) > 6:
                tk.Label(panel, text="and %d more" % (len(r["todos"]) - 6),
                         bg=ui.INK, fg=ui.DIM, font=("Segoe UI", 8),
                         anchor="w").pack(fill="x", pady=(3, 0))

        # --- recent -------------------------------------------------------
        if r["recent"]:
            panel = self._panel(self.detail, "TOUCHED THIS WEEK")
            for name, stamp in r["recent"][:6]:
                row = tk.Frame(panel, bg=ui.INK)
                row.pack(fill="x", pady=1)
                tk.Label(row, text=name[-58:], bg=ui.INK, fg=ui.TEXT,
                         font=("Consolas", 8), anchor="w").pack(side="left")
                tk.Label(row, text=relative_time(stamp), bg=ui.INK, fg=ui.DIM,
                         font=("Segoe UI", 8)).pack(side="right")

    def _sparkline(self, parent, activity):
        """Commits per day for the last fortnight."""
        import datetime
        today = datetime.date.today()
        days = [(today - datetime.timedelta(days=n)).isoformat() for n in range(13, -1, -1)]
        values = [activity.get(day, 0) for day in days]
        peak = max(values) or 1

        canvas = tk.Canvas(parent, height=26, bg=ui.INK, highlightthickness=0)
        canvas.pack(fill="x", pady=(8, 6))

        def draw(event=None):
            canvas.delete("all")
            width = canvas.winfo_width() or 400
            slot = width / len(values)
            for index, value in enumerate(values):
                height = (value / peak) * 20
                x = index * slot
                canvas.create_rectangle(
                    x + 1, 24 - height, x + slot - 2, 24,
                    fill=ui.GLOW if value else ui.LINE, outline="")
            canvas.create_text(width - 2, 6, anchor="e",
                               text="%d commits in 14 days" % sum(values),
                               fill=ui.DIM, font=("Segoe UI", 7))
        canvas.bind("<Configure>", draw)
        draw()

    def on_close(self):
        return {"roots": self.roots}


def wanted_project():
    """What Mavis asked for, if anything.

    Read from the environment first: an environment variable survives spaces
    and quoting, which a command line does not, and a project called
    "My Trader Bot" would otherwise arrive as three arguments.
    """
    value = os.environ.get("MAVIS_ARG_PROJECT") or os.environ.get("MAVIS_UTTERANCE")
    if value:
        return value.strip()
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:]).strip()
    return None


if __name__ == "__main__":
    Lens(wanted_project()).mainloop()
