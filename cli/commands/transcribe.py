"""`transcribe` subcommand — transcribe an existing audio file."""

from __future__ import annotations

# Standard library imports
import argparse
import logging
import sys
from pathlib import Path

# Third-party imports
import pyperclip

from core import TranscriptionClient, TranscriptionError
from core.app_config import LANGUAGE_MODES
from whisper_server import WhisperServerManager
from .base import BaseCommand

logger = logging.getLogger(__name__)


class TranscribeCommand(BaseCommand):
    @classmethod
    def add_parser(cls, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(
            "transcribe",
            help="Transcribe an existing audio file",
        )
        parser.add_argument("file", type=Path, help="Path to an audio file (wav/mp3/m4a/...)")
        parser.add_argument(
            "--language", type=str, choices=LANGUAGE_MODES, default=None,
            help="Dictation mode (overrides config)",
        )
        parser.add_argument("--no-copy", action="store_true", help="Do not copy to clipboard")
        parser.add_argument("--start-server", action="store_true")

    def execute(self, args: argparse.Namespace) -> int:
        manager = WhisperServerManager()
        status = manager.status()
        if not status.running:
            if not (args.start_server or self.config.auto_start_server):
                logger.error(
                    f"❌ Whisper server is not running at {status.base_url}. "
                    "Pass --start-server or set auto_start_server:true."
                )
                return 2
            try:
                manager.start()
            except RuntimeError as e:
                logger.error(str(e))
                return 2
            status = manager.status()

        if args.language:
            self.config.language = args.language
        iso_lang = self.config.whisper_language

        client = TranscriptionClient(status.base_url)
        try:
            text = client.transcribe_file(args.file, language=iso_lang)
        except TranscriptionError as e:
            logger.error(f"❌ {e}")
            return 1

        sys.stdout.write(text + "\n")
        sys.stdout.flush()

        if not args.no_copy and self.config.auto_copy:
            try:
                pyperclip.copy(text)
                logger.info("📋 Copied to clipboard")
            except Exception as e:
                logger.warning(f"⚠️  Clipboard copy failed: {e}")

        return 0
