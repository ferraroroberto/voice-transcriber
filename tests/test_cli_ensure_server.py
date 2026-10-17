"""`BaseCommand._ensure_server` honours the hub-first record gate (#283).

Before the fix the CLI refused to record/transcribe whenever the local
whisper-server was down, even with the hub reachable and able to serve.
"""

from __future__ import annotations

# Standard library imports
import argparse
import logging

# Third-party imports
import pytest
import requests

from app.cli.commands.base import BaseCommand
from src import AppConfig
from src.whisper_server import OWNERSHIP_NONE, ServerStatus


class _Cmd(BaseCommand):
    @classmethod
    def add_parser(cls, subparsers):  # pragma: no cover
        ...

    def execute(self, args):  # pragma: no cover
        return 0


@pytest.fixture
def stopped_whisper(mocker):
    manager = mocker.patch("app.cli.commands.base.WhisperServerManager").return_value
    manager.status.return_value = ServerStatus(
        running=False, ownership=OWNERSHIP_NONE, base_url="http://127.0.0.1:8090",
    )
    return manager


def _args(start_server: bool = False) -> argparse.Namespace:
    return argparse.Namespace(start_server=start_server)


def _cmd() -> _Cmd:
    return _Cmd(AppConfig(transcribe_base_url="http://hub:8000"))


def test_hub_reachable_proceeds_without_starting_whisper(mocker, stopped_whisper):
    mocker.patch("src.transcription_client.requests.get")
    ensured = _cmd()._ensure_server(_args())
    assert ensured is not None
    _, spawned_here, status = ensured
    assert spawned_here is False
    assert status.base_url == "http://127.0.0.1:8090"
    stopped_whisper.start.assert_not_called()


def test_both_down_refuses_and_names_both(mocker, stopped_whisper, caplog):
    mocker.patch(
        "src.transcription_client.requests.get",
        side_effect=requests.ConnectionError("refused"),
    )
    with caplog.at_level(logging.ERROR):
        assert _cmd()._ensure_server(_args()) is None
    assert "http://hub:8000" in caplog.text
    assert "whisper-server is not running" in caplog.text
