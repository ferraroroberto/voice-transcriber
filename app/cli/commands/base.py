"""Base class for CLI subcommands."""

from __future__ import annotations

# Standard library imports
import argparse
import logging
import sys
from abc import ABC, abstractmethod
from typing import Optional, Tuple

# Third-party imports
import pyperclip

from src import AppConfig, resolve_iso
from src.whisper_server import OWNERSHIP_OURS, ServerStatus, WhisperServerManager

logger = logging.getLogger(__name__)


def language_type(value: str) -> str:
    """``argparse`` ``type=`` for ``--language`` — accepts anything
    :func:`src.resolve_iso` accepts (any of the 100 Whisper ISO codes,
    an English-name spelling, or a legacy mode name) and raises
    ``ArgumentTypeError`` naming the accepted forms otherwise.

    Shared by the ``record``/``transcribe`` subcommands in place of a
    ``choices=LANGUAGE_MODES`` allowlist, which rejected every language
    but the legacy three — ``resolve_iso`` (used downstream via
    ``AppConfig.whisper_language``) already accepts the full set
    (voice-transcriber#164).
    """
    if resolve_iso(value) is None:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a recognized language — pass a Whisper ISO "
            "code (e.g. 'en', 'es', 'haw', 'yue') or an English name "
            "(e.g. 'english', 'german')"
        )
    return value


class BaseCommand(ABC):
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @classmethod
    @abstractmethod
    def add_parser(cls, subparsers: argparse._SubParsersAction) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def execute(self, args: argparse.Namespace) -> int:  # pragma: no cover
        ...

    def _ensure_server(
        self, args: argparse.Namespace
    ) -> Optional[Tuple[WhisperServerManager, bool, ServerStatus]]:
        """Status → refuse-or-``--start-server`` → re-status preamble shared
        by the ``record``/``transcribe`` subcommands.

        Returns ``(manager, spawned_here, status)``, or ``None`` (having
        already logged the reason) when the server isn't running and
        wasn't allowed to be started — callers should treat that as exit
        code 2. Pair with :meth:`_release_server` in a ``finally`` block.
        """
        manager = WhisperServerManager()
        spawned_here = False
        status = manager.status()
        if not status.running:
            if not (args.start_server or self.config.auto_start_server):
                logger.error(
                    f"❌ Whisper server is not running at {status.base_url}. "
                    "Pass --start-server or set auto_start_server:true in config."
                )
                return None
            try:
                manager.start()
                spawned_here = True
            except RuntimeError as e:
                logger.error(str(e))
                return None
            status = manager.status()
        return manager, spawned_here, status

    def _release_server(self, manager: WhisperServerManager, spawned_here: bool) -> None:
        """Stop the whisper-server iff this command spawned it and still owns it."""
        if spawned_here and manager.status().ownership == OWNERSHIP_OURS:
            manager.stop()

    def _emit(self, text: str, args: argparse.Namespace) -> None:
        """Write ``text`` to stdout and, unless ``--no-copy``/config disabled
        it, copy it to the clipboard. Shared output tail for
        ``record``/``transcribe``."""
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        if not args.no_copy and self.config.auto_copy:
            try:
                pyperclip.copy(text)
                logger.info("📋 Copied to clipboard")
            except Exception as e:
                logger.warning(f"⚠️  Clipboard copy failed: {e}")
