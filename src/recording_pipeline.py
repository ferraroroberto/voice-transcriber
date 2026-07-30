"""Shared mic-recording take-processing pipeline: silence gate -> gain
boost -> whisper transcription.

Both the tk main window (``app/gui/app.py``) and the tray
(``app/gui/tray.py``) run this exact sequence on every Record-button /
hotkey take: a near-silent recording never reaches whisper (it would
otherwise hallucinate text on empty audio), a configured gain boost is
applied for quiet environments, then the samples go to
``TranscriptionClient``. One implementation here means a fix to the
silence-threshold fallback or the gain-boost ordering lands once instead
of twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Union

import pyperclip

from .app_config import AppConfig
from .gain import apply_gain_db
from .recorder import Recording
from .silence import DEFAULT_SILENCE_DBFS, is_silent_samples
from .transcription_client import TranscriptionClient
from .webapp_config import WebappConfig

logger = logging.getLogger(__name__)


@dataclass
class SilentTake:
    """Returned instead of transcribed text when the take didn't clear the
    silence gate — the caller turns this into its own UI-specific notice
    (a messagebox for the tk window, a toast for the tray)."""

    dbfs: float
    threshold: float


def process_recording(
    recording: Recording,
    config: AppConfig,
    webapp_cfg: Optional[WebappConfig],
    client: TranscriptionClient,
    *,
    translate: bool = False,
) -> Union[str, SilentTake]:
    """Run the silence gate -> gain boost -> transcribe pipeline.

    Returns the transcribed text, or a ``SilentTake`` when the recording
    didn't clear ``webapp_cfg.silence_dbfs_threshold`` (falling back to
    ``silence.DEFAULT_SILENCE_DBFS`` when ``webapp_cfg`` is ``None`` — the
    tray still processes a take when webapp_config.json failed to load).
    Raises ``TranscriptionError`` on a whisper-server failure — the caller
    decides how to surface it.
    """
    threshold = (
        webapp_cfg.silence_dbfs_threshold
        if webapp_cfg is not None
        else DEFAULT_SILENCE_DBFS
    )
    silent, dbfs = is_silent_samples(recording.samples, threshold)
    if silent:
        return SilentTake(dbfs=dbfs, threshold=threshold)

    if webapp_cfg is not None and webapp_cfg.gain_boost_enabled:
        recording.samples = apply_gain_db(recording.samples, webapp_cfg.gain_boost_db)

    return client.transcribe_array(
        recording.samples,
        recording.sample_rate,
        language=config.whisper_language,
        translate=translate,
    )


def finalize_transcript(
    text: str,
    *,
    last_transcription: Optional[str],
    append_mode: bool,
    auto_copy: bool,
) -> Optional[str]:
    """Shared post-transcription tail: strip -> append-mode glue -> clipboard.

    Both the tray and the tk main window run this exact sequence after
    ``process_recording`` returns text: strip whitespace, merge onto
    ``last_transcription`` with a blank-line separator when ``append_mode``
    is on, then copy to the clipboard when ``auto_copy`` is on. One
    implementation here means the ordering can't drift between the two
    surfaces again (voice-transcriber#160).

    Returns ``None`` for an empty (whitespace-only) transcription — callers
    treat that as "nothing to show" and skip writing back their own
    last-transcription slot or touching the clipboard. Otherwise returns
    the finalized text; the caller writes it into its own last-transcription
    slot and handles its own UI-specific notification/display.
    """
    text = text.strip()
    if not text:
        return None
    if append_mode and last_transcription:
        text = last_transcription.rstrip() + "\n\n" + text
    if auto_copy:
        try:
            pyperclip.copy(text)
        except Exception as exc:
            logger.warning(f"⚠️  Clipboard copy failed: {exc}")
    return text
