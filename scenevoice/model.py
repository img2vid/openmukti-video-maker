"""Project state, validation, and persistence."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from uuid import uuid4

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif"}
RATIOS = ("16:9", "9:16", "1:1")
RESOLUTIONS = {"720": "720p", "1080": "1080p", "1440": "1440p", "2160": "4K / 2160"}
FRAME_RATES = (24, 25, 30, 50, 60)
FIT_MODES = {"fit": "Fit with bars", "fill": "Fill and crop"}
SOURCES = {"piper": "Piper narration", "file": "Imported audio", "recording": "Microphone take"}
EXTRA_MODES = {"after": "After narration", "before": "Before narration", "mix": "Under narration"}
PROFILES = {
    "h264": ("MP4 · H.264", ".mp4"),
    "hevc": ("MP4 · H.265 / HEVC", ".mp4"),
    "vp9": ("WebM · VP9", ".webm"),
    "prores": ("MOV · ProRes 422 HQ", ".mov"),
}
QUALITIES = {"high": "High", "maximum": "Maximum", "compact": "Compact"}


class SceneVoiceError(Exception):
    """An error that can be shown directly in the editor."""


def user_data() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "SceneVoice Studio"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/SceneVoice Studio"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "scenevoice-studio"


@dataclass
class Scene:
    title: str = ""
    visual: str = ""
    script: str = ""
    source: str = "piper"
    voice: str = ""
    speaker: int = 0
    speech_rate: float = 1.0
    audio: str = ""
    recording: str = ""
    extra_audio: str = ""
    extra_mode: str = "after"
    mix_percent: int = 20

    @property
    def name(self) -> str:
        return self.title.strip() or (Path(self.visual).stem if self.visual else "Untitled scene")


@dataclass
class ExportSettings:
    ratio: str = "16:9"
    resolution: str = "1080"
    fps: int = 30
    fit: str = "fit"
    profile: str = "h264"
    quality: str = "high"

    @property
    def size(self) -> tuple[int, int]:
        short = int(self.resolution)
        wide = short * 16 // 9
        if self.ratio == "16:9":
            return wide, short
        if self.ratio == "9:16":
            return short, wide
        return short, short


@dataclass
class Project:
    scenes: list[Scene] = field(default_factory=list)
    cover: str = ""
    cover_all: bool = False
    export: ExportSettings = field(default_factory=ExportSettings)

    def visual_for(self, scene: Scene) -> str:
        if self.cover and (self.cover_all or not scene.visual):
            return self.cover
        return scene.visual


def validate_export(settings: ExportSettings) -> None:
    if (
        settings.ratio not in RATIOS or settings.resolution not in RESOLUTIONS
        or settings.fps not in FRAME_RATES or settings.fit not in FIT_MODES
        or settings.profile not in PROFILES or settings.quality not in QUALITIES
    ):
        raise SceneVoiceError("The project has an invalid export setting.")


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_TYPES


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".writing-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def relative_to_project(value: str, project_dir: Path) -> str:
    if not value:
        return ""
    try:
        return os.path.relpath(Path(value), project_dir)
    except ValueError:
        # Windows cannot express a relative path between drives.
        return value


def load_project(path: Path) -> Project:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise SceneVoiceError("This file is not a supported SceneVoice project.")
        if not isinstance(data.get("scenes"), list) or not isinstance(data.get("export"), dict):
            raise ValueError("Missing scenes or export settings")

        def asset(value: str) -> str:
            if not isinstance(value, str):
                raise ValueError("Invalid asset path")
            return str((path.parent / value).resolve()) if value else ""

        settings = ExportSettings(**{item.name: data["export"][item.name]
                                    for item in fields(ExportSettings) if item.name in data["export"]})
        validate_export(settings)
        scenes = []
        for raw in data["scenes"]:
            if not isinstance(raw, dict):
                raise ValueError("Invalid scene")
            scene = Scene(**{item.name: raw[item.name] for item in fields(Scene) if item.name in raw})
            for attr in ("title", "script", "voice"):
                if not isinstance(getattr(scene, attr), str):
                    raise ValueError("Invalid scene text")
            for attr in ("visual", "audio", "recording", "extra_audio"):
                setattr(scene, attr, asset(getattr(scene, attr)))
            if (
                scene.source not in SOURCES or scene.extra_mode not in EXTRA_MODES
                or not isinstance(scene.speaker, int) or scene.speaker < 0
                or not isinstance(scene.speech_rate, (int, float)) or not 0.5 <= scene.speech_rate <= 2
                or not isinstance(scene.mix_percent, int) or not 0 <= scene.mix_percent <= 100
            ):
                raise ValueError("Invalid narration settings")
            scenes.append(scene)
        cover_all = data.get("cover_all", False)
        if not isinstance(cover_all, bool):
            raise ValueError("Invalid podcast cover setting")
        return Project(scenes, asset(data.get("cover", "")), cover_all, settings)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise SceneVoiceError(f"Could not open the project. {exc}") from exc


def save_project(project: Project, path: Path) -> None:
    """Copy takes beside the project and update their paths after the save commits."""
    path = path.resolve()
    assets = path.with_name(path.stem + ".assets")
    data = {"schema": 1, **asdict(project)}
    data["cover"] = relative_to_project(project.cover, path.parent)
    copies: dict[Path, Path] = {}
    changes: list[tuple[Scene, Path]] = []
    for scene, raw in zip(project.scenes, data["scenes"]):
        for attr in ("visual", "audio", "extra_audio"):
            raw[attr] = relative_to_project(raw[attr], path.parent)
        if scene.recording:
            source = Path(scene.recording).resolve()
            if source.parent == assets or not source.is_file():
                destination = source
            elif source in copies:
                destination = copies[source]
            else:
                assets.mkdir(parents=True, exist_ok=True)
                destination = assets / f"take-{uuid4().hex}.wav"
                temporary = destination.with_suffix(".partial")
                try:
                    shutil.copyfile(source, temporary)
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                copies[source] = destination
            raw["recording"] = relative_to_project(str(destination), path.parent)
            changes.append((scene, destination))
    atomic_json(path, data)
    for scene, destination in changes:
        scene.recording = str(destination)


def read_preferences(directory: Path) -> dict:
    try:
        data = json.loads((directory / "preferences.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
