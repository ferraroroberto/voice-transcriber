"""Tests for `app/webapp/audio.py`'s ffmpeg transcode wrapper."""

from __future__ import annotations

# Standard library imports
from pathlib import Path
from unittest.mock import MagicMock, patch

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
