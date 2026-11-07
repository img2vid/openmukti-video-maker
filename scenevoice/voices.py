"""Piper model catalog and offline voice storage."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .jobs import Job
from .model import SceneVoiceError, atomic_json

REPOSITORY = "https://huggingface.co/rhasspy/piper-voices"
CATALOG_URL = f"{REPOSITORY}/resolve/main/voices.json"


@dataclass(frozen=True)
class Voice:
    key: str
    model: Path
    config: Path
    language: str
    speakers: tuple[tuple[int, str], ...]

    @property
    def label(self) -> str:
        return f"{self.key}  ·  {self.language}" if self.language else self.key


def read_voice(key: str, model: Path, config: Path) -> Voice:
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        count = int(data.get("num_speakers", 1))
        if not 1 <= count <= 100_000 or not model.stat().st_size:
            raise ValueError("Invalid model size or speaker count")
        names = {int(number): label for label, number in data.get("speaker_id_map", {}).items()}
        language = data.get("language") or {}
        code = language.get("name_english") or language.get("code") or data.get("espeak", {}).get("voice", "")
        return Voice(key, model, config, code, tuple((i, names.get(i, f"Speaker {i}")) for i in range(count)))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise SceneVoiceError(f"Could not load the voice configuration for {key}. {exc}") from exc


class VoiceStore:
    def __init__(self, directory: Path):
        self.root = directory / "voices"
        self.index = directory / "voice-catalog.json"

    def installed(self) -> dict[str, Voice]:
        voices = {}
        for folder in sorted(self.root.glob("*")):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            model = folder / f"{folder.name}.onnx"
            config = folder / f"{folder.name}.onnx.json"
            if model.is_file() and config.is_file():
                try:
                    voices[folder.name] = read_voice(folder.name, model, config)
                except SceneVoiceError:
                    continue
        return voices

    def cached_catalog(self) -> dict:
        try:
            data = json.loads(self.index.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def refresh(self, job: Job) -> dict:
        job.notify("Loading the Piper voice catalog...", None)
        try:
            with urlopen(Request(CATALOG_URL, headers={"User-Agent": "SceneVoice-Studio/1.0"}), timeout=20) as response:
                data = json.loads(response.read(8 * 1024 * 1024))
        except (OSError, ValueError) as exc:
            raise SceneVoiceError(f"Could not load the online voice catalog. {exc}") from exc
        job.check()
        if not isinstance(data, dict) or not data:
            raise SceneVoiceError("The voice catalog is empty or invalid.")
        atomic_json(self.index, data)
        return data

    def import_voice(self, job: Job, model: Path) -> str:
        key = model.stem
        config = model.with_suffix(".onnx.json")
        if model.suffix.lower() != ".onnx" or not config.is_file():
            raise SceneVoiceError("Put the matching .onnx.json file beside the .onnx file, then select the .onnx file.")
        read_voice(key, model, config)
        target = self._destination(key)
        job.notify(f"Importing {key}...", None)
        with tempfile.TemporaryDirectory(prefix=".voice-", dir=self.root) as scratch:
            staged = Path(scratch) / key
            staged.mkdir()
            shutil.copyfile(model, staged / f"{key}.onnx")
            shutil.copyfile(config, staged / f"{key}.onnx.json")
            card = model.parent / "MODEL_CARD"
            if card.is_file():
                shutil.copyfile(card, staged / "MODEL_CARD")
            job.commit(lambda: staged.rename(target))
        return key

    def _destination(self, key: str) -> Path:
        if not key or key.startswith(".") or Path(key).name != key or "\\" in key:
            raise SceneVoiceError("The voice name is invalid.")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / key
        if target.exists():
            raise SceneVoiceError(f"A voice named {key} is already installed.")
        return target

    def download(self, job: Job, key: str, item: dict) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise SceneVoiceError("The catalog contains an invalid voice name.")
        target = self._destination(key)
        filenames = {f"{key}.onnx", f"{key}.onnx.json", "MODEL_CARD"}
        files = {}
        for remote, info in item.get("files", {}).items():
            path = PurePosixPath(remote)
            if path.name in filenames and not path.is_absolute() and ".." not in path.parts and "\\" not in remote:
                files[path.name] = (remote, info)
        if f"{key}.onnx" not in files or f"{key}.onnx.json" not in files:
            raise SceneVoiceError("The selected catalog entry is missing a model or configuration.")

        total = max(1, sum(info.get("size_bytes", 0) for _, info in files.values()))
        downloaded = 0
        job.notify(f"Connecting to download {key}...", 0)
        try:
            with tempfile.TemporaryDirectory(prefix=".voice-", dir=self.root) as scratch:
                staged = Path(scratch) / key
                staged.mkdir()
                for name, (remote, info) in files.items():
                    job.check()
                    url = f"{REPOSITORY}/resolve/main/{quote(remote, safe='/')}"
                    digest = hashlib.md5(usedforsecurity=False)
                    with urlopen(Request(url, headers={"User-Agent": "SceneVoice-Studio/1.0"}), timeout=30) as response:
                        with (staged / name).open("wb") as output:
                            while chunk := response.read(256 * 1024):
                                job.check()
                                output.write(chunk)
                                digest.update(chunk)
                                downloaded += len(chunk)
                                job.notify(f"Downloading {key} · {downloaded / 1048576:.1f} MiB", min(0.99, downloaded / total))
                    expected_size = info.get("size_bytes")
                    if expected_size is not None and (staged / name).stat().st_size != expected_size:
                        raise SceneVoiceError(f"The download of {name} has an unexpected size.")
                    if info.get("md5_digest") and digest.hexdigest() != info["md5_digest"]:
                        raise SceneVoiceError(f"The download of {name} did not pass its checksum.")
                read_voice(key, staged / f"{key}.onnx", staged / f"{key}.onnx.json")
                job.commit(lambda: staged.rename(target))
        except (URLError, OSError) as exc:
            raise SceneVoiceError(f"Could not download {key}. {exc}") from exc
        return key
