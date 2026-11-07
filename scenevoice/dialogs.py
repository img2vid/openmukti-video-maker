"""Voice selection and export settings windows."""

from __future__ import annotations

import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk
from urllib.parse import quote

from .model import (
    FIT_MODES,
    FRAME_RATES,
    PROFILES,
    QUALITIES,
    RATIOS,
    RESOLUTIONS,
    ExportSettings,
)
from .ui import TaskDialog, button, dialog
from .voices import REPOSITORY


class VoiceLibrary:
    def __init__(self, parent, store, on_change, on_use):
        self.store = store
        self.on_change = on_change
        self.on_use = on_use
        self.catalog = store.cached_catalog()
        self.voices = store.installed()
        self.window = dialog(parent, "Piper Voice Library", minimum=(760, 430))
        self.window.geometry("880x530")
        self.query = tk.StringVar(self.window)
        self.installed_only = tk.BooleanVar(self.window, False)
        self.summary = tk.StringVar(self.window)
        body = ttk.Frame(self.window, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, text="Piper Voice Library", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            body, text="Install a model once, then use it offline. Each scene can use a different voice.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 12))

        controls = ttk.Frame(body)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(controls, text="Search").pack(side="left", padx=(0, 8))
        ttk.Entry(controls, textvariable=self.query, width=24).pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(controls, text="Installed only", variable=self.installed_only,
                        command=self.populate).pack(side="left", padx=8)
        button(controls, "Refresh online catalog", self.refresh).pack(side="left", padx=(0, 6))
        button(controls, "Import local voice...", self.import_voice).pack(side="left")

        listing = ttk.Frame(body)
        listing.grid(row=3, column=0, sticky="nsew")
        self.tree = ttk.Treeview(listing, columns=("voice", "language", "state"), show="headings",
                                 selectmode="browse", height=6)
        for name, label, width in (("voice", "Voice", 285), ("language", "Language", 185), ("state", "Status", 100)):
            self.tree.heading(name, text=label, anchor="w")
            self.tree.column(name, width=width, anchor="w", stretch=name != "state")
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        ttk.Label(body, textvariable=self.summary, style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=9)
        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, sticky="ew")
        self.card = button(actions, "Model card", self.model_card)
        self.card.pack(side="left")
        self.download_button = button(actions, "Download selected", self.download, accent=True)
        self.download_button.pack(side="left", padx=6)
        self.use_button = button(actions, "Use in this scene", self.use)
        self.use_button.pack(side="right", padx=(6, 0))
        button(actions, "Close", self.window.destroy).pack(side="right")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_buttons())
        self.query.trace_add("write", lambda *_: self.populate())
        self.populate()

    def selected(self) -> str:
        return next(iter(self.tree.selection()), "")

    def populate(self, select=""):
        select = select or self.selected()
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().strip().casefold()
        keys = self.voices.keys() if self.installed_only.get() else self.voices.keys() | self.catalog.keys()
        for key in sorted(keys):
            item = self.catalog.get(key, {})
            voice = self.voices.get(key)
            locale = item.get("language", {})
            language = locale.get("name_english", voice.language if voice else "")
            native = locale.get("name_native", "")
            if query and query not in f"{key} {language} {native}".casefold():
                continue
            size = sum(info.get("size_bytes", 0) for info in item.get("files", {}).values())
            status = "Installed" if voice else f"{size / 1048576:.0f} MiB"
            self.tree.insert("", "end", iid=key, values=(key, language, status))
        if select and self.tree.exists(select):
            self.tree.selection_set(select)
            self.tree.see(select)
        if self.catalog:
            self.summary.set(f"{len(self.voices)} installed  ·  {len(self.catalog)} in the online catalog")
        else:
            self.summary.set("Refresh the online catalog to browse voices, or import a local .onnx and .onnx.json pair.")
        self.update_buttons()

    def update_buttons(self):
        key = self.selected()
        self.download_button.state(["!disabled"] if key in self.catalog and key not in self.voices else ["disabled"])
        self.card.state(["!disabled"] if key in self.catalog else ["disabled"])
        self.use_button.state(["!disabled"] if self.on_use and key in self.voices else ["disabled"])

    def refresh(self):
        def ready(catalog):
            self.catalog = catalog
            self.populate()

        TaskDialog(self.window, "Refresh voice catalog", self.store.refresh, ready)

    def download(self):
        key = self.selected()
        if key in self.catalog:
            TaskDialog(self.window, "Download Piper voice",
                       lambda job: self.store.download(job, key, self.catalog[key]), self.installed)

    def import_voice(self):
        path = filedialog.askopenfilename(parent=self.window, title="Choose a Piper ONNX model",
                                         filetypes=[("Piper model", "*.onnx"), ("All files", "*")])
        if path:
            TaskDialog(self.window, "Import Piper voice", lambda job: self.store.import_voice(job, Path(path)),
                       self.installed)

    def installed(self, key):
        self.voices = self.store.installed()
        self.on_change()
        self.query.set("")
        self.populate(key)

    def model_card(self):
        key = self.selected()
        remote = next((path for path in self.catalog.get(key, {}).get("files", {}) if path.endswith("/MODEL_CARD")), "")
        url = f"{REPOSITORY}/blob/main/{quote(remote, safe='/')}" if remote else REPOSITORY
        webbrowser.open(url)

    def use(self):
        key = self.selected()
        if self.on_use and key in self.voices:
            self.on_use(key)
            self.window.destroy()


class ExportWindow:
    def __init__(self, parent, settings, on_export):
        self.window = dialog(parent, "Export Video", minimum=(535, 425))
        self.window.geometry("575x425")
        self.on_export = on_export
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Export video", style="Heading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.summary = tk.StringVar(self.window)
        ttk.Label(body, textvariable=self.summary, style="Muted.TLabel").grid(row=1, column=0, columnspan=2,
                                                                             sticky="w", pady=(5, 10))
        profiles = {key: value[0] for key, value in PROFILES.items()}
        choices = [
            ("ratio", "Aspect ratio", {value: value for value in RATIOS}),
            ("resolution", "Resolution", RESOLUTIONS),
            ("fps", "Frame rate", {fps: str(fps) for fps in FRAME_RATES}),
            ("fit", "Framing", FIT_MODES),
            ("profile", "File format", profiles),
            ("quality", "Quality", QUALITIES),
        ]
        self.fields = {}
        for row, (attribute, label, options) in enumerate(choices, 2):
            value = tk.StringVar(self.window, options[getattr(settings, attribute)])
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 12))
            combo = ttk.Combobox(body, textvariable=value, values=list(options.values()), state="readonly")
            combo.grid(row=row, column=1, sticky="ew", pady=3)
            combo.bind("<<ComboboxSelected>>", lambda _event: self.update_summary())
            self.fields[attribute] = (value, options, combo)
        actions = ttk.Frame(body)
        actions.grid(row=8, column=0, columnspan=2, sticky="e", pady=(16, 0))
        button(actions, "Cancel", self.window.destroy).pack(side="left", padx=(0, 8))
        button(actions, "Choose output file...", self.export, accent=True).pack(side="left")
        self.update_summary()

    def settings(self):
        return ExportSettings(**{
            name: next(key for key, label in options.items() if label == variable.get())
            for name, (variable, options, _combo) in self.fields.items()
        })

    def update_summary(self):
        settings = self.settings()
        width, height = settings.size
        self.summary.set(f"{width} × {height} pixels  ·  {settings.fps} fps  ·  Stereo 48 kHz")
        self.fields["quality"][2].configure(state="disabled" if settings.profile == "prores" else "readonly")

    def export(self):
        settings = self.settings()
        if self.on_export(self.window, settings):
            self.window.destroy()
