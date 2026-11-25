"""Audio transcoding helpers — webm/opus → 16 kHz mono WAV via ffmpeg.

Whisper.cpp's `whisper-server` uses miniaudio for decoding, which does
not support webm/opus. The browser's `MediaRecorder` produces webm/opus
by default. So we shell out to `ffmpeg` to bridge the two formats.

`ffmpeg` is detected via PATH, then `vendor/ffmpeg/ffmpeg.exe`. If
neither is present, `transcode_to_wav` raises `AudioToolMissing` with
a clear install hint.

A take that is already 16 kHz mono 16-bit PCM WAV (the browser's PCM capture)
needs no conversion, so it is copied without ffmpeg and a PC without ffmpeg can
still transcribe it.
"""

from __future__ import annotations

# Standard library imports
import logging
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path
from typing import Optional

from src import runtime_files
from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 16000
# ffmpeg transcodes far faster than real time; 15 min covers hours of speech on a
# loaded box and still frees the request worker if ffmpeg wedges on a bad file.
FFMPEG_TIMEOUT_S = 900


# The fmt and data chunks sit within the first few hundred bytes of a real WAV,
# so a longer search means "not a WAV".
_WAV_HEADER_SCAN_BYTES = 4096
_WAV_PCM_FORMAT = 1
_WAV_OPEN_ENDED_SIZE = 0xFFFFFFFF
_WAV_BYTES_PER_FRAME = 2  # mono, 16-bit
_COPY_CHUNK_BYTES = 1 << 20


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


def _scan_pcm_wav(src: Path, sample_rate: int) -> Optional[tuple[int, int]]:
    """Return ``(data_offset, data_bytes)`` when ``src`` is mono 16-bit PCM WAV at ``sample_rate``.

    The format comes from the file header, never the extension. A data length
    of ``0xFFFFFFFF`` or 0, or one running past the end of the file, marks an
    open-ended streamed take: the length is then the rest of the file, whole
    frames only. ``None`` means "not this shape" and the caller transcodes.
    """
    try:
        file_size = src.stat().st_size
        with open(src, "rb") as fh:
            head = fh.read(_WAV_HEADER_SCAN_BYTES)
    except OSError:
        return None
    if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None

    pos = 12
    fmt_ok = False
    while pos + 8 <= len(head):
        chunk_id = head[pos:pos + 4]
        (chunk_size,) = struct.unpack("<I", head[pos + 4:pos + 8])
        body = pos + 8
        if chunk_id == b"fmt ":
            if chunk_size < 16 or body + 16 > len(head):
                return None
            tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", head[body:body + 16])
            fmt_ok = (
                tag == _WAV_PCM_FORMAT and channels == 1 and bits == 16 and rate == sample_rate
            )
            if not fmt_ok:
                return None
        elif chunk_id == b"data":
            if not fmt_ok:
                return None
            declared = chunk_size
            remaining = file_size - body
            if declared in (0, _WAV_OPEN_ENDED_SIZE) or declared > remaining:
                declared = remaining
            return body, declared - declared % _WAV_BYTES_PER_FRAME
        # Chunks are word-aligned: a pad byte follows an odd size.
        pos = body + chunk_size + (chunk_size & 1)
    return None


def _copy_pcm_wav(src: Path, dst: Path, data_offset: int, data_bytes: int, sample_rate: int) -> None:
    """Write ``dst`` as a closed canonical 44-byte-header WAV holding ``src``'s PCM."""
    header = (
        b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVE"
        + b"fmt " + struct.pack(
            "<IHHIIHH", 16, _WAV_PCM_FORMAT, 1, sample_rate,
            sample_rate * _WAV_BYTES_PER_FRAME, _WAV_BYTES_PER_FRAME, 16,
        )
        + b"data" + struct.pack("<I", data_bytes)
    )
    written = 0
    with open(src, "rb") as fin, runtime_files.open_for_write(dst, "wb") as fout:
        fout.write(header)
        fin.seek(data_offset)
        while written < data_bytes:
            block = fin.read(min(_COPY_CHUNK_BYTES, data_bytes - written))
            if not block:
                break
            fout.write(block)
            written += len(block)
        if written < data_bytes:
            # The file shrank under us: keep the header honest about what landed.
            fout.seek(4)
            fout.write(struct.pack("<I", 36 + written))
            fout.seek(40)
            fout.write(struct.pack("<I", written))


def transcode_to_wav(
    src: Path,
    dst: Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    ffmpeg_path: Optional[Path] = None,
) -> Path:
    """Transcode any input file to 16 kHz mono PCM WAV at ``dst``.

    Overwrites ``dst`` if it exists. A take that is already mono 16-bit PCM WAV
    at ``sample_rate`` is copied with a closed header instead, so it needs no ffmpeg.
    """
    pcm = _scan_pcm_wav(src, sample_rate)
    if pcm is not None:
        logger.debug(f"🎞️  {src.name} is already {sample_rate} Hz mono PCM WAV, skipping ffmpeg")
        _copy_pcm_wav(src, dst, pcm[0], pcm[1], sample_rate)
        return dst

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
            creationflags=NO_WINDOW,
            timeout=FFMPEG_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise AudioTranscodeError(f"ffmpeg timed out after {FFMPEG_TIMEOUT_S}s transcoding {src.name}") from None
    except OSError as exc:
        raise AudioTranscodeError(f"ffmpeg failed to launch: {exc}") from exc

    if result.returncode != 0:
        raise AudioTranscodeError(
            f"ffmpeg exited {result.returncode}: {result.stderr.strip()[:500]}"
        )
    return dst


def wav_duration_seconds(path: Path) -> Optional[float]:
    """Length of a PCM WAV in seconds (frames / rate), or ``None``.

    ``None`` means "could not tell" (missing or unreadable file), never zero:
    a caller must not log a fabricated duration for a take it could not measure.
    """
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            frames = wf.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        logger.warning(f"⚠️  Could not read duration of {path}: {exc}")
        return None
    if rate <= 0:
        return None
    return frames / rate
