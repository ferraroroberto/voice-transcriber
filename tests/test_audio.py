"""Tests for `app/webapp/audio.py`'s ffmpeg transcode wrapper."""

from __future__ import annotations

# Standard library imports
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

# Third-party imports
import pytest

# Local imports
from app.webapp import audio


def test_transcode_to_wav_passes_shared_no_window_flag(tmp_path: Path) -> None:
    """`subprocess.run` must be called with the shared `NO_WINDOW` flag so
    the ffmpeg transcode — run once per dictation — never flashes a
    console window on Windows (issue #147). The platform branch itself is
    `src/no_window.py`'s responsibility, consolidated there instead of
    re-derived per call site (issue #243)."""
    src = tmp_path / "in.webm"
    src.write_bytes(b"fake")
    dst = tmp_path / "out.wav"

    fake_result = MagicMock(returncode=0, stderr="")
    with patch.object(audio.subprocess, "run", return_value=fake_result) as mock_run:
        audio.transcode_to_wav(src, dst, ffmpeg_path=Path("ffmpeg"))

    _, kwargs = mock_run.call_args
    assert kwargs.get("creationflags") == audio.NO_WINDOW


# --- PCM WAV passthrough: no ffmpeg for a take that is already 16 kHz mono ---

RATE = 16000
OPEN_ENDED = 0xFFFFFFFF


def _pcm(frames: int = RATE) -> bytes:
    return struct.pack("<h", 1000) * frames


def _wav_bytes(pcm: bytes, *, rate: int = RATE, channels: int = 1, bits: int = 16,
               riff_size: int | None = None, data_size: int | None = None,
               extra_chunk: bool = False) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * bits // 8,
                      channels * bits // 8, bits)
    out = b"RIFF" + struct.pack("<I", 36 + len(pcm) if riff_size is None else riff_size) + b"WAVE"
    out += b"fmt " + struct.pack("<I", 16) + fmt
    if extra_chunk:
        out += b"LIST" + struct.pack("<I", 5) + b"abcde" + b"\x00"  # odd size + pad byte
    out += b"data" + struct.pack("<I", len(pcm) if data_size is None else data_size)
    return out + pcm


@pytest.fixture
def no_ffmpeg(monkeypatch):
    monkeypatch.setattr(audio, "find_ffmpeg", lambda *_a, **_k: None)
    boom = MagicMock(side_effect=AssertionError("ffmpeg must not run"))
    monkeypatch.setattr(audio.subprocess, "run", boom)
    return boom


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"extra_chunk": True},
        {"riff_size": OPEN_ENDED, "data_size": OPEN_ENDED},
        {"riff_size": 0, "data_size": 0},
    ],
    ids=["closed", "extra-chunk", "open-ended-ffff", "open-ended-zero"],
)
def test_pcm_wav_take_skips_ffmpeg(tmp_path: Path, no_ffmpeg, kwargs) -> None:
    src = tmp_path / "take.bin"  # extension is irrelevant: the header decides
    src.write_bytes(_wav_bytes(_pcm(), **kwargs))
    dst = tmp_path / "out.wav"

    audio.transcode_to_wav(src, dst)

    no_ffmpeg.assert_not_called()
    assert audio.wav_duration_seconds(dst) == pytest.approx(1.0)
    with wave.open(str(dst), "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, RATE)
        assert wf.readframes(wf.getnframes()) == _pcm()


def test_growing_open_ended_snapshot_is_cut_to_whole_frames(tmp_path: Path, no_ffmpeg) -> None:
    """The partial worker copies a WAV that is still being written, so the cut can land mid-frame."""
    full = _wav_bytes(_pcm(RATE), riff_size=OPEN_ENDED, data_size=OPEN_ENDED)
    src = tmp_path / "snap.webm"
    src.write_bytes(full[:-3])  # an odd number of PCM bytes
    dst = tmp_path / "out.wav"

    audio.transcode_to_wav(src, dst)

    assert audio.wav_duration_seconds(dst) == pytest.approx((RATE - 2) / RATE)


def test_closed_header_longer_than_file_is_clamped_to_the_file(tmp_path: Path, no_ffmpeg) -> None:
    src = tmp_path / "t.wav"
    src.write_bytes(_wav_bytes(_pcm(100), data_size=RATE * 2))
    dst = tmp_path / "out.wav"

    audio.transcode_to_wav(src, dst)

    assert audio.wav_duration_seconds(dst) == pytest.approx(100 / RATE)


@pytest.mark.parametrize(
    "wav",
    [
        _wav_bytes(_pcm(), rate=44100),
        _wav_bytes(_pcm(), channels=2),
        _wav_bytes(_pcm(), bits=8),
        b"\x1a\x45\xdf\xa3" + b"webm-bytes" * 10,
        b"RIFF",
    ],
    ids=["wrong-rate", "stereo", "8-bit", "webm", "truncated-header"],
)
def test_other_shapes_still_go_through_ffmpeg(tmp_path: Path, wav: bytes) -> None:
    src = tmp_path / "in.wav"
    src.write_bytes(wav)
    dst = tmp_path / "out.wav"
    fake = MagicMock(returncode=0, stderr="")
    with patch.object(audio.subprocess, "run", return_value=fake) as mock_run:
        audio.transcode_to_wav(src, dst, ffmpeg_path=Path("ffmpeg"))
    mock_run.assert_called_once()


def test_other_shape_without_ffmpeg_still_reports_it_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(audio, "find_ffmpeg", lambda *_a, **_k: None)
    src = tmp_path / "in.webm"
    src.write_bytes(b"\x1a\x45\xdf\xa3webm")
    with pytest.raises(audio.AudioToolMissing):
        audio.transcode_to_wav(src, tmp_path / "out.wav")
