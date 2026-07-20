"""Audio transcoding helpers — webm/opus → 16 kHz mono WAV via ffmpeg.

Whisper.cpp's `whisper-server` uses miniaudio for decoding, which does
not support webm/opus. The browser's `MediaRecorder` produces webm/opus
by default. So we shell out to `ffmpeg` to bridge the two formats.

`ffmpeg` is detected via PATH, then `vendor/ffmpeg/ffmpeg.exe`. If
neither is present, `transcode_to_wav` raises `AudioToolMissing` with
a clear install hint.
"""

from __future__ import annotations

# Standard library imports
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 16000


class AudioToolMissing(RuntimeError):
    """Raised when ffmpeg cannot be located on the system."""


class AudioTranscodeError(RuntimeError):
    """Raised when ffmpeg returns non-zero."""


def find_ffmpeg(project_root: Optional[Path] = None) -> Optional[Path]:
    """Return the absolute path to a usable ffmpeg, or ``None``."""
    on_path = shutil.which("ffmpeg")
    if on_path:
        return Path(on_path)

    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent
    bundled = project_root / "vendor" / "ffmpeg" / "ffmpeg.exe"
    if not bundled.exists() and sys.platform != "win32":
        bundled = project_root / "vendor" / "ffmpeg" / "ffmpeg"
    if bundled.exists():
        return bundled
    return None


def transcode_to_wav(
    src: Path,
    dst: Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    ffmpeg_path: Optional[Path] = None,
) -> Path:
    """Transcode any input file to 16 kHz mono PCM WAV at ``dst``.

    Overwrites ``dst`` if it exists.
    """
    tool = ffmpeg_path or find_ffmpeg()
    if tool is None:
        raise AudioToolMissing(
            "ffmpeg not found. Install via `winget install Gyan.FFmpeg` "
            "or drop `ffmpeg.exe` into `vendor/ffmpeg/`."
        )

    cmd = [
        str(tool),
        "-y",
        "-loglevel", "error",
        "-i", str(src),
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(dst),
    ]
    logger.debug(f"🎞️  ffmpeg {' '.join(cmd[1:])}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except OSError as exc:
        raise AudioTranscodeError(f"ffmpeg failed to launch: {exc}") from exc

    if result.returncode != 0:
        raise AudioTranscodeError(
            f"ffmpeg exited {result.returncode}: {result.stderr.strip()[:500]}"
        )
    return dst
