"""The guided microphone and teleprompter window."""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import font, messagebox, ttk
from uuid import uuid4

from .model import SceneVoiceError
from .recording import Recorder, microphones
from .ui import button, dialog, open_path, show_error, text_area


class Prompter:
    def __init__(self, parent, scenes, index: int, session: Path, guided: bool, on_keep, on_close):
        self.scenes = scenes
        self.index = index
        self.session = session
        self.guided = guided
        self.on_keep = on_keep
        self.on_close = on_close
        self.take: Path | None = None
        self.recorder: Recorder | None = None
        self.countdown_id = None
        self.timer_id = None
        self.started = 0.0
        self.last_tick = 0.0
        self.script_pixels = 1
        self.window = dialog(parent, "Recording Studio", minimum=(760, 540))
        self.window.geometry("960x630")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.bind("<Destroy>", self.on_destroy, add="+")
        self.device_ids = []
        self.device = tk.StringVar(self.window)
        self.autoscroll = tk.BooleanVar(self.window, True)
        self.speed = tk.DoubleVar(self.window, 12)
        self.status = tk.StringVar(self.window)
        self.timer = tk.StringVar(self.window, "00:00")
        self.step = tk.StringVar(self.window)
        self.scene_name = tk.StringVar(self.window)

        body = ttk.Frame(self.window, padding=18)
        body.pack(expand=True, fill="both")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, textvariable=self.step, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, textvariable=self.scene_name, style="Heading.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 10))

        controls = ttk.Frame(body)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Microphone").grid(row=0, column=0, padx=(0, 8))
        self.device_combo = ttk.Combobox(controls, textvariable=self.device, state="readonly", width=20)
        self.device_combo.grid(row=0, column=1, sticky="ew")
        self.refresh_button = button(controls, "Refresh", self.refresh_devices)
        self.refresh_button.grid(row=0, column=2, padx=(6, 14))
        ttk.Checkbutton(controls, text="Auto-scroll", variable=self.autoscroll).grid(row=0, column=3)
        ttk.Scale(controls, from_=4, to=70, variable=self.speed, length=80).grid(row=0, column=4, padx=(6, 12))
        button(controls, "A-", lambda: self.resize_text(-2)).grid(row=0, column=5, padx=(0, 4))
        button(controls, "A+", lambda: self.resize_text(2)).grid(row=0, column=6)

        family = font.nametofont("TkDefaultFont").actual("family")
        self.prompt_font = font.Font(root=self.window, family=family, size=23)
        frame, self.script = text_area(body, height=6, font_name=self.prompt_font)
        frame.grid(row=3, column=0, sticky="nsew")
        self.script.configure(state="disabled", padx=28, pady=22, spacing2=7, spacing3=16, takefocus=False)

        meter = ttk.Frame(body)
        meter.grid(row=4, column=0, sticky="ew", pady=(8, 6))
        meter.columnconfigure(0, weight=1)
        ttk.Label(meter, textvariable=self.status, style="Muted.TLabel", wraplength=640).grid(row=0, column=0, sticky="w")
        ttk.Label(meter, textvariable=self.timer, style="Section.TLabel").grid(row=0, column=1, sticky="e")

        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, sticky="ew")
        actions.columnconfigure(2, weight=1)
        self.record_button = button(actions, "Start recording", self.toggle_record, accent=True)
        self.record_button.grid(row=0, column=0, padx=(0, 6))
        self.listen_button = button(actions, "Listen", self.listen)
        self.listen_button.grid(row=0, column=1)
        self.back_button = button(actions, "Back", lambda: self.move(-1))
        self.skip_button = button(actions, "Skip", lambda: self.move(1))
        if guided:
            self.back_button.grid(row=0, column=3, padx=(6, 0))
            self.skip_button.grid(row=0, column=4, padx=(6, 0))
        self.keep_button = button(actions, "Keep take", self.keep)
        self.keep_button.grid(row=0, column=5, padx=(6, 0))
        button(actions, "Close", self.close).grid(row=0, column=6, padx=(6, 0))

        self.device_error = ""
        self.refresh_devices()
        self.load_scene()

    @property
    def scene(self):
        return self.scenes[self.index]

    def refresh_devices(self):
        try:
            devices = microphones()
            self.device_ids = [device for device, _ in devices]
            self.device_combo.configure(values=[label for _, label in devices])
            self.device_combo.current(0)
            self.device_error = ""
            self.status.set("Select a microphone and start a take when ready.")
        except SceneVoiceError as exc:
            self.device_ids = []
            self.device_combo.configure(values=[])
            self.device.set("")
            self.device_error = str(exc)
            self.status.set(self.device_error)
        if hasattr(self, "record_button"):
            self.update_buttons()

    def load_scene(self):
        self.step.set(f"GUIDED RECORDING  ·  SCENE {self.index + 1} OF {len(self.scenes)}" if self.guided
                      else f"RECORDING STUDIO  ·  SCENE {self.index + 1}")
        self.scene_name.set(self.scene.name)
        self.script.configure(state="normal")
        self.script.delete("1.0", "end")
        self.script.insert("1.0", self.scene.script.strip() or "This scene has no script. Record freely or add text in the editor.")
        self.script.configure(state="disabled")
        self.script.yview_moveto(0)
        self.status.set(self.device_error or ("An earlier take is saved for this scene." if self.scene.recording
                        else "A three-second countdown gives you time to get ready."))
        self.timer.set("00:00")
        self.update_buttons()

    def update_buttons(self):
        busy = self.recorder is not None or self.countdown_id is not None
        self.record_button.configure(
            text="Stop recording" if self.recorder else "Cancel countdown" if self.countdown_id
            else "Record another take" if self.take else "Start recording"
        )
        self.record_button.state(["!disabled"] if self.device_ids else ["disabled"])
        self.device_combo.configure(state="disabled" if busy else "readonly")
        self.refresh_button.state(["disabled"] if busy else ["!disabled"])
        existing = self.scene.recording and Path(self.scene.recording).is_file()
        self.listen_button.state(["!disabled"] if (self.take or existing) and not busy else ["disabled"])
        self.keep_button.state(["!disabled"] if self.take and not busy else ["disabled"])
        self.keep_button.configure(text=("Keep & next" if self.index + 1 < len(self.scenes) else "Keep & finish")
                                   if self.guided else "Keep take")
        self.back_button.state(["!disabled"] if self.index > 0 and not busy else ["disabled"])
        self.skip_button.state(["disabled"] if busy else ["!disabled"])
        self.skip_button.configure(text="Skip" if self.index + 1 < len(self.scenes) else "Finish")

    def resize_text(self, delta):
        self.prompt_font.configure(size=min(46, max(13, self.prompt_font.cget("size") + delta)))
        self.measure_script()

    def measure_script(self):
        self.window.update_idletasks()
        count = self.script.count("1.0", "end", "update", "ypixels")
        self.script_pixels = max(1, count[0] if isinstance(count, tuple) else count or 1)

    def toggle_record(self):
        if self.recorder:
            self.stop()
        elif self.countdown_id:
            self.window.after_cancel(self.countdown_id)
            self.countdown_id = None
            self.status.set("Countdown cancelled. Start again when ready.")
            self.update_buttons()
        elif self.device_ids:
            if self.take and not messagebox.askyesno("Record a new take", "Discard this unsaved take and record again?",
                                                     parent=self.window):
                return
            self.discard_take()
            self.script.yview_moveto(0)
            self.timer.set("00:00")
            self.countdown(3)

    def countdown(self, remaining):
        self.countdown_id = None
        if remaining > 0:
            self.status.set(f"Get ready... recording starts in {remaining}")
            self.countdown_id = self.window.after(1000, self.countdown, remaining - 1)
        else:
            try:
                device = self.device_ids[max(0, self.device_combo.current())]
                self.recorder = Recorder(self.session / f"take-{uuid4().hex}.wav", device)
                self.started = self.last_tick = time.monotonic()
                self.measure_script()
                self.status.set("Recording live. Read the script and stop when finished.")
                self.tick()
            except SceneVoiceError as exc:
                self.status.set("The microphone could not start.")
                show_error(self.window, "Microphone unavailable", exc)
        self.update_buttons()

    def tick(self):
        now = time.monotonic()
        elapsed = int(now - self.started)
        self.timer.set(f"{elapsed // 60:02}:{elapsed % 60:02}")
        if self.autoscroll.get():
            first, last = self.script.yview()
            if last < 1:
                step = (now - self.last_tick) * self.speed.get() / self.script_pixels
                self.script.yview_moveto(first + step)
        self.last_tick = now
        self.timer_id = self.window.after(50, self.tick)

    def stop(self):
        if self.timer_id:
            self.window.after_cancel(self.timer_id)
            self.timer_id = None
        recorder, self.recorder = self.recorder, None
        try:
            duration = recorder.finish()
            self.take = recorder.path
            self.status.set(f"Take ready · {duration:.2f} seconds. Listen, keep it, or record again.")
        except SceneVoiceError as exc:
            self.status.set("Record another take when ready.")
            show_error(self.window, "Take could not be saved", exc)
        self.update_buttons()

    def listen(self):
        path = self.take or Path(self.scene.recording)
        try:
            open_path(path)
        except SceneVoiceError as exc:
            show_error(self.window, "Playback unavailable", exc)

    def keep(self, *, advance=True):
        if self.take is None:
            return
        self.scene.recording = str(self.take)
        self.scene.source = "recording"
        self.take = None
        self.on_keep(self.index)
        if advance and self.guided and self.index + 1 < len(self.scenes):
            self.index += 1
            self.load_scene()
        elif advance:
            self.finish()

    def discard_take(self):
        if self.take:
            try:
                self.take.unlink(missing_ok=True)
            except PermissionError:
                # The default audio player may still be using this session file.
                pass
            self.take = None

    def confirm_take(self) -> bool:
        if not self.take:
            return True
        answer = messagebox.askyesnocancel("Unsaved take", "Keep this take before leaving the scene?", parent=self.window)
        if answer is None:
            return False
        if answer:
            self.keep(advance=False)
        else:
            self.discard_take()
        return True

    def move(self, step):
        if not self.confirm_take():
            return
        self.index += step
        if self.index == len(self.scenes):
            self.finish()
        else:
            self.load_scene()

    def close(self):
        if self.recorder:
            if not messagebox.askyesno("Recording in progress", "Stop recording and review the take?", parent=self.window):
                return
            self.stop()
        if self.confirm_take():
            self.finish()

    def finish(self):
        self.window.destroy()
        self.on_close()

    def on_destroy(self, event):
        if event.widget == self.window:
            if self.countdown_id:
                self.window.after_cancel(self.countdown_id)
            if self.timer_id:
                self.window.after_cancel(self.timer_id)
            if self.recorder:
                self.recorder.finish(discard=True)
