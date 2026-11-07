"""Shared desktop styling, dialogs, and background job presentation."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import font, messagebox, ttk

from .jobs import Cancelled, Job
from .model import SceneVoiceError

BG = "#101622"
PANEL = "#172132"
FIELD = "#111a2a"
EDGE = "#324158"
INK = "#eef4ff"
MUTED = "#aebed5"
ACCENT = "#79e3cd"
ACCENT_HOVER = "#a3f2e2"
ACCENT_INK = "#102b2c"
BLUE = "#a9c4ff"
WARN = "#ffcd91"

VISUAL_FILTER = [
    ("Images and videos", "*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.mts *.m2ts *.wmv *.mpg *.mpeg *.3gp "
                          "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif *.avif"),
    ("All files", "*"),
]
IMAGE_FILTER = [("Still images", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif *.avif"), ("All files", "*")]
AUDIO_FILTER = [
    ("Audio files", "*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.aiff *.aif *.wma *.webm *.mp4"),
    ("All files", "*"),
]


def theme(root: tk.Tk) -> None:
    root.configure(bg=BG)
    root.option_add("*tearOff", False)
    default = font.nametofont("TkDefaultFont")
    default.configure(size=10)
    family = default.actual("family")
    root.scenevoice_fonts = [
        font.Font(root=root, name="SceneVoiceTitle", family=family, size=23, weight="bold"),
        font.Font(root=root, name="SceneVoiceHeading", family=family, size=15, weight="bold"),
        font.Font(root=root, name="SceneVoiceLabel", family=family, size=10, weight="bold"),
        font.Font(root=root, name="SceneVoiceScript", family=family, size=12),
    ]
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=INK, font="TkDefaultFont", bordercolor=EDGE,
                    lightcolor=EDGE, darkcolor=EDGE, troughcolor=FIELD, selectbackground="#294d68",
                    selectforeground=INK, fieldbackground=FIELD, focuscolor=ACCENT)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Title.TLabel", font="SceneVoiceTitle")
    style.configure("Heading.TLabel", font="SceneVoiceHeading")
    style.configure("Section.TLabel", font="SceneVoiceLabel", foreground=ACCENT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Panel.TLabel", background=PANEL)
    style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("PanelSection.TLabel", background=PANEL, foreground=ACCENT, font="SceneVoiceLabel")
    style.configure("Warn.TLabel", foreground=WARN)
    style.configure("TButton", padding=(10, 6), background=PANEL, borderwidth=1)
    style.map("TButton", background=[("pressed", "#263952"), ("active", "#233149")],
              foreground=[("disabled", "#748298")])
    style.configure("Accent.TButton", background=ACCENT, foreground=ACCENT_INK, font="SceneVoiceLabel")
    style.map("Accent.TButton", background=[("disabled", "#354652"), ("pressed", "#62bbaa"), ("active", ACCENT_HOVER)],
              foreground=[("disabled", MUTED)])
    style.configure("TEntry", padding=6, insertcolor=INK)
    style.map("TEntry", fieldbackground=[("readonly", FIELD), ("disabled", FIELD)], foreground=[("disabled", MUTED)])
    style.configure("TCombobox", padding=5, arrowcolor=ACCENT, arrowsize=13, insertcolor=INK)
    style.map("TCombobox", fieldbackground=[("readonly", FIELD), ("disabled", FIELD)],
              foreground=[("disabled", MUTED)], selectforeground=[("readonly", INK)],
              selectbackground=[("readonly", FIELD)])
    style.configure("TSpinbox", padding=5, arrowcolor=ACCENT, insertcolor=INK)
    style.configure("Treeview", background=FIELD, fieldbackground=FIELD, rowheight=32)
    style.configure("Treeview.Heading", background=PANEL, padding=(8, 6), font="SceneVoiceLabel")
    style.map("Treeview", background=[("selected", "#224662")], foreground=[("selected", INK)])
    style.configure("Horizontal.TProgressbar", background=ACCENT, bordercolor=EDGE, lightcolor=ACCENT, darkcolor=ACCENT)
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", "#294d68")
    root.option_add("*TCombobox*Listbox.selectForeground", INK)


def button(parent, label, command, *, accent=False) -> ttk.Button:
    return ttk.Button(parent, text=label, width=0, command=command,
                      style="Accent.TButton" if accent else "TButton")


def text_area(parent, *, height=7, font_name="SceneVoiceScript") -> tuple[ttk.Frame, tk.Text]:
    frame = ttk.Frame(parent)
    text = tk.Text(
        frame, wrap="word", height=height, width=1, bg=FIELD, fg=INK, insertbackground=INK,
        font=font_name, padx=12, pady=10, spacing2=3, spacing3=6, undo=True,
        borderwidth=0, highlightthickness=1, highlightbackground=EDGE, highlightcolor=ACCENT,
        selectbackground="#294d68", selectforeground=INK,
    )
    scrollbar = ttk.Scrollbar(frame, command=text.yview, orient="vertical")
    text.configure(yscrollcommand=scrollbar.set)
    text.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    return frame, text


def dialog(parent, title: str, *, minimum=(440, 160)) -> tk.Toplevel:
    previous = parent.grab_current()
    top = tk.Toplevel(parent)
    top.title(title)
    top.configure(bg=BG)
    top.transient(parent)
    top.minsize(*minimum)
    top.grab_set()

    def restore(event):
        if event.widget == top and previous and previous.winfo_exists():
            previous.grab_set()

    top.bind("<Destroy>", restore, add="+")
    return top


def open_path(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise SceneVoiceError(f"Could not open {path.name}. {exc}") from exc


def show_error(parent, title: str, error) -> None:
    message = str(error) or type(error).__name__
    messagebox.showerror(title, message, parent=parent)


class TaskDialog:
    def __init__(self, parent, title, action, success, *, finished=None):
        self.parent = parent
        self.window = dialog(parent, title, minimum=(500, 180))
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.events = queue.Queue()
        self.job = Job(lambda message, progress: self.events.put(("progress", (message, progress))))
        self.success = success
        self.finished = finished
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=title, style="Heading.TLabel").pack(anchor="w")
        self.message = ttk.Label(body, text="Starting...", wraplength=475, style="Muted.TLabel")
        self.message.pack(anchor="w", pady=(10, 8))
        self.progress = ttk.Progressbar(body, mode="indeterminate", maximum=100)
        self.progress.pack(fill="x")
        self.progress.start(12)
        self.cancel_button = button(body, "Cancel", self.cancel)
        self.cancel_button.pack(anchor="e", pady=(14, 0))
        self.window.bind("<Escape>", lambda _event: self.cancel())

        def worker():
            try:
                self.events.put(("done", action(self.job)))
            except Cancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()
        self.window.after(55, self.poll)

    def cancel(self):
        self.job.cancel()
        self.message.configure(text="Cancelling...")
        self.cancel_button.state(["disabled"])

    def poll(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                self.window.after(55, self.poll)
                return
            if kind == "progress":
                if self.job.cancelled.is_set():
                    continue
                message, amount = payload
                self.message.configure(text=message)
                if amount is not None:
                    if self.progress.cget("mode") != "determinate":
                        self.progress.stop()
                        self.progress.configure(mode="determinate")
                    self.progress.configure(value=amount * 100)
                elif self.progress.cget("mode") != "indeterminate":
                    self.progress.configure(mode="indeterminate")
                    self.progress.start(12)
                continue
            self.window.destroy()
            if kind == "done":
                self.success(payload)
            elif kind == "error":
                show_error(self.parent, "Task could not finish", payload)
            if self.finished:
                self.finished()
            return
