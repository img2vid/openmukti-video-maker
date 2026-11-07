"""Render each scene against its audio clock, then assemble one video."""

from __future__ import annotations

import io
import json
import os
import tempfile
from fractions import Fraction
from pathlib import Path

from .jobs import Job
from .media_tools import Tools
from .media_tools import find_tools as find_tools
from .model import (
    PROFILES,
    ExportSettings,
    Project,
    Scene,
    SceneVoiceError,
    is_image,
    validate_export,
)
from .piper_runtime import piper_python
from .voices import Voice

SAMPLE_RATE = 48_000
CHANNELS = 2
SAMPLE_BYTES = 3 * CHANNELS
BACKGROUND = "#111827"
LOCAL_INPUT = ["-protocol_whitelist", "file,pipe", "-i"]


def ffmpeg(tools: Tools) -> list[str | Path]:
    return [
        tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-filter_threads", "2", "-filter_complex_threads", "2",
    ]


def check_project(project: Project, voices: dict[str, Voice]) -> list[str]:
    validate_export(project.export)
    if not project.scenes:
        return ["Add at least one scene."]
    problems = []
    for index, scene in enumerate(project.scenes, 1):
        label = f"Scene {index} · {scene.name}"
        visual = project.visual_for(scene)
        if not visual or not Path(visual).is_file():
            problems.append(f"{label} needs an existing video or image.")
        if scene.source == "piper":
            if not scene.script.strip():
                problems.append(f"{label} needs a narration script.")
            voice = voices.get(scene.voice)
            if voice is None:
                problems.append(f"{label} needs an installed Piper voice.")
            elif not 0 <= scene.speaker < len(voice.speakers):
                problems.append(f"{label} has an unavailable speaker ID.")
        elif scene.source == "file" and (not scene.audio or not Path(scene.audio).is_file()):
            problems.append(f"{label} needs an audio file.")
        elif scene.source == "recording" and (not scene.recording or not Path(scene.recording).is_file()):
            problems.append(f"{label} needs a recorded take.")
        if scene.extra_audio and not Path(scene.extra_audio).is_file():
            problems.append(f"{label} has a missing extra audio file.")
    return problems


def positive_fraction(value) -> Fraction | None:
    try:
        number = Fraction(str(value))
        return number if number > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def visual_duration(job: Job, tools: Tools, path: Path) -> tuple[int, Fraction]:
    prefix = [tools.ffprobe, "-v", "error"]
    stream_data = job.run(
        prefix + ["-show_streams", "-select_streams", "V:0", "-of", "json", *LOCAL_INPUT, path],
        "Could not inspect the video",
    )
    streams = json.loads(stream_data).get("streams", [])
    if not streams:
        raise SceneVoiceError("The selected visual file has no video stream.")
    stream = streams[0]
    stream_index = stream["index"]
    ticks = positive_fraction(stream.get("duration_ts"))
    base = positive_fraction(stream.get("time_base"))
    duration = ticks * base if ticks and base else positive_fraction(stream.get("duration"))
    if duration:
        return stream_index, duration

    # A container can outlast its video stream when it includes longer audio.
    # Read video packets when a stream duration is missing.
    first_pts = None
    last_pts = None
    last_duration = None
    latest_end = None

    def packet(line: str) -> None:
        nonlocal first_pts, last_pts, last_duration, latest_end
        values = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
        try:
            pts = Fraction(values.get("pts_time", ""))
        except (ValueError, ZeroDivisionError):
            return
        packet_duration = positive_fraction(values.get("duration_time"))
        first_pts = pts if first_pts is None else min(first_pts, pts)
        if last_pts is None or pts >= last_pts:
            last_pts = pts
            last_duration = packet_duration
        if packet_duration is not None:
            end = pts + packet_duration
            latest_end = end if latest_end is None else max(latest_end, end)

    job.run(
        prefix + ["-select_streams", "V:0", "-show_packets", "-show_entries",
                  "packet=pts_time,duration_time", "-of", "compact=p=0", *LOCAL_INPUT, path],
        "Could not measure the video duration", on_line=packet,
    )
    if first_pts is None or last_pts is None:
        raise SceneVoiceError("The video has no readable presentation timestamps.")
    step = 1 / (positive_fraction(stream.get("avg_frame_rate")) or Fraction(30))
    end = last_pts + (last_duration or step)
    if latest_end is not None:
        end = max(end, latest_end)
    return stream_index, end - first_pts


def fit_image(path: Path, size: tuple[int, int], fit: str):
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGBA")
            opaque = Image.new("RGBA", image.size, BACKGROUND)
            image = Image.alpha_composite(opaque, image).convert("RGB")
            method = ImageOps.fit if fit == "fill" else ImageOps.pad
            if fit == "fill":
                return method(image, size, method=Image.Resampling.LANCZOS)
            return method(image, size, method=Image.Resampling.LANCZOS, color=BACKGROUND)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise SceneVoiceError(f"Could not read image {path.name}. {exc}") from exc


def framing(width: int, height: int, fit: str) -> str:
    # Use display aspect ratio so anamorphic video keeps its intended shape.
    if fit == "fill":
        dimensions = f"w='max({width},ceil({height}*dar/2)*2)':h='max({height},ceil({width}/dar/2)*2)'"
        finish = f"crop={width}:{height}"
    else:
        dimensions = (
            f"w='max(2,trunc(min({width},{height}*dar)/2)*2)':"
            f"h='max(2,trunc(min({height},{width}/dar)/2)*2)'"
        )
        finish = f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x111827"
    return f"scale={dimensions}:flags=lanczos:out_color_matrix=bt709:out_range=tv,setsar=1,{finish}"


def thumbnail(path: Path, size: tuple[int, int], fit: str, tools: Tools | None):
    if is_image(path):
        return fit_image(path, size, fit)
    if tools is None:
        raise SceneVoiceError("Set up FFmpeg to preview this clip.")
    from PIL import Image

    png = Job().run(
        ffmpeg(tools) + [*LOCAL_INPUT, path, "-map", "0:V:0", "-vf", framing(*size, fit),
                         "-an", "-frames:v", "1", "-c:v", "png", "-threads", "1",
                         "-f", "image2pipe", "pipe:1"],
        "Could not preview this clip",
    )
    with Image.open(io.BytesIO(png)) as image:
        return image.copy()


def pcm_input(path: Path) -> list[str | Path]:
    return ["-f", "s24le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), *LOCAL_INPUT, path]


def to_pcm(job: Job, tools: Tools, audio: Path, output: Path) -> None:
    job.run(
        ffmpeg(tools) + [*LOCAL_INPUT, audio, "-map", "0:a:0", "-vn", "-sn", "-dn",
                         "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-c:a", "pcm_s24le",
                         "-f", "s24le", output],
        f"Could not decode audio {audio.name}",
    )
    if not output.stat().st_size:
        raise SceneVoiceError(f"The audio file {audio.name} contains no samples.")


def audio_parts(
    job: Job, tools: Tools, scene: Scene, voices: dict[str, Voice], work: Path
) -> list[Path]:
    if scene.source == "piper":
        python = piper_python()
        if python is None:
            raise SceneVoiceError("Open Setup and choose Set up Piper to install the speech runtime.")
        voice = voices[scene.voice]
        script = work / "script.txt"
        script.write_text(scene.script.strip() + "\n", encoding="utf-8")
        audio = work / "piper.wav"
        job.run(
            [python, "-I", "-m", "piper", "--model", voice.model, "--config", voice.config,
             "--output-file", audio, "--speaker", str(scene.speaker),
             "--length-scale", str(1 / scene.speech_rate), "--sentence-silence", "0.16",
             "--input-file", script],
            f"Piper could not speak this scene with {voice.key}",
        )
    else:
        audio = Path(scene.audio if scene.source == "file" else scene.recording)
    primary = work / "narration.pcm"
    to_pcm(job, tools, audio, primary)
    if not scene.extra_audio:
        return [primary]

    extra = work / "extra.pcm"
    to_pcm(job, tools, Path(scene.extra_audio), extra)
    if scene.extra_mode == "after":
        return [primary, extra]
    if scene.extra_mode == "before":
        return [extra, primary]
    if scene.mix_percent == 0:
        return [primary]

    mixed = work / "mixed.pcm"
    gain = scene.mix_percent / 100
    filters = (
        f"[1:a]volume={gain:.2f}[bed];"
        "[0:a][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95:level=false:latency=1"
    )
    job.run(
        ffmpeg(tools) + [*pcm_input(primary), *pcm_input(extra), "-filter_complex", filters,
                         "-c:a", "pcm_s24le", "-f", "s24le", mixed],
        "Could not mix narration and extra audio",
    )
    if mixed.stat().st_size != primary.stat().st_size:
        raise SceneVoiceError("The audio mix did not preserve the narration length.")
    return [mixed]


def video_encoder(settings: ExportSettings) -> tuple[list[str], str]:
    quality = settings.quality
    if settings.profile == "h264":
        crf = {"maximum": "14", "high": "17", "compact": "21"}[quality]
        return ["-c:v", "libx264", "-preset", "fast", "-crf", crf, "-bf", "0"], "yuv420p"
    if settings.profile == "hevc":
        crf = {"maximum": "16", "high": "19", "compact": "24"}[quality]
        return [
            "-c:v", "libx265", "-preset", "fast", "-crf", crf,
            "-x265-params", "pools=2:frame-threads=2:bframes=0:log-level=error", "-tag:v", "hvc1",
        ], "yuv420p"
    if settings.profile == "vp9":
        crf = {"maximum": "15", "high": "20", "compact": "29"}[quality]
        return [
            "-c:v", "libvpx-vp9", "-crf", crf, "-b:v", "0", "-deadline", "good",
            "-cpu-used", "3", "-row-mt", "1", "-lag-in-frames", "0", "-auto-alt-ref", "0",
        ], "yuv420p"
    return ["-c:v", "prores_ks", "-profile:v", "3"], "yuv422p10le"


def render_picture(
    job: Job, tools: Tools, visual: Path, settings: ExportSettings, frames: int,
    output: Path, work: Path, on_progress,
) -> None:
    width, height = settings.size
    target = Fraction(frames, settings.fps)
    encoder, pixel_format = video_encoder(settings)
    command = ffmpeg(tools) + ["-progress", "pipe:1", "-threads", "2"]
    if is_image(visual):
        image = work / "still.png"
        fit_image(visual, (width, height), settings.fit).save(image)
        command += ["-f", "image2", "-framerate", str(settings.fps), "-pattern_type", "none", *LOCAL_INPUT, image]
        stream = "0:v:0"
        filters = f"scale=iw:ih:out_color_matrix=bt709:out_range=tv,format={pixel_format},setsar=1"
        if frames > 1:
            filters += f",tpad=stop_mode=clone:stop={frames - 1}"
    else:
        stream_index, original = visual_duration(job, tools, visual)
        speed = float(original / target)
        ratio = target / original
        command += [*LOCAL_INPUT, visual]
        stream = f"0:{stream_index}"
        filters = (
            f"settb=AVTB,setpts=(PTS-STARTPTS)*{ratio.numerator}/{ratio.denominator},"
            f"fps=fps={settings.fps}:start_time=0:round=near,"
            f"{framing(width, height, settings.fit)},format={pixel_format},"
            "tpad=stop_mode=clone:stop=-1"
        )
        on_progress(0, f" · {speed:.2f}× clip speed")

    def report(line: str) -> None:
        if line.startswith("frame="):
            try:
                on_progress(min(1, int(line.split("=", 1)[1]) / frames), "")
            except ValueError:
                pass

    job.run(
        command + ["-map", stream, "-an", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
                   "-vf", filters, "-frames:v", str(frames), "-fps_mode", "cfr", *encoder, "-threads", "4",
                   "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
                   "-color_trc", "bt709", "-video_track_timescale", str(SAMPLE_RATE), output],
        f"Could not encode video {visual.name}",
        on_line=report,
    )
    count = job.run(
        [tools.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
         "-of", "default=noprint_wrappers=1:nokey=1", output],
        "Could not verify the rendered scene",
    )
    if count.strip() != str(frames).encode():
        raise SceneVoiceError(f"The visual produced an unexpected number of frames (expected {frames}).")


def concatenate(
    job: Job, tools: Tools, settings: ExportSettings, segments: list[tuple[Path, int]],
    master: Path, output: Path, work: Path,
) -> None:
    playlist = work / "scenes.ffconcat"
    lines = ["ffconcat version 1.0"]
    previous = total = 0
    for clip, frames in segments:
        total += frames
        boundary = round(Fraction(total * 1_000_000, settings.fps))
        micros = boundary - previous
        previous = boundary
        lines.extend((f"file '{clip.name}'", f"duration {micros // 1_000_000}.{micros % 1_000_000:06d}"))
    playlist.write_text("\n".join(lines) + "\n", encoding="ascii")

    if settings.profile == "prores":
        audio_options = ["-c:a", "pcm_s24le"]
    elif settings.profile == "vp9":
        audio_options = ["-c:a", "libopus", "-b:a", "256k"]
    else:
        audio_options = ["-c:a", "aac", "-b:a", "320k"]
    container_options = []
    if settings.profile != "vp9":
        container_options = [
            "-movflags", "+faststart", "-video_track_timescale", str(SAMPLE_RATE),
            "-movie_timescale", str(SAMPLE_RATE),
        ]
    if settings.profile == "hevc":
        container_options += ["-tag:v", "hvc1"]
    job.notify("Joining the scenes and writing the final video...", 0.98)
    job.run(
        ffmpeg(tools) + ["-f", "concat", "-safe", "1", *LOCAL_INPUT, playlist,
                         *pcm_input(master), "-map", "0:v:0", "-map", "1:a:0",
                         "-c:v", "copy", *audio_options, *container_options, output],
        "Could not write the final video",
    )


def append_audio(job: Job, master, parts: list[Path], frames: int, samples: int, fps: int) -> None:
    for part in parts:
        with part.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                job.check()
                master.write(chunk)
    padding = frames * (SAMPLE_RATE // fps) - samples
    master.write(b"\0" * padding * SAMPLE_BYTES)


def export_video(
    job: Job, tools: Tools, project: Project, voices: dict[str, Voice], destination: Path,
) -> Path:
    errors = check_project(project, voices)
    if errors:
        raise SceneVoiceError("\n".join(errors))
    destination = destination.resolve()
    settings = project.export
    if destination.suffix.lower() != PROFILES[settings.profile][1]:
        raise SceneVoiceError(f"Use a {PROFILES[settings.profile][1]} extension for this export format.")
    for scene in project.scenes:
        assets = (scene.visual, scene.audio, scene.recording, scene.extra_audio, project.cover)
        for asset in filter(None, assets):
            source = Path(asset).resolve()
            if destination == source or (destination.exists() and source.is_file() and destination.samefile(source)):
                raise SceneVoiceError("Choose a different output path from your source media.")

    with tempfile.TemporaryDirectory(prefix=".scenevoice-render-", dir=destination.parent) as scratch:
        work = Path(scratch)
        master = work / "soundtrack.pcm"
        segments = []
        count = len(project.scenes)
        span = 0.96 / count
        samples_per_frame = SAMPLE_RATE // settings.fps
        with master.open("wb") as soundtrack:
            for index, scene in enumerate(project.scenes):
                job.check()
                heading = f"Scene {index + 1}/{count} · {scene.name}"
                base = 0.96 * index / count
                job.notify(f"{heading} · Preparing audio", base)
                try:
                    with tempfile.TemporaryDirectory(prefix="audio-", dir=work) as scene_work:
                        parts = audio_parts(job, tools, scene, voices, Path(scene_work))
                        samples = sum(part.stat().st_size for part in parts) // SAMPLE_BYTES
                        if not samples:
                            raise SceneVoiceError("The scene has no audible samples.")
                        frames = (samples + samples_per_frame - 1) // samples_per_frame
                        append_audio(job, soundtrack, parts, frames, samples, settings.fps)
                    extension = ".mov" if settings.profile == "prores" else ".mp4"
                    clip = work / f"scene-{index:06d}{extension}"
                    job.notify(f"{heading} · Rendering {float(Fraction(frames, settings.fps)):.2f}s", base + span * 0.2)
                    def progress(fraction, detail, label=heading, offset=base):
                        job.notify(f"{label} · Rendering{detail}", offset + span * (0.2 + 0.8 * fraction))

                    render_picture(job, tools, Path(project.visual_for(scene)), settings, frames, clip, work, progress)
                    segments.append((clip, frames))
                except SceneVoiceError as exc:
                    raise SceneVoiceError(f"{heading}\n\n{exc}") from exc
        staged = work / ("finished" + destination.suffix.lower())
        concatenate(job, tools, settings, segments, master, staged, work)
        if not staged.is_file() or not staged.stat().st_size:
            raise SceneVoiceError("The export did not produce a video.")
        job.commit(lambda: os.replace(staged, destination))

    job.notify("Video exported", 1.0)
    return destination
