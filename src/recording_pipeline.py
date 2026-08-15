"""Shared mic-recording take-processing pipeline: silence gate -> gain
boost -> whisper transcription -> finalize.

Both the tk main window (``app/gui/app.py``) and the tray
(``app/gui/tray.py``) run this exact sequence on every Record-button /
hotkey take: a near-silent recording never reaches whisper (it would
otherwise hallucinate text on empty audio), a configured gain boost is
applied for quiet environments, then the samples go to
``TranscriptionClient``, then the result is finalized (strip / append-mode
merge / clipboard copy). ``handle_take`` is the single owner of that whole
sequence (voice-transcriber#174) — each surface gets back one
``TakeResult`` and renders it (toast vs messagebox) plus writes its own
last-transcription slot; the workflow itself can't drift between the two
surfaces again.
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
from .transcription_client import TranscriptionClient, TranscriptionError
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


@dataclass
class TakeResult:
    """Outcome of ``handle_take``. Exactly one of the three fields is set;
    the caller switches on which and renders it — a messagebox for the tk
    window, a toast for the tray. ``text is None`` with ``error`` and
    ``silent`` both ``None`` means the transcription came back empty after
    finalizing (whitespace-only)."""

    text: Optional[str] = None
    error: Optional[str] = None
    silent: Optional[SilentTake] = None


def handle_take(
    recording: Recording,
    config: AppConfig,
    webapp_cfg: Optional[WebappConfig],
    client: TranscriptionClient,
    *,
    last_transcription: Optional[str],
    append_mode: bool,
    auto_copy: bool,
    translate: bool = False,
) -> TakeResult:
    """Run the full take-processing workflow: ``process_recording`` ->
    TranscriptionError arm -> SilentTake arm -> ``finalize_transcript``.

    Both the tray and the tk main window ran hand-written copies of this
    sequence that had already drifted (voice-transcriber#174); this is the
    single implementation. Callers only render the result and write their
    own last-transcription slot.
    """
    try:
        result = process_recording(
            recording, config, webapp_cfg, client, translate=translate,
        )
    except TranscriptionError as e:
        msg = str(e)
        logger.error(f"❌ {msg}")
        return TakeResult(error=msg)

    if isinstance(result, SilentTake):
        logger.info(
            f"🤫 Skipping whisper: {result.dbfs:.1f} dBFS < {result.threshold} dBFS"
        )
        return TakeResult(silent=result)

    text = finalize_transcript(
        result,
        last_transcription=last_transcription,
        append_mode=append_mode,
        auto_copy=auto_copy,
    )
    return TakeResult(text=text)
