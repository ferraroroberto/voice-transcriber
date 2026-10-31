"""The CLI `record` command runs takes through `handle_take`, so it gets the
same silence gate as the tk window and the tray: a silent take never reaches
whisper (which would hallucinate text on empty audio)."""

from __future__ import annotations

# Standard library imports
import argparse

# Third-party imports
import numpy as np
import pytest

from app.cli.commands.record import RecordCommand
from src import AppConfig
from src.recorder import Recording
from src.webapp_config import WebappConfig
from src.whisper_server import OWNERSHIP_NONE, ServerStatus


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        language=None, max_seconds=None, start_server=False, no_copy=True,
    )


@pytest.fixture
def command(mocker):
    cmd = RecordCommand(AppConfig())
    manager = mocker.Mock()
    status = ServerStatus(
        running=True, ownership=OWNERSHIP_NONE, base_url="http://127.0.0.1:8090",
    )
    mocker.patch.object(cmd, "_ensure_server", return_value=(manager, False, status))
    mocker.patch("app.cli.commands.record.load_webapp_config", return_value=WebappConfig())
    return cmd


def _fake_recording(mocker, samples: np.ndarray) -> None:
    recorder = mocker.patch("app.cli.commands.record.AudioRecorder").return_value
    recorder.record.return_value = Recording(
        samples=samples, sample_rate=16000, peak_level=0.0,
    )


def test_silent_take_never_reaches_whisper(mocker, command):
    _fake_recording(mocker, np.zeros(16000, dtype=np.int16))
    client = mocker.patch("app.cli.commands.record.build_transcription_client").return_value
    emit = mocker.patch.object(command, "_emit")

    assert command.execute(_args()) == 1
    client.transcribe_array.assert_not_called()
    emit.assert_not_called()


def test_audible_take_is_transcribed_and_emitted(mocker, command):
    _fake_recording(mocker, np.full(16000, 8000, dtype=np.int16))
    client = mocker.patch("app.cli.commands.record.build_transcription_client").return_value
    client.transcribe_array.return_value = "  hello world  "
    emit = mocker.patch.object(command, "_emit")

    assert command.execute(_args()) == 0
    emit.assert_called_once()
    assert emit.call_args.args[0] == "hello world"
