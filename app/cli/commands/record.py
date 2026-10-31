"""`record` subcommand — record → transcribe → copy → exit."""

from __future__ import annotations

# Standard library imports
import argparse
import logging
import sys
import threading
from typing import Optional

from src import (
    AudioRecorder,
    RecordingError,
    TranscriptionError,
    build_transcription_client,
)
from src.recording_pipeline import TakeResult, handle_take
from src.webapp_config import load_webapp_config
from .base import BaseCommand, language_type

logger = logging.getLogger(__name__)


class RecordCommand(BaseCommand):
    @classmethod
    def add_parser(cls, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(
            "record",
            help="Record from mic, transcribe, copy to clipboard, exit",
        )
        parser.add_argument(
            "--language", type=language_type, default=None,
            help="Dictation mode: a Whisper ISO code (en, es, haw, yue, "
            "...) or English name (overrides config)",
        )
        parser.add_argument(
            "--max-seconds", type=int, default=None,
            help=f"Cap recording length (default: from config)",
        )
        parser.add_argument(
            "--start-server", action="store_true",
            help="Start the whisper-server if not already running",
        )
        parser.add_argument(
            "--no-copy", action="store_true",
            help="Do not copy the result to the clipboard",
        )

    def execute(self, args: argparse.Namespace) -> int:
        if args.language:
            self.config.language = args.language
        max_seconds = args.max_seconds or self.config.max_record_seconds

        ensured = self._ensure_server(args)
        if ensured is None:
            return 2
        manager, spawned_here, status = ensured

        try:
            take = _record_and_transcribe(
                self.config, status.base_url, max_seconds,
            )
        except (RecordingError, TranscriptionError) as e:
            logger.error(f"❌ {e}")
            return 1
        finally:
            self._release_server(manager, spawned_here)

        if take.error is not None:
            return 1
        if take.silent is not None:
            logger.warning(
                f"⚠️  Recording was silent ({take.silent.dbfs:.1f} dBFS) — "
                "nothing transcribed"
            )
            return 1
        if not take.text:
            logger.warning("⚠️  Empty transcription")
            return 1

        self._emit(take.text, args)
        return 0


def _record_and_transcribe(config, base_url: str, max_seconds: int) -> TakeResult:
    recorder = AudioRecorder(
        sample_rate=config.sample_rate,
        preferred_mics=config.resolve_preferred_mics(),
    )
    stop_thread = _EnterToStopWatcher(recorder)
    logger.info(f"🎤 Recording (max {max_seconds}s) — press Enter or Ctrl+C to stop")
    try:
        result = recorder.record(max_seconds=max_seconds)
    except KeyboardInterrupt:
        recorder.request_stop()
        raise
    finally:
        stop_thread.stop()

    logger.info(f"🔇 Captured {len(result.samples) / result.sample_rate:.1f}s (peak={result.peak_level:.3f})")
    client = build_transcription_client(config, base_url)
    # auto_copy=False: `_emit` owns the clipboard copy so `--no-copy` is honoured.
    return handle_take(
        result, config, load_webapp_config(), client,
        last_transcription=None, append_mode=False, auto_copy=False,
    )


class _EnterToStopWatcher:
    """Background thread that stops recording on Enter (best-effort)."""

    def __init__(self, recorder: AudioRecorder) -> None:
        self.recorder = recorder
        self._thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()
        if sys.stdin and sys.stdin.isatty():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            sys.stdin.readline()
        except Exception:
            return
        if not self._stopped.is_set():
            self.recorder.request_stop()

    def stop(self) -> None:
        self._stopped.set()
