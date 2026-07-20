"""Tests for `app/webapp/audio.py`'s ffmpeg transcode wrapper."""

from __future__ import annotations

# Standard library imports
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Local imports
from app.webapp import audio


def test_transcode_to_wav_suppresses_console_window_on_windows(tmp_path: Path) -> None:
    """`subprocess.run` must be called with CREATE_NO_WINDOW on win32 so
    the ffmpeg transcode — run once per dictation — never flashes a
    console window (issue #147)."""
    src = tmp_path / "in.webm"
    src.write_bytes(b"fake")
    dst = tmp_path / "out.wav"

    fake_result = MagicMock(returncode=0, stderr="")
    with patch.object(audio.subprocess, "run", return_value=fake_result) as mock_run, \
            patch.object(audio.sys, "platform", "win32"):
        audio.transcode_to_wav(src, dst, ffmpeg_path=Path("ffmpeg"))

    _, kwargs = mock_run.call_args
    assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW


def test_transcode_to_wav_no_creationflags_on_non_windows(tmp_path: Path) -> None:
    """Off Windows, `creationflags` must be a no-op (0) — the flag is
    Windows-only and undefined elsewhere."""
    src = tmp_path / "in.webm"
    src.write_bytes(b"fake")
    dst = tmp_path / "out.wav"

    fake_result = MagicMock(returncode=0, stderr="")
    with patch.object(audio.subprocess, "run", return_value=fake_result) as mock_run, \
            patch.object(audio.sys, "platform", "linux"):
        audio.transcode_to_wav(src, dst, ffmpeg_path=Path("ffmpeg"))

    _, kwargs = mock_run.call_args
    assert kwargs.get("creationflags") == 0
