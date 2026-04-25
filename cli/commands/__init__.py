"""CLI subcommand registry."""

from .base import BaseCommand
from .gui_cmd import GuiCommand
from .record import RecordCommand
from .server_cmd import ServerCommand
from .transcribe import TranscribeCommand
from .tray_cmd import TrayCommand

COMMANDS = {
    "record": RecordCommand,
    "transcribe": TranscribeCommand,
    "gui": GuiCommand,
    "tray": TrayCommand,
    "server": ServerCommand,
}


def get_command(name: str):
    return COMMANDS.get(name)


__all__ = ["BaseCommand", "COMMANDS", "get_command"]
