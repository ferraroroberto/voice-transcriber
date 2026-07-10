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

from dataclasses import dataclass
from typing import Optional, Union

from .app_config import AppConfig
from .gain import apply_gain_db
from .recorder import Recording
from .silence import is_silent_samples
from .transcription_client import TranscriptionClient
from .webapp_config import WebappConfig

# Same fallback both callers used before this pipeline was shared: the
# tray still processes a take when webapp_config.json failed to load.
_DEFAULT_SILENCE_DBFS_THRESHOLD = -50.0


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
    -50.0 dBFS when ``webapp_cfg`` is ``None``). Raises
    ``TranscriptionError`` on a whisper-server failure — the caller
    decides how to surface it.
    """
    threshold = (
        webapp_cfg.silence_dbfs_threshold
        if webapp_cfg is not None
        else _DEFAULT_SILENCE_DBFS_THRESHOLD
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
