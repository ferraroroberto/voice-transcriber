"""`server stop` exits 0 once the stop has run; only `status` maps to running."""

from __future__ import annotations

# Standard library imports
import argparse

# Local imports
from app.cli.commands.server_cmd import ServerCommand
from src import AppConfig
from src.whisper_server import OWNERSHIP_NONE, ServerStatus


def _manager(mocker):
    manager = mocker.patch("app.cli.commands.server_cmd.WhisperServerManager").return_value
    manager.status.return_value = ServerStatus(
        running=False, ownership=OWNERSHIP_NONE, base_url="http://127.0.0.1:8090",
    )
    return manager


def test_stop_exits_zero_when_the_server_is_down_afterwards(mocker):
    manager = _manager(mocker)
    assert ServerCommand(AppConfig()).execute(argparse.Namespace(action="stop")) == 0
    manager.stop.assert_called_once_with()


def test_status_still_reports_not_running_as_nonzero(mocker):
    _manager(mocker)
    assert ServerCommand(AppConfig()).execute(argparse.Namespace(action="status")) == 1
