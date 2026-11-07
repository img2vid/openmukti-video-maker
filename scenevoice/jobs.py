"""Cancellable child process execution shared by render and voice jobs."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from .model import SceneVoiceError


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, notify: Callable[[str, float | None], None] | None = None):
        self.notify = notify or (lambda _message, _progress: None)
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    def check(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled

    def cancel(self) -> None:
        with self._lock:
            self.cancelled.set()
            if self._process and self._process.poll() is None:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass

    def commit(self, action: Callable[[], None]) -> None:
        """Keep cancellation and the final atomic rename ordered."""
        with self._lock:
            self.check()
            action()

    def run(
        self,
        command: list[str | Path],
        label: str,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> bytes:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with tempfile.TemporaryFile() as errors:
            with self._lock:
                self.check()
                try:
                    process = subprocess.Popen(
                        [str(arg) for arg in command],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=errors,
                        creationflags=flags,
                    )
                except OSError as exc:
                    raise SceneVoiceError(f"{label}. {exc}") from exc
                self._process = process
            try:
                with process:
                    try:
                        if on_line:
                            for line in process.stdout:
                                on_line(line.decode("utf-8", errors="replace").strip())
                            output = b""
                        else:
                            output = process.stdout.read()
                    except BaseException:
                        if process.poll() is None:
                            process.kill()
                        raise
                    code = process.wait()
            finally:
                with self._lock:
                    self._process = None
            self.check()
            if code:
                errors.seek(max(0, errors.seek(0, os.SEEK_END) - 5000))
                detail = errors.read().decode("utf-8", errors="replace").strip()
                raise SceneVoiceError(f"{label}.\n\n{detail or f'Exit code {code}.'}")
            return output
