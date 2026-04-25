"""`tray` subcommand — resident tray icon + global hotkey."""

from __future__ import annotations

# Standard library imports
import argparse
import logging

from .base import BaseCommand

logger = logging.getLogger(__name__)


class TrayCommand(BaseCommand):
    @classmethod
    def add_parser(cls, subparsers: argparse._SubParsersAction) -> None:
        subparsers.add_parser(
            "tray",
            help="Run resident in the system tray with global hotkey",
        )

    def execute(self, args: argparse.Namespace) -> int:
        from gui.tray import run_tray  # lazy import — optional deps
        return run_tray(self.config)
