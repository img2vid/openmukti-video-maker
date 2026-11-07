"""Record a mono PCM WAV without blocking PortAudio's input callback."""

from __future__ import annotations

import queue
import threading
import wave
from pathlib import Path

from .model import SceneVoiceError


def audio_module():
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        raise SceneVoiceError(
            "Microphone recording needs sounddevice and PortAudio. On Linux, install libportaudio2 "
            "or the equivalent package for your distribution."
        ) from exc
    return sounddevice


def microphones() -> list[tuple[int | None, str]]:
    sd = audio_module()
    return [(None, "System default")] + [
        (index, f"{device['name']} · {sd.query_hostapis(device['hostapi'])['name']}")
        for index, device in enumerate(sd.query_devices()) if device["max_input_channels"] > 0
    ]


class Recorder:
    def __init__(self, path: Path, device: int | None):
        self.path = path
        self.sd = audio_module()
        self.data: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
        self.issue = threading.Event()
        self.write_error: Exception | None = None
        self.stream = None
        try:
            info = self.sd.query_devices(device, kind="input")
            self.rate = round(info["default_samplerate"])
            self.stream = self.sd.RawInputStream(
                samplerate=self.rate, device=device, channels=1, dtype="int16", callback=self._capture
            )
        except Exception as exc:
            raise SceneVoiceError(f"Could not open the microphone. Check the selected device and OS permissions.\n\n{exc}") from exc
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.writer.start()
        try:
            self.stream.start()
        except Exception as exc:
            try:
                self.finish(discard=True)
            except SceneVoiceError:
                pass
            raise SceneVoiceError(f"Could not start the microphone. {exc}") from exc

    def _capture(self, frames, _count, _time, status) -> None:
        if status:
            self.issue.set()
        try:
            self.data.put_nowait(bytes(frames))
        except queue.Full:
            self.issue.set()

    def _write(self) -> None:
        try:
            with wave.open(str(self.path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(self.rate)
                while (chunk := self.data.get()) is not None:
                    audio.writeframesraw(chunk)
        except Exception as exc:
            self.write_error = exc

    def finish(self, *, discard: bool = False) -> float:
        problem = None
        try:
            self.stream.stop()
        except Exception as exc:
            problem = exc
        finally:
            try:
                self.stream.close()
            except Exception as exc:
                problem = problem or exc
            while self.writer.is_alive():
                try:
                    self.data.put(None, timeout=0.1)
                    break
                except queue.Full:
                    continue
            self.writer.join()

        if discard:
            self.path.unlink(missing_ok=True)
            return 0
        try:
            if problem or self.write_error:
                raise SceneVoiceError(f"Could not save the take. {problem or self.write_error}")
            if self.issue.is_set():
                raise SceneVoiceError("The microphone dropped samples. Try recording this take again.")
            with wave.open(str(self.path), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
            if duration < 0.1:
                raise SceneVoiceError("The take is too short. Record for at least a tenth of a second.")
            return duration
        except (SceneVoiceError, OSError, EOFError, wave.Error) as exc:
            self.path.unlink(missing_ok=True)
            raise SceneVoiceError(str(exc)) from exc
