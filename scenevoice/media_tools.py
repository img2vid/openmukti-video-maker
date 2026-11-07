"""Resolve FFmpeg and FFprobe together and install the Windows app copy."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .jobs import Job
from .model import SceneVoiceError, user_data

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_VERSION = "9.0.2"
WINDOWS_BUILD = f"ffmpeg-{WINDOWS_VERSION}-essentials_build"
WINDOWS_ARCHIVE_SIZE = 114_768_076
WINDOWS_ARCHIVE_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
WINDOWS_ARCHIVE_URLS = (
    f"https://github.com/GyanD/codexffmpeg/releases/download/{WINDOWS_VERSION}/{WINDOWS_BUILD}.zip",
    f"https://www.gyan.dev/ffmpeg/builds/packages/{WINDOWS_BUILD}.zip",
)
REQUIRED_ENCODERS = {"aac", "libopus", "libvpx-vp9", "libx264", "libx265", "pcm_s24le", "prores_ks"}
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Tools:
    ffmpeg: Path
    ffprobe: Path
    source: str = "Selected folder"


def supports_managed_tools() -> bool:
    return os.name == "nt" and platform.machine().lower() in {"amd64", "x86_64"}


def _managed_folder() -> Path:
    return user_data() / "media-tools" / f"ffmpeg-{WINDOWS_VERSION}-win64"


def _pair(folder: Path, source: str, *, extension: str) -> Tools | None:
    ffmpeg = folder / f"ffmpeg{extension}"
    ffprobe = folder / f"ffprobe{extension}"
    if ffmpeg.is_file() and ffprobe.is_file() and ffmpeg.stat().st_size and ffprobe.stat().st_size:
        return Tools(ffmpeg.resolve(), ffprobe.resolve(), source)
    return None


def managed_tools() -> Tools | None:
    if supports_managed_tools():
        return _pair(_managed_folder(), "SceneVoice installation", extension=".exe")
    return None


def find_tools(folder: str = "", *, allow_fallback: bool = False) -> Tools:
    extension = ".exe" if os.name == "nt" else ""
    if folder:
        selected = _pair(Path(folder), "Selected folder", extension=extension)
        if selected:
            return selected
        if not allow_fallback:
            raise SceneVoiceError("Choose the folder containing both FFmpeg and FFprobe.")

    for bundled in (PROJECT_ROOT / "tools", PROJECT_ROOT / "tools/bin"):
        pair = _pair(bundled, "SceneVoice tools folder", extension=extension)
        if pair:
            return pair

    installed = managed_tools()
    if installed:
        return installed

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return Tools(Path(ffmpeg).resolve(), Path(ffprobe).resolve(), "System PATH")

    if supports_managed_tools():
        raise SceneVoiceError("Open Setup and choose Install FFmpeg and FFprobe.")
    raise SceneVoiceError("Install FFmpeg and FFprobe or choose the folder containing both in Setup.")


def _download_archive(job: Job, target: Path) -> None:
    errors = []
    for url in WINDOWS_ARCHIVE_URLS:
        job.check()
        host = urlsplit(url).hostname
        job.notify(f"Connecting to {host} for FFmpeg {WINDOWS_VERSION}...", 0)
        digest = hashlib.sha256()
        received = 0
        try:
            request = Request(url, headers={"User-Agent": "SceneVoice Studio"})
            with urlopen(request, timeout=25) as response, target.open("wb") as output:
                if urlsplit(response.geturl()).scheme != "https":
                    raise SceneVoiceError("The download must use HTTPS.")
                while chunk := response.read(CHUNK_BYTES):
                    job.check()
                    received += len(chunk)
                    if received > WINDOWS_ARCHIVE_SIZE:
                        raise SceneVoiceError("The archive is larger than expected.")
                    digest.update(chunk)
                    output.write(chunk)
                    job.notify(
                        f"Downloading FFmpeg and FFprobe · {received / CHUNK_BYTES:.0f} MiB",
                        0.8 * received / WINDOWS_ARCHIVE_SIZE,
                    )
            if received != WINDOWS_ARCHIVE_SIZE or digest.hexdigest() != WINDOWS_ARCHIVE_SHA256:
                raise SceneVoiceError("The archive failed its size or SHA-256 check.")
            return
        except (OSError, HTTPException, SceneVoiceError) as exc:
            errors.append(f"{host} · {exc}")

    job.check()
    raise SceneVoiceError("Could not download FFmpeg and FFprobe. Check your connection and retry from Setup.\n\n"
                          + "\n".join(errors))


def _extract_windows_tools(job: Job, archive: Path, destination: Path) -> None:
    destination.mkdir()
    try:
        with zipfile.ZipFile(archive) as package:
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                job.notify(f"Preparing {name}...", 0.84)
                with package.open(f"{WINDOWS_BUILD}/bin/{name}") as source, (destination / name).open("wb") as output:
                    while chunk := source.read(CHUNK_BYTES):
                        job.check()
                        output.write(chunk)
            for name in ("LICENSE", "LICENSE.txt", "README.txt"):
                member = f"{WINDOWS_BUILD}/{name}"
                if member in package.namelist():
                    (destination / name).write_bytes(package.read(member))
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise SceneVoiceError(f"Could not extract the FFmpeg tools.\n\n{exc}") from exc


def _verify_windows_tools(job: Job, folder: Path) -> None:
    job.notify("Checking FFmpeg, FFprobe, and video encoders...", 0.93)
    for name in ("ffmpeg", "ffprobe"):
        version = job.run([folder / f"{name}.exe", "-version"], f"Could not start {name}")
        if not version.startswith(f"{name} version {WINDOWS_VERSION}".encode("ascii")):
            raise SceneVoiceError(f"{name} returned an unexpected version.")
    listing = job.run([folder / "ffmpeg.exe", "-hide_banner", "-encoders"], "Could not read the FFmpeg encoders")
    available = {fields[1] for line in listing.decode("utf-8", errors="replace").splitlines()
                 if len(fields := line.split()) >= 2 and len(fields[0]) == 6}
    missing = REQUIRED_ENCODERS - available
    if missing:
        raise SceneVoiceError("The FFmpeg build is missing these encoders. " + ", ".join(sorted(missing)))


def install_managed_tools(job: Job) -> Tools:
    if not supports_managed_tools():
        raise SceneVoiceError("The app download is available on Windows x64. Choose an installed FFmpeg folder in Setup.")
    existing = managed_tools()
    if existing:
        return existing

    target = _managed_folder()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ffmpeg-setup-", dir=target.parent) as directory:
        work = Path(directory)
        archive = work / "ffmpeg.zip"
        staged = work / "bin"
        _download_archive(job, archive)
        _extract_windows_tools(job, archive, staged)
        _verify_windows_tools(job, staged)

        def publish() -> None:
            try:
                staged.rename(target)
            except OSError:
                if managed_tools():
                    return
                # A manually removed executable can leave an incomplete cache.
                # Each replacement comes from the same verified release.
                if not target.is_dir():
                    raise
                for file in staged.iterdir():
                    os.replace(file, target / file.name)

        job.commit(publish)

    installed = managed_tools()
    if installed is None:
        raise SceneVoiceError("Could not find FFmpeg and FFprobe after setup.")
    return installed
