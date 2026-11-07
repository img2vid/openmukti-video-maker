"""SceneVoice Studio's storyboard editor."""

from __future__ import annotations

import copy
import queue
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4

from . import __version__
from .dialogs import ExportWindow, VoiceLibrary
from .media_tools import (
    find_tools,
    install_managed_tools,
    managed_tools,
    supports_managed_tools,
)
from .model import (
    EXTRA_MODES,
    FIT_MODES,
    PROFILES,
    RATIOS,
    SOURCES,
    Project,
    Scene,
    SceneVoiceError,
    atomic_json,
    is_image,
    load_project,
    read_preferences,
    save_project,
    user_data,
)
from .piper_runtime import install_piper, piper_python
from .prompter import Prompter
from .recording import microphones
from .render import check_project, export_video, thumbnail
from .ui import (
    AUDIO_FILTER,
    EDGE,
    FIELD,
    IMAGE_FILTER,
    MUTED,
    VISUAL_FILTER,
    TaskDialog,
    button,
    dialog,
    open_path,
    show_error,
    text_area,
    theme,
)
from .voices import VoiceStore

PROJECT_FILTER = [("SceneVoice projects", "*.scenevoice"), ("All files", "*")]
SHORT_SOURCE = {"piper": "Piper", "file": "File", "recording": "Mic"}


class Studio:
    def __init__(self, root: tk.Tk):
        self.root = root
        theme(root)
        root.geometry("1160x770")
        root.minsize(970, 650)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.directory = user_data()
        self.preferences = read_preferences(self.directory)
        folder = self.preferences.get("ffmpeg_folder", "")
        self.ffmpeg_folder = folder if isinstance(folder, str) else ""
        self.store = VoiceStore(self.directory)
        self.voices = self.store.installed()
        self.session = tempfile.TemporaryDirectory(prefix="scenevoice-session-", ignore_cleanup_errors=True)
        self.session_path = Path(self.session.name)
        self.project = Project()
        self.project_path: Path | None = None
        self.index: int | None = None
        self.dirty = False
        self.loading = False
        self.thumbnail_version = 0
        self.thumbnail_pending = None
        self.thumbnail_requests = queue.Queue(maxsize=1)
        self.thumbnail_results = queue.Queue()
        self.thumbnail_photo = None
        self.status = tk.StringVar(root, "Add videos or images to begin.")
        self.output_info = tk.StringVar(root)
        self.count = tk.StringVar(root)
        self.cover_info = tk.StringVar(root)
        self.ratio = tk.StringVar(root)
        self.fit = tk.StringVar(root)
        self.build()
        self.refresh_project()
        threading.Thread(target=self.thumbnail_worker, daemon=True).start()
        self.root.after(100, self.poll_thumbnails)

        modifier = "Command" if sys.platform == "darwin" else "Control"
        for key, command in (("n", self.new), ("o", self.open), ("s", self.save),
                             ("Shift-S", lambda: self.save(save_as=True))):
            root.bind(f"<{modifier}-{key}>", lambda _event, call=command: self.shortcut(call))

    @property
    def scene(self) -> Scene | None:
        return None if self.index is None else self.project.scenes[self.index]

    def build(self):
        shell = ttk.Frame(self.root, padding=(16, 12))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        brand = ttk.Frame(header)
        brand.pack(side="left")
        ttk.Label(brand, text="SceneVoice Studio", style="Title.TLabel").pack(anchor="w")
        ttk.Label(brand, text="STORYBOARD   /   NARRATION   /   VIDEO", style="Section.TLabel").pack(anchor="w")
        button(header, "Setup", self.setup).pack(side="right")
        button(header, "Piper voices", self.voice_library).pack(side="right", padx=(0, 6))

        toolbar = ttk.Frame(shell)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for label, call in (("New", self.new), ("Open...", self.open), ("Save", self.save),
                            ("Save as...", lambda: self.save(save_as=True))):
            button(toolbar, label, call).pack(side="left", padx=(0, 5))
        self.export_button = button(toolbar, "Export video...", self.export, accent=True)
        self.export_button.pack(side="right")
        self.preview_button = button(toolbar, "Preview scene", self.preview)
        self.preview_button.pack(side="right", padx=(0, 6))
        self.guided_button = button(toolbar, "Guided recording...", lambda: self.prompt(guided=True))
        self.guided_button.pack(side="right", padx=(0, 6))
        button(toolbar, "Podcast cover...", self.podcast_cover).pack(side="right", padx=(0, 6))
        ttk.Separator(shell).grid(row=2, column=0, sticky="ew", pady=(0, 12))

        workspace = ttk.Frame(shell)
        workspace.grid(row=3, column=0, sticky="nsew")
        workspace.columnconfigure(0, minsize=270)
        workspace.columnconfigure(1, weight=1)
        workspace.rowconfigure(0, weight=1)
        self.build_scenes(workspace)

        detail = ttk.Frame(workspace, padding=(14, 0, 0, 0))
        detail.grid(row=0, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(0, weight=1)
        self.empty = ttk.Frame(detail, style="Panel.TFrame", padding=28)
        self.empty.grid(row=0, column=0, sticky="nsew")
        ttk.Label(self.empty, text="START A PROJECT", style="PanelSection.TLabel").pack(anchor="w", pady=(8, 8))
        ttk.Label(self.empty, text="Turn a script into a narrated video", style="Panel.TLabel",
                  font="SceneVoiceHeading").pack(anchor="w")
        ttk.Label(
            self.empty,
            text="Add your clips or still images. Give each scene a script, choose a narration source, and export in scene order.",
            style="PanelMuted.TLabel", wraplength=480,
        ).pack(anchor="w", pady=(8, 14))
        button(self.empty, "Add videos or images...", self.add_media, accent=True).pack(anchor="w")
        ttk.Label(self.empty, text="For a video podcast, set a podcast cover and add blank scenes.",
                  style="PanelMuted.TLabel", wraplength=480).pack(anchor="w", pady=(14, 0))

        self.editor = ttk.Frame(detail)
        self.editor.grid(row=0, column=0, sticky="nsew")
        self.editor.columnconfigure(0, weight=1)
        self.editor.rowconfigure(3, weight=1)
        self.scene_heading = tk.StringVar(self.root)
        ttk.Label(self.editor, textvariable=self.scene_heading, style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        self.build_visual()
        ttk.Label(self.editor, text="SCRIPT / TELEPROMPTER", style="Section.TLabel").grid(
            row=2, column=0, sticky="w", pady=(8, 4))
        frame, self.script = text_area(self.editor, height=4)
        frame.grid(row=3, column=0, sticky="nsew")
        self.script.bind("<<Modified>>", self.script_modified)
        self.build_audio()

        footer = ttk.Frame(shell)
        footer.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(footer, text="Canvas", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        ratio = ttk.Combobox(footer, textvariable=self.ratio, values=RATIOS, state="readonly", width=6)
        ratio.pack(side="left", padx=(0, 6))
        fit = ttk.Combobox(footer, textvariable=self.fit, values=list(FIT_MODES.values()), state="readonly", width=14)
        fit.pack(side="left", padx=(0, 10))
        for combo in (ratio, fit):
            combo.bind("<<ComboboxSelected>>", self.change_canvas)
        ttk.Label(footer, textvariable=self.output_info, style="Muted.TLabel").pack(side="left")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel", anchor="e").pack(side="right", fill="x", expand=True)

    def build_scenes(self, workspace):
        left = ttk.Frame(workspace, style="Panel.TFrame", padding=10)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        heading = ttk.Frame(left, style="Panel.TFrame")
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(heading, text="SCENE ORDER", style="PanelSection.TLabel").pack(side="left")
        ttk.Label(heading, textvariable=self.count, style="PanelMuted.TLabel").pack(side="right")

        create = ttk.Frame(left, style="Panel.TFrame")
        create.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        button(create, "Add media...", self.add_media, accent=True).pack(side="left")
        button(create, "Blank scene", self.add_blank).pack(side="left", padx=(6, 0))
        listing = ttk.Frame(left, style="Panel.TFrame")
        listing.grid(row=2, column=0, sticky="nsew")
        self.tree = ttk.Treeview(listing, columns=("number", "scene", "voice"), show="headings", selectmode="browse", height=4)
        for key, title, width in (("number", "#", 34), ("scene", "Scene", 132), ("voice", "Audio", 60)):
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, anchor="w", width=width, minwidth=width, stretch=key == "scene")
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.tree_selected)

        order = ttk.Frame(left, style="Panel.TFrame")
        order.grid(row=3, column=0, sticky="ew", pady=(8, 4))
        self.up = button(order, "Up", lambda: self.move(-1))
        self.down = button(order, "Down", lambda: self.move(1))
        self.remove = button(order, "Remove", self.remove_scene)
        for item in (self.up, self.down, self.remove):
            item.pack(side="left", padx=(0, 5))
        ttk.Label(left, textvariable=self.cover_info, style="PanelMuted.TLabel", width=28,
                  wraplength=235).grid(row=4, column=0, sticky="w")

    def build_visual(self):
        row = ttk.Frame(self.editor, padding=8, style="Panel.TFrame")
        row.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        row.columnconfigure(1, weight=1)
        self.canvas = tk.Canvas(row, width=196, height=110, bg=FIELD, highlightbackground=EDGE, highlightthickness=1)
        self.canvas.grid(row=0, column=0, rowspan=3, sticky="nw", padx=(0, 12))
        controls = ttk.Frame(row, style="Panel.TFrame")
        controls.grid(row=0, column=1, sticky="ew")
        controls.columnconfigure(1, weight=1)
        self.title = tk.StringVar(self.root)
        self.visual = tk.StringVar(self.root)
        self.visual_note = tk.StringVar(self.root)
        ttk.Label(controls, text="Scene title", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(controls, textvariable=self.title).grid(row=0, column=1, columnspan=3, sticky="ew")
        self.title.trace_add("write", self.title_changed)
        ttk.Label(controls, text="Visual", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(controls, textvariable=self.visual, state="readonly", width=14).grid(row=1, column=1, sticky="ew", pady=5)
        button(controls, "Choose...", self.choose_visual).grid(row=1, column=2, padx=5)
        button(controls, "Clear", self.clear_visual).grid(row=1, column=3)
        ttk.Label(controls, textvariable=self.visual_note, style="PanelMuted.TLabel", wraplength=390).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))

    def build_audio(self):
        body = ttk.Frame(self.editor, padding=8, style="Panel.TFrame")
        body.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        body.columnconfigure(0, weight=1)
        first = ttk.Frame(body, style="Panel.TFrame")
        first.grid(row=0, column=0, sticky="ew")
        first.columnconfigure(2, weight=1)
        ttk.Label(first, text="NARRATION", style="PanelSection.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.source = tk.StringVar(self.root)
        combo = ttk.Combobox(first, textvariable=self.source, values=list(SOURCES.values()), width=19, state="readonly")
        combo.grid(row=0, column=1)
        combo.bind("<<ComboboxSelected>>", self.change_source)
        button(first, "Record this scene...", lambda: self.prompt(guided=False)).grid(row=0, column=3, sticky="e")

        self.source_panel = ttk.Frame(body, style="Panel.TFrame")
        self.source_panel.grid(row=1, column=0, sticky="ew", pady=(5, 5))
        self.source_panel.columnconfigure(0, weight=1)
        self.voice_row = ttk.Frame(self.source_panel, style="Panel.TFrame")
        self.voice_row.grid(row=0, column=0, sticky="ew")
        self.voice_row.columnconfigure(1, weight=1)
        self.voice = tk.StringVar(self.root)
        self.speaker = tk.StringVar(self.root)
        self.rate = tk.StringVar(self.root)
        ttk.Label(self.voice_row, text="Voice", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.voice_combo = ttk.Combobox(self.voice_row, textvariable=self.voice, state="readonly", width=18)
        self.voice_combo.grid(row=0, column=1, sticky="ew")
        self.voice_combo.bind("<<ComboboxSelected>>", self.change_voice)
        button(self.voice_row, "Voices...", self.voice_library).grid(row=0, column=2, padx=(5, 9))
        self.speaker_combo = ttk.Combobox(self.voice_row, textvariable=self.speaker, width=11, state="readonly")
        self.speaker_combo.grid(row=0, column=3)
        self.speaker_combo.bind("<<ComboboxSelected>>", self.change_speaker)
        ttk.Label(self.voice_row, text="Rate", style="Panel.TLabel").grid(row=0, column=4, padx=(8, 4))
        rate = ttk.Spinbox(self.voice_row, textvariable=self.rate, from_=0.5, to=2.0, increment=0.05,
                           width=5, format="%.2f", command=self.commit_numbers)
        rate.grid(row=0, column=5)
        rate.bind("<FocusOut>", lambda _event: self.commit_numbers())
        rate.bind("<Return>", lambda _event: self.commit_numbers())
        ttk.Label(self.voice_row, text="×", style="PanelMuted.TLabel").grid(row=0, column=6, padx=(2, 0))

        self.file_row = ttk.Frame(self.source_panel, style="Panel.TFrame")
        self.file_row.grid(row=0, column=0, sticky="ew")
        self.file_row.columnconfigure(1, weight=1)
        self.audio_file = tk.StringVar(self.root)
        self.audio_label = tk.StringVar(self.root)
        ttk.Label(self.file_row, textvariable=self.audio_label, style="Panel.TLabel").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(self.file_row, textvariable=self.audio_file, width=15, state="readonly").grid(row=0, column=1, sticky="ew")
        self.audio_choose = button(self.file_row, "Choose audio...", lambda: self.choose_audio("audio"))
        self.audio_choose.grid(row=0, column=2, padx=5)
        self.audio_listen = button(self.file_row, "Listen", self.listen)
        self.audio_listen.grid(row=0, column=3)

        ttk.Separator(body).grid(row=2, column=0, sticky="ew", pady=(0, 5))
        extras = ttk.Frame(body, style="Panel.TFrame")
        extras.grid(row=3, column=0, sticky="ew")
        extras.columnconfigure(1, weight=1)
        self.extra_file = tk.StringVar(self.root)
        self.extra_mode = tk.StringVar(self.root)
        self.mix = tk.StringVar(self.root)
        ttk.Label(extras, text="Extra audio", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(extras, textvariable=self.extra_file, width=10, state="readonly").grid(row=0, column=1, sticky="ew")
        button(extras, "Choose...", lambda: self.choose_audio("extra_audio")).grid(row=0, column=2, padx=5)
        self.extra_clear = button(extras, "Clear", self.clear_extra)
        self.extra_clear.grid(row=0, column=3, padx=(0, 6))
        self.placement = ttk.Combobox(extras, textvariable=self.extra_mode, values=list(EXTRA_MODES.values()),
                                      state="readonly", width=16)
        self.placement.grid(row=0, column=4)
        self.placement.bind("<<ComboboxSelected>>", self.change_extra_mode)
        self.mix_box = ttk.Spinbox(extras, textvariable=self.mix, from_=0, to=100, increment=5, width=4,
                                   command=self.commit_numbers)
        self.mix_box.grid(row=0, column=5, padx=(6, 0))
        self.mix_box.bind("<FocusOut>", lambda _event: self.commit_numbers())
        self.mix_box.bind("<Return>", lambda _event: self.commit_numbers())
        ttk.Label(extras, text="%", style="PanelMuted.TLabel").grid(row=0, column=6, padx=(2, 0))

    def refresh_project(self):
        self.ratio.set(self.project.export.ratio)
        self.fit.set(FIT_MODES[self.project.export.fit])
        self.rebuild_tree(0 if self.project.scenes else None)
        self.update_footer()

    def update_title(self):
        name = self.project_path.stem if self.project_path else "Untitled project"
        self.root.title(f"{name}{' *' if self.dirty else ''} · SceneVoice Studio")

    def mark_dirty(self):
        self.dirty = True
        self.update_title()

    def update_footer(self):
        count = len(self.project.scenes)
        self.count.set(f"{count} scene{'s' if count != 1 else ''}")
        cover = self.project.cover
        self.cover_info.set(
            f"{'Podcast cover' if self.project.cover_all else 'Fallback cover'} · {Path(cover).name}"
            if cover else "No podcast cover"
        )
        width, height = self.project.export.size
        self.output_info.set(f"{width} × {height}  ·  {self.project.export.fps} fps")
        self.update_title()

    def row_values(self, index):
        scene = self.project.scenes[index]
        return (f"{index + 1:02}", scene.name, SHORT_SOURCE[scene.source])

    def refresh_row(self, index):
        if self.tree.exists(str(index)):
            self.tree.item(str(index), values=self.row_values(index))

    def rebuild_tree(self, select):
        self.tree.delete(*self.tree.get_children())
        for index in range(len(self.project.scenes)):
            self.tree.insert("", "end", iid=str(index), values=self.row_values(index))
        self.index = select
        if select is not None:
            self.tree.selection_set(str(select))
            self.tree.see(str(select))
        self.show_scene()
        self.update_footer()

    def tree_selected(self, _event):
        selected = self.tree.selection()
        if selected and int(selected[0]) != self.index:
            self.commit_numbers()
            self.index = int(selected[0])
            self.show_scene()

    def show_scene(self):
        scene = self.scene
        count = len(self.project.scenes)
        for control, enabled in (
            (self.up, self.index is not None and self.index > 0),
            (self.down, self.index is not None and self.index + 1 < count),
            (self.remove, scene is not None), (self.preview_button, scene is not None),
            (self.export_button, count > 0), (self.guided_button, count > 0),
        ):
            control.state(["!disabled"] if enabled else ["disabled"])
        if scene is None:
            self.editor.grid_remove()
            self.empty.grid()
            self.thumbnail_version += 1
            return
        self.empty.grid_remove()
        self.editor.grid()
        self.loading = True
        try:
            self.scene_heading.set(f"Scene {self.index + 1:02} of {count:02}")
            self.title.set(scene.title)
            self.script.delete("1.0", "end")
            self.script.insert("1.0", scene.script)
            self.script.edit_reset()
            self.script.edit_modified(False)
            self.source.set(SOURCES[scene.source])
            self.rate.set(f"{scene.speech_rate:.2f}")
            self.mix.set(str(scene.mix_percent))
            self.update_audio()
            self.update_visual()
        finally:
            self.loading = False

    def update_visual(self):
        scene = self.scene
        effective = self.project.visual_for(scene)
        self.visual.set(Path(effective).name if effective else "")
        if not effective:
            note = "Choose a video or image, or set a podcast cover."
        elif self.project.cover and (self.project.cover_all or not scene.visual):
            note = ("Using the project’s podcast cover. Your scene visual is kept for later."
                    if scene.visual else "Using the project’s podcast cover.")
        elif is_image(effective):
            note = "The image holds for the duration of the scene audio."
        else:
            note = "The clip speeds up or slows down to match the scene audio."
        if effective and not Path(effective).is_file():
            note = "The visual file is missing. Choose it again to relink it."
        self.visual_note.set(note)
        self.request_thumbnail(effective)

    def update_audio(self):
        scene = self.scene
        if scene.source == "piper":
            self.file_row.grid_remove()
            self.voice_row.grid()
            self.update_voice_controls()
        else:
            self.voice_row.grid_remove()
            self.file_row.grid()
            imported = scene.source == "file"
            value = scene.audio if imported else scene.recording
            self.audio_label.set("Audio file" if imported else "Saved take")
            self.audio_file.set(Path(value).name if value else "")
            if imported:
                self.audio_choose.grid()
            else:
                self.audio_choose.grid_remove()
            self.audio_listen.state(["!disabled"] if value and Path(value).is_file() else ["disabled"])
        self.extra_file.set(Path(scene.extra_audio).name if scene.extra_audio else "")
        self.extra_mode.set(EXTRA_MODES[scene.extra_mode])
        self.placement.configure(state="readonly" if scene.extra_audio else "disabled")
        self.extra_clear.state(["!disabled"] if scene.extra_audio else ["disabled"])
        self.mix_box.configure(state="normal" if scene.extra_audio and scene.extra_mode == "mix" else "disabled")

    def update_voice_controls(self):
        scene = self.scene
        voice = self.voices.get(scene.voice)
        self.voice_combo.configure(values=[item.label for item in self.voices.values()])
        self.voice.set(voice.label if voice else f"{scene.voice} (missing)" if scene.voice else "Select a voice")
        self.speakers = voice.speakers if voice else ()
        self.speaker_combo.configure(values=[f"{number} · {name}" for number, name in self.speakers],
                                     state="readonly" if len(self.speakers) > 1 else "disabled")
        if 0 <= scene.speaker < len(self.speakers):
            self.speaker_combo.current(scene.speaker)
        else:
            self.speaker.set("Speaker")

    def title_changed(self, *_):
        if self.scene is not None and not self.loading and self.scene.title != self.title.get():
            self.scene.title = self.title.get()
            self.refresh_row(self.index)
            self.mark_dirty()

    def script_modified(self, _event=None):
        if self.script.edit_modified():
            self.script.edit_modified(False)
            if self.scene is not None and not self.loading:
                text = self.script.get("1.0", "end-1c")
                if text != self.scene.script:
                    self.scene.script = text
                    self.mark_dirty()

    def commit_numbers(self):
        if self.scene is None or self.loading:
            return
        try:
            rate = float(self.rate.get())
            if not 0.5 <= rate <= 2:
                raise ValueError
        except ValueError:
            rate = self.scene.speech_rate
        try:
            mix = int(self.mix.get())
            if not 0 <= mix <= 100:
                raise ValueError
        except ValueError:
            mix = self.scene.mix_percent
        if (rate, mix) != (self.scene.speech_rate, self.scene.mix_percent):
            self.scene.speech_rate, self.scene.mix_percent = rate, mix
            self.mark_dirty()
        self.rate.set(f"{rate:.2f}")
        self.mix.set(str(mix))

    def change_source(self, _event):
        self.scene.source = next(key for key, label in SOURCES.items() if label == self.source.get())
        self.update_audio()
        self.refresh_row(self.index)
        self.mark_dirty()

    def change_voice(self, _event):
        choice = next((key for key, voice in self.voices.items() if voice.label == self.voice.get()), "")
        if choice:
            self.select_voice(choice)

    def select_voice(self, key):
        if self.scene is None:
            return
        if self.scene.voice != key:
            self.scene.voice = key
            self.scene.speaker = 0
        self.scene.source = "piper"
        self.source.set(SOURCES["piper"])
        self.update_audio()
        self.refresh_row(self.index)
        self.mark_dirty()

    def change_speaker(self, _event):
        index = self.speaker_combo.current()
        if index >= 0:
            self.scene.speaker = self.speakers[index][0]
            self.mark_dirty()

    def change_extra_mode(self, _event):
        self.scene.extra_mode = next(key for key, label in EXTRA_MODES.items() if label == self.extra_mode.get())
        self.update_audio()
        self.mark_dirty()

    def change_canvas(self, _event):
        self.project.export.ratio = self.ratio.get()
        self.project.export.fit = next(key for key, label in FIT_MODES.items() if label == self.fit.get())
        if self.scene:
            self.update_visual()
        self.update_footer()
        self.mark_dirty()

    def add_scenes(self, paths):
        self.commit_numbers()
        index = len(self.project.scenes)
        current = self.scene.voice if self.scene and self.scene.voice in self.voices else ""
        default = current or (next(iter(self.voices)) if len(self.voices) == 1 else "")
        self.project.scenes.extend(Scene(visual=str(Path(path).resolve()) if path else "", voice=default) for path in paths)
        self.rebuild_tree(index)
        self.mark_dirty()
        self.status.set(f"Added {len(paths)} scene{'s' if len(paths) != 1 else ''}.")

    def add_media(self):
        paths = filedialog.askopenfilenames(parent=self.root, title="Add images or videos", filetypes=VISUAL_FILTER)
        if paths:
            self.add_scenes(paths)

    def add_blank(self):
        self.add_scenes([""])

    def move(self, offset):
        self.commit_numbers()
        old = self.index
        new = old + offset
        scenes = self.project.scenes
        if 0 <= new < len(scenes):
            scenes[old], scenes[new] = scenes[new], scenes[old]
            self.rebuild_tree(new)
            self.mark_dirty()

    def remove_scene(self):
        if self.scene and messagebox.askyesno("Remove scene", f"Remove scene {self.index + 1} from this project?",
                                              parent=self.root):
            self.project.scenes.pop(self.index)
            new = min(self.index, len(self.project.scenes) - 1)
            self.rebuild_tree(new if new >= 0 else None)
            self.mark_dirty()

    def choose_visual(self):
        value = filedialog.askopenfilename(parent=self.root, title="Choose visual", filetypes=VISUAL_FILTER)
        if value and self.scene:
            self.scene.visual = str(Path(value).resolve())
            self.update_visual()
            self.refresh_row(self.index)
            self.mark_dirty()

    def clear_visual(self):
        if self.scene and self.scene.visual:
            self.scene.visual = ""
            self.update_visual()
            self.refresh_row(self.index)
            self.mark_dirty()

    def choose_audio(self, field):
        value = filedialog.askopenfilename(parent=self.root, title="Choose audio", filetypes=AUDIO_FILTER)
        if value and self.scene:
            setattr(self.scene, field, str(Path(value).resolve()))
            self.update_audio()
            self.mark_dirty()

    def clear_extra(self):
        self.scene.extra_audio = ""
        self.update_audio()
        self.mark_dirty()

    def listen(self):
        value = self.scene.audio if self.scene.source == "file" else self.scene.recording
        self.open_file(Path(value))

    def open_file(self, path):
        try:
            open_path(path)
        except SceneVoiceError as exc:
            show_error(self.root, "Could not open file", exc)

    def prompt(self, *, guided):
        if not self.project.scenes:
            return
        self.commit_numbers()
        self.script_modified()

        def kept(index):
            self.refresh_row(index)
            self.mark_dirty()
            self.status.set(f"Saved microphone take for scene {index + 1}.")

        Prompter(self.root, self.project.scenes, 0 if guided else self.index, self.session_path,
                 guided, kept, self.show_scene)

    def podcast_cover(self):
        window = dialog(self.root, "Podcast Cover", minimum=(565, 245))
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Podcast cover", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text="Use one still image across every part of a video podcast.",
                  style="Muted.TLabel").pack(anchor="w", pady=(5, 10))
        cover = tk.StringVar(window, self.project.cover)
        all_scenes = tk.BooleanVar(window, self.project.cover_all if self.project.cover else True)
        file_row = ttk.Frame(body)
        file_row.pack(fill="x")
        ttk.Entry(file_row, textvariable=cover, state="readonly").pack(side="left", fill="x", expand=True)

        def choose():
            path = filedialog.askopenfilename(parent=window, title="Choose podcast cover", filetypes=IMAGE_FILTER)
            if path:
                if is_image(path):
                    cover.set(str(Path(path).resolve()))
                else:
                    show_error(window, "Choose a still image", "Select a PNG, JPEG, WebP, or another supported image.")

        button(file_row, "Choose image...", choose).pack(side="left", padx=(6, 0))
        button(file_row, "Clear", lambda: cover.set("")).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(body, text="Use this cover for every scene", variable=all_scenes).pack(anchor="w", pady=(8, 0))
        ttk.Label(body, text="When unchecked, the cover fills scenes without their own visual.",
                  style="Muted.TLabel").pack(anchor="w", pady=(3, 8))

        def apply():
            self.project.cover = cover.get()
            self.project.cover_all = bool(cover.get() and all_scenes.get())
            self.update_footer()
            if self.scene:
                self.update_visual()
            self.mark_dirty()
            window.destroy()

        button(body, "Apply cover", apply, accent=True).pack(anchor="e")

    def voice_library(self):
        def changed():
            self.voices = self.store.installed()
            if self.scene and self.scene.source == "piper":
                self.update_voice_controls()

        VoiceLibrary(self.root, self.store, changed, self.select_voice if self.scene else None)

    def tools(self):
        return find_tools(self.ffmpeg_folder, allow_fallback=True)

    def setup_on_start(self):
        def prepare_piper():
            if sys.prefix != sys.base_prefix and piper_python() is None:
                self.setup_piper()

        if supports_managed_tools():
            try:
                self.tools()
            except SceneVoiceError:
                self.setup_media_tools(on_finished=prepare_piper)
                return
        prepare_piper()

    def setup_media_tools(self, parent=None, on_ready=None, *, on_finished=None):
        def ready(_tools):
            self.status.set("FFmpeg and FFprobe are ready.")
            if self.scene:
                self.update_visual()
            if on_ready:
                on_ready()

        TaskDialog(parent or self.root, "Install FFmpeg and FFprobe", install_managed_tools, ready,
                   finished=on_finished)

    def setup_piper(self, parent=None, on_ready=None):
        def ready(_python):
            self.status.set("Piper is ready for offline narration.")
            if on_ready:
                on_ready()

        TaskDialog(parent or self.root, "Set up offline Piper", install_piper, ready)

    def setup(self):
        window = dialog(self.root, "SceneVoice Setup", minimum=(640, 360))
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Setup", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text=f"SceneVoice Studio {__version__}  ·  Local rendering and speech",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 12))
        status = tk.StringVar(window)
        ttk.Label(body, textvariable=status, justify="left", wraplength=620).pack(anchor="w")

        def refresh():
            try:
                tools = self.tools()
                line = (f"FFmpeg and FFprobe ready · {tools.source}\n"
                        f"FFmpeg {tools.ffmpeg}\nFFprobe {tools.ffprobe}")
            except SceneVoiceError as exc:
                line = str(exc)
            if media_button:
                media_button.state(["disabled"] if managed_tools() else ["!disabled"])
            python = piper_python()
            if python is None:
                piper = "setup needed"
                piper_button.state(["!disabled"])
            else:
                piper = "ready in the editor environment" if python == Path(sys.executable) else "ready in a separate environment"
                piper_button.state(["disabled"])
            try:
                detected = len(microphones()) - 1
                mic = f"{detected} input device{'s' if detected != 1 else ''} detected"
            except SceneVoiceError:
                mic = "PortAudio or sounddevice is missing"
            status.set(f"{line}\n\nPiper · {piper} · {len(self.voices)} installed voice(s)\nMicrophone · {mic}")

        def set_folder(value):
            if value:
                try:
                    find_tools(value)
                except SceneVoiceError as exc:
                    show_error(window, "FFmpeg folder", exc)
                    return
            self.ffmpeg_folder = value
            try:
                self.preferences["ffmpeg_folder"] = value
                atomic_json(self.directory / "preferences.json", self.preferences)
            except OSError as exc:
                show_error(window, "Could not save settings", exc)
            refresh()
            if self.scene:
                self.update_visual()

        def browse():
            folder = filedialog.askdirectory(parent=window, title="Choose the FFmpeg binary folder")
            if folder:
                set_folder(folder)

        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=(12, 8))
        button(controls, "Choose FFmpeg folder...", browse).pack(side="left", padx=(0, 6))
        button(controls, "Use automatic selection", lambda: set_folder("")).pack(side="left")
        button(controls, "FFmpeg downloads", lambda: webbrowser.open("https://ffmpeg.org/download.html")).pack(side="left", padx=6)
        installers = ttk.Frame(body)
        installers.pack(fill="x", pady=(0, 8))
        media_button = None
        if supports_managed_tools():
            media_button = button(installers, "Install FFmpeg and FFprobe", lambda: self.setup_media_tools(window, refresh))
            media_button.pack(side="left", padx=(0, 6))
        piper_button = button(installers, "Set up Piper...", lambda: self.setup_piper(window, refresh))
        piper_button.pack(side="left")
        ttk.Label(body, text="The included README has platform setup steps and troubleshooting.",
                  style="Muted.TLabel", wraplength=560).pack(anchor="w", pady=(0, 8))
        button(body, "Close", window.destroy).pack(anchor="e")
        refresh()

    def request_thumbnail(self, path):
        self.thumbnail_version += 1
        self.canvas.delete("all")
        self.thumbnail_photo = None
        self.canvas.create_text(99, 56, text="Loading preview..." if path else "No visual yet", fill=MUTED)
        if self.thumbnail_pending:
            self.root.after_cancel(self.thumbnail_pending)
        self.thumbnail_pending = self.root.after(110, self.queue_thumbnail, self.thumbnail_version, path)

    def queue_thumbnail(self, version, path):
        self.thumbnail_pending = None
        if version != self.thumbnail_version or not path:
            return
        if not Path(path).is_file():
            self.thumbnail_results.put((version, None, "Visual file missing"))
            return
        sizes = {"16:9": (196, 110), "9:16": (62, 110), "1:1": (110, 110)}
        try:
            tools = self.tools()
        except SceneVoiceError:
            tools = None
        try:
            self.thumbnail_requests.get_nowait()
        except queue.Empty:
            pass
        self.thumbnail_requests.put_nowait((version, Path(path), sizes[self.project.export.ratio], self.project.export.fit, tools))

    def thumbnail_worker(self):
        while True:
            version, path, size, fit, tools = self.thumbnail_requests.get()
            try:
                image = thumbnail(path, size, fit, tools)
                self.thumbnail_results.put((version, image, ""))
            except (SceneVoiceError, OSError, ValueError, ImportError):
                self.thumbnail_results.put((version, None, "Preview unavailable"))

    def poll_thumbnails(self):
        while True:
            try:
                version, image, error = self.thumbnail_results.get_nowait()
            except queue.Empty:
                break
            if version != self.thumbnail_version:
                continue
            self.canvas.delete("all")
            if image is not None:
                from PIL import ImageTk

                self.thumbnail_photo = ImageTk.PhotoImage(image, master=self.root)
                self.canvas.create_image(99, 56, image=self.thumbnail_photo)
            else:
                self.canvas.create_text(99, 56, text=error, fill=MUTED)
        self.root.after(100, self.poll_thumbnails)

    def confirm_changes(self):
        self.commit_numbers()
        self.script_modified()
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel("Unsaved project", "Save your changes to this project?", parent=self.root)
        return answer is False or (answer is True and self.save())

    def new(self):
        if self.confirm_changes():
            self.project = Project()
            self.project_path = None
            self.dirty = False
            self.status.set("Add videos or images to begin.")
            self.refresh_project()

    def open(self):
        if self.confirm_changes():
            path = filedialog.askopenfilename(parent=self.root, title="Open project", filetypes=PROJECT_FILTER)
            if path:
                self.load(Path(path))

    def load(self, path):
        try:
            self.project = load_project(path.resolve())
        except SceneVoiceError as exc:
            show_error(self.root, "Could not open project", exc)
            return
        self.project_path = path.resolve()
        self.dirty = False
        self.status.set(f"Opened {path.name}.")
        self.refresh_project()

    def save(self, *, save_as=False):
        self.commit_numbers()
        self.script_modified()
        path = self.project_path
        if path is None or save_as:
            value = filedialog.asksaveasfilename(parent=self.root, title="Save project", defaultextension=".scenevoice",
                                                filetypes=PROJECT_FILTER, initialfile=path.name if path else "My Video.scenevoice")
            if not value:
                return False
            path = Path(value)
            if path.suffix.lower() != ".scenevoice":
                path = path.with_name(path.name + ".scenevoice")
        try:
            save_project(self.project, path)
        except (OSError, ValueError, SceneVoiceError) as exc:
            show_error(self.root, "Could not save project", exc)
            return False
        self.project_path = path.resolve()
        self.dirty = False
        if self.scene:
            self.update_audio()
        self.update_title()
        self.status.set(f"Saved {path.name}.")
        return True

    def check_render(self, project):
        errors = check_project(project, self.voices)
        if errors:
            detail = "\n".join(errors[:10])
            if len(errors) > 10:
                detail += f"\n...and {len(errors) - 10} more missing inputs."
            show_error(self.root, "Complete your scenes", detail)
            return None
        if any(scene.source == "piper" for scene in project.scenes) and piper_python() is None:
            show_error(self.root, "Piper needed", "Open Setup and choose Set up Piper before rendering Piper narration.")
            return None
        try:
            return self.tools()
        except SceneVoiceError as exc:
            show_error(self.root, "FFmpeg needed", exc)
            return None

    def preview(self):
        if self.scene is None:
            return
        self.commit_numbers()
        self.script_modified()
        project = copy.deepcopy(self.project)
        project.scenes = [project.scenes[self.index]]
        project.export = replace(project.export, resolution="720", profile="h264", quality="compact")
        tools = self.check_render(project)
        if tools:
            path = self.session_path / f"scene-preview-{uuid4().hex[:8]}.mp4"
            voices = dict(self.voices)
            TaskDialog(self.root, "Preview scene", lambda job: export_video(job, tools, project, voices, path),
                       self.open_file)

    def export(self):
        self.commit_numbers()
        self.script_modified()
        tools = self.check_render(self.project)
        if tools:
            ExportWindow(self.root, self.project.export, lambda window, settings: self.choose_export(window, settings, tools))

    def choose_export(self, window, settings, tools):
        name, extension = PROFILES[settings.profile]
        default = self.project_path.stem if self.project_path else "My Video"
        path = filedialog.asksaveasfilename(
            parent=window, title="Save final video", defaultextension=extension, initialfile=default + extension,
            filetypes=[(name, "*" + extension)],
        )
        if not path:
            return False
        destination = Path(path)
        if destination.suffix.lower() != extension:
            show_error(window, "File extension", f"Use the {extension} extension for this format.")
            return False
        if settings != self.project.export:
            self.project.export = settings
            self.ratio.set(settings.ratio)
            self.fit.set(FIT_MODES[settings.fit])
            self.update_footer()
            if self.scene:
                self.update_visual()
            self.mark_dirty()
        project = copy.deepcopy(self.project)
        voices = dict(self.voices)

        def start():
            TaskDialog(self.root, "Exporting video",
                       lambda job: export_video(job, tools, project, voices, destination), self.exported)

        self.root.after_idle(start)
        return True

    def exported(self, path):
        self.status.set(f"Exported {path.name}.")
        window = dialog(self.root, "Video Exported", minimum=(495, 178))
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Video exported", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text=str(path), wraplength=490, style="Muted.TLabel").pack(anchor="w", pady=(8, 12))
        actions = ttk.Frame(body)
        actions.pack(anchor="e")
        button(actions, "Open video", lambda: self.open_file(path), accent=True).pack(side="left", padx=(0, 5))
        button(actions, "Show folder", lambda: self.open_file(path.parent)).pack(side="left", padx=(0, 5))
        button(actions, "Done", window.destroy).pack(side="left")

    def shortcut(self, action):
        if self.root.grab_current() is None:
            action()
        return "break"

    def close(self):
        modal = self.root.grab_current()
        if modal is not None and modal != self.root:
            modal.lift()
            return
        if self.confirm_changes():
            self.session.cleanup()
            self.root.destroy()
