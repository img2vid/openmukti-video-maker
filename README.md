# SceneVoice Studio 1.0.2

SceneVoice Studio turns an ordered storyboard into a narrated video. Each scene pairs a video or still image with Piper speech, an imported audio file, or a microphone take. The editor fits the clip or still image to the length of the scene audio and joins the scenes into one export.

## Install and launch

Extract the whole ZIP to a writable folder before launching. Use 64-bit Python 3.11 or newer with Tk. The editor supports Python 3.15. The launchers install the editor packages in a virtual environment beside the app on first use.

Piper setup opens on first launch. With Python 3.15, SceneVoice prepares a separate Python 3.14 environment for Piper and ONNX Runtime. You can keep your existing Python installation. Setup needs an internet connection once. After setup and voice installation, narration and export work offline.

### Windows

1. Use your current 64-bit Python 3.15 installation with Tcl/Tk support, or install Python 3.11 or newer. [Python for Windows](https://www.python.org/downloads/windows/)
2. Double-click **Start-Windows.cmd**. On Windows x64, SceneVoice finds an existing FFmpeg and FFprobe pair or downloads and verifies its own copy. The one-time download is about 110 MiB. SceneVoice uses the installed tools offline for previews and exports.
3. If setup is interrupted, open **Setup → Install FFmpeg and FFprobe** to retry. To use a specific pair, choose **Setup → Choose FFmpeg folder** and select the folder containing both executables.

SceneVoice downloads the Windows FFmpeg 9.0.2 Essentials build directly from its publisher. **Setup** shows the active FFmpeg and FFprobe paths. [Windows build](https://www.gyan.dev/ffmpeg/builds/) · [FFmpeg source](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz)

### Ubuntu, Debian, and Lubuntu

Install the platform packages and run the launcher from the extracted folder.

```sh
sudo apt install python3 python3-tk python3-venv ffmpeg libportaudio2
sh start.sh
```

On another Linux distribution, install the corresponding Python, Tk, venv, FFmpeg, and PortAudio packages. Use `SCENEVOICE_PYTHON=/path/to/python3 sh start.sh` to select a particular Python installation.

### macOS

Use Python 3.11 or newer with Tk support and an FFmpeg build with FFprobe. Open **Start-macOS.command** or run `sh start.sh` from the extracted folder. If you use Homebrew, make sure its binary directory is on your PATH, or choose the FFmpeg binary folder in **Setup**. [Python for macOS](https://www.python.org/downloads/macos/) · [FFmpeg downloads](https://ffmpeg.org/download.html)

## Make your first video

1. Click **Add media** and select your images and clips. Each file becomes one scene. Use **Blank scene** for a scene that will use the podcast cover or a visual you will pick later. Use **Up** and **Down** to set the final order.
2. Enter the text for each scene. For Piper speech, open **Piper voices**, click **Refresh online catalog**, find a voice for your language, and click **Download selected**. Choose **Use in this scene** or select an installed voice from the editor. Multi-speaker models expose a speaker selector. The **Rate** control adjusts Piper's speech speed before the visual is matched to the result.
3. For your own narration, switch **Narration** to **Imported audio** and choose a file, or record a microphone take. Add **Extra audio** to place music or another recording before, after, or underneath the primary audio. Underneath mode uses the narration length. A short bed ends naturally and a long bed is cut at the end of the narration. Adjust its gain with the percentage field.
4. Choose the canvas and framing. **Fit with bars** keeps the whole visual and adds dark borders when needed. **Fill and crop** fills the canvas with a centered crop. **Preview scene** opens a 720p draft of the selected scene in your default video player.
5. Save your project and click **Export video**. Pick the format, resolution, frame rate, and output file. Existing audio in a visual clip is replaced by the scene's chosen audio. To use that audio, select the clip as an imported or extra audio file.

## Record with the teleprompter

**Record this scene** opens the selected script. **Guided recording** begins at scene one and presents one script at a time.

1. Choose a microphone. Adjust the reading size with **A-** and **A+** and the auto-scroll speed with the slider. You can also scroll manually or turn auto-scroll off.
2. Click **Start recording**. The microphone starts after a three-second countdown. Read the displayed part and click **Stop recording**.
3. **Listen** opens the take in your default audio player. Keep the take or record again. In guided mode **Keep & next** attaches the take and advances to the next script. Use **Back** or **Skip** to revisit or leave a scene.
4. A kept take becomes that scene's narration source. Save the project to keep the takes beside it in a folder ending in `.assets`.

If your system cannot open a microphone, check the device selection and your operating system's microphone permissions. Linux microphone input needs PortAudio, such as the `libportaudio2` package.

## Make a video podcast

Click **Podcast cover**, choose a still image, and keep **Use this cover for every scene** selected. Add one blank scene per section of the podcast, then use Piper, audio files, or microphone takes for the sections. Export normally. The cover remains visible while the sections play. You can also use a single image scene with a single long audio file.

## Export choices

| Format | Video and audio | Use |
| --- | --- | --- |
| MP4 | H.264 and 320 kbps AAC | Widely playable video |
| MP4 | H.265 / HEVC and 320 kbps AAC | Smaller high-quality video on compatible players |
| WebM | VP9 and 256 kbps Opus | Web playback and distribution |
| MOV | ProRes 422 HQ and 24-bit PCM | High-bitrate master for editing |

Choose 720, 1080, 1440, or 2160 pixels on the short edge and 24, 25, 30, 50, or 60 frames per second. At 2160 the canvases are 3840 × 2160, 2160 × 3840, and 2160 × 2160 for landscape, portrait, and square respectively. Exports use SDR and stereo 48 kHz audio. ProRes has a fixed HQ preset. The other profiles offer Maximum, High, and Compact quality.

Video edits are aligned to whole frames. Each scene preserves its audio and can add less than one frame of silence at the end to align the next cut. The app uses temporary disk space next to the export. Longer videos and ProRes masters can need substantial space.

## Work offline and move projects

- Voice download and package setup use the internet. Speech generation, recording, and exporting run locally. If you already have a Piper model, use **Import local voice** and select its `.onnx` file with the corresponding `.onnx.json` file beside it. Use each model's card to check its license. [Piper voice catalog](https://huggingface.co/rhasspy/piper-voices)
- If the Piper setup is cancelled or a download fails, use **Setup → Set up Piper** to retry. Imported audio and microphone takes can be used while Piper setup is pending.
- SceneVoice supports still PNG, JPEG, WebP, BMP, TIFF, GIF, and AVIF files. Animated image files used as stills show their first frame. Video and audio decoding depend on your FFmpeg build.
- Keep the source videos, images, and imported audio available. Projects reference those files, while saved microphone takes are copied into the project's `.assets` folder. When moving a project, bring its source files and the `.assets` folder and keep their relative locations when possible. Reinstall the same Piper voices on the destination computer.
- If an encoder is unavailable, use an FFmpeg build with `libx264`, `libx265`, `libvpx-vp9`, `prores_ks`, and `libopus` for all export options.

[Piper](https://github.com/OHF-Voice/piper1-gpl) · [FFmpeg](https://ffmpeg.org/) · [Pillow](https://python-pillow.org/) · [sounddevice](https://python-sounddevice.readthedocs.io/)
