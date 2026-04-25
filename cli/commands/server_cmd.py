"""`server` subcommand — control the shared whisper-server."""

from __future__ import annotations

# Standard library imports
import argparse
import logging

from whisper_server import WhisperServerManager
from .base import BaseCommand

logger = logging.getLogger(__name__)


class ServerCommand(BaseCommand):
    @classmethod
    def add_parser(cls, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser("server", help="Manage the local whisper-server")
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("start", help="Start the server (no-op if already running)")
        sub.add_parser("stop", help="Stop the server (only if we own it)")
        sub.add_parser("status", help="Show server status")
        logs = sub.add_parser("logs", help="Print the captured server log")
        logs.add_argument("--tail", type=int, default=40, help="Lines to show (default: 40)")

    def execute(self, args: argparse.Namespace) -> int:
        manager = WhisperServerManager()
        action = args.action

        if action == "status":
            return _print_status(manager)
        if action == "start":
            try:
                manager.start()
            except RuntimeError as e:
                logger.error(str(e))
                return 1
            return _print_status(manager)
        if action == "stop":
            manager.stop()
            return _print_status(manager)
        if action == "logs":
            lines = manager.log_lines()
            for line in lines[-args.tail:]:
                logger.info(line)
            return 0
        return 1


def _print_status(manager: WhisperServerManager) -> int:
    s = manager.status()
    icon = "✅" if s.running else "🔴"
    pid = f" pid={s.pid}" if s.pid else ""
    logger.info(f"{icon} {s.detail} @ {s.base_url}{pid} [{s.ownership}]")
    # Transparency: always show the configured model and, when available,
    # the live runtime info parsed from whisper.cpp's startup log.
    description = manager.describe(status=s)
    for line in description.multiline():
        logger.info(line)
    return 0 if s.running else 1
