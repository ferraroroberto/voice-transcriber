"""`gui` subcommand — launch the classic main window."""

from __future__ import annotations

# Standard library imports
import argparse
import logging

from .base import BaseCommand

logger = logging.getLogger(__name__)


class GuiCommand(BaseCommand):
    @classmethod
    def add_parser(cls, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser("gui", help="Launch the main window")
        parser.add_argument(
            "--tray-on-close", action="store_true",
            help="Minimise to tray instead of exiting when the window is closed",
        )

    def execute(self, args: argparse.Namespace) -> int:
        from gui.app import run_gui  # lazy — tk isn't always present
        return run_gui(self.config, tray_on_close=args.tray_on_close)
