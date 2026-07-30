"""`transcribe` subcommand — transcribe an existing audio file."""

from __future__ import annotations

# Standard library imports
import argparse
import logging
from pathlib import Path

from src import TranscriptionError, build_transcription_client
from .base import BaseCommand, language_type

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
            "--language", type=language_type, default=None,
            help="Dictation mode: a Whisper ISO code (en, es, haw, yue, "
            "...) or English name (overrides config)",
        )
        parser.add_argument("--no-copy", action="store_true", help="Do not copy to clipboard")
        parser.add_argument("--start-server", action="store_true")

    def execute(self, args: argparse.Namespace) -> int:
        ensured = self._ensure_server(args)
        if ensured is None:
            return 2
        manager, spawned_here, status = ensured

        if args.language:
            self.config.language = args.language
        iso_lang = self.config.whisper_language

        client = build_transcription_client(self.config, status.base_url)
        try:
            text = client.transcribe_file(args.file, language=iso_lang)
        except TranscriptionError as e:
            logger.error(f"❌ {e}")
            return 1
        finally:
            self._release_server(manager, spawned_here)

        self._emit(text, args)
        return 0
