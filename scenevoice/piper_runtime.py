"""Find or set up the offline Piper interpreter used by the editor."""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import sysconfig
from functools import cache
from pathlib import Path

from .jobs import Job
from .model import SceneVoiceError

PIPER_PACKAGE = "piper-tts==1.8.0"
WORKER_VERSION = "3.14"
UV_PACKAGE = "uv==0.13.5"
WORKER_ENV = Path(__file__).resolve().parents[1] / ".piper-venv"
PIP_INSTALL = [
    sys.executable, "-m", "pip", "--disable-pip-version-check", "install",
    "--only-binary=:all:", "--retries", "2", "--timeout", "20",
]


def worker_python() -> Path:
    relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return WORKER_ENV / relative


@cache
def _can_start(python: Path) -> bool:
    try:
        probe = subprocess.run(
            [str(python), "-I", "-m", "piper", "--help"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, timeout=10,
        )
        return probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def piper_python() -> Path | None:
    """Resolve a verified local runner without installing or downloading anything."""
    worker = worker_python()
    marker = WORKER_ENV / "scenevoice-piper.txt"
    try:
        ready = marker.read_text(encoding="ascii").strip() == PIPER_PACKAGE
    except (OSError, UnicodeError):
        ready = False
    if ready and worker.is_file() and _can_start(worker):
        return worker

    active = Path(sys.executable)
    if importlib.util.find_spec("piper") and importlib.util.find_spec("onnxruntime") and _can_start(active):
        return active
    return None


def install_piper(job: Job) -> Path:
    ready = piper_python()
    if ready is not None:
        return ready
    if sys.prefix == sys.base_prefix:
        raise SceneVoiceError("Open SceneVoice using its launcher to install Piper in the app's own environment.")

    # Python 3.15 lacks a compatible Windows ONNX Runtime wheel. Free-threaded
    # interpreters also need a separate runner for the current Piper ABI.
    native_error = None
    if sys.version_info[:2] < (3, 15) and not sysconfig.get_config_var("Py_GIL_DISABLED"):
        try:
            job.notify("Installing offline Piper in the editor environment...", None)
            job.run([*PIP_INSTALL, PIPER_PACKAGE], "Could not install Piper in the editor environment")
            job.run([sys.executable, "-I", "-m", "piper", "--help"], "Piper did not start")
            importlib.invalidate_caches()
            _can_start.cache_clear()
            return Path(sys.executable)
        except SceneVoiceError as exc:
            native_error = exc

    try:
        executable = "uv.exe" if os.name == "nt" else "uv"
        uv = Path(sysconfig.get_path("scripts")) / executable
        if not uv.is_file():
            job.notify("Installing the Piper setup tool...", None)
            job.run([*PIP_INSTALL, UV_PACKAGE], "Could not install the Piper setup tool")

        command = [uv, "--system-certs", "--no-progress"]
        python = worker_python()
        job.notify(f"Preparing a separate Python {WORKER_VERSION} environment for Piper...", None)
        job.run(
            [*command, "venv", "--python", WORKER_VERSION, "--allow-existing", WORKER_ENV],
            f"Could not prepare the Python {WORKER_VERSION} environment for Piper",
        )
        job.notify("Installing Piper and ONNX Runtime...", None)
        job.run(
            [*command, "pip", "install", "--python", python, "--only-binary", ":all:", PIPER_PACKAGE],
            "Could not install Piper in its separate environment",
        )
        job.run([python, "-I", "-m", "piper", "--help"], "Piper did not start in its separate environment")
        marker = WORKER_ENV / "scenevoice-piper.txt"
        job.commit(lambda: marker.write_text(PIPER_PACKAGE + "\n", encoding="ascii"))
        _can_start.cache_clear()
        return python
    except SceneVoiceError as exc:
        if native_error is not None:
            raise SceneVoiceError(f"{exc}\n\nThe editor environment also could not start Piper.\n{native_error}") from exc
        raise
