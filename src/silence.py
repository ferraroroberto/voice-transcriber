"""Silence detection — gate that skips whisper on empty audio.

Whisper hallucinates plausible-sounding text on near-silent input
("Thanks for watching!", "[Music]", a single "you", etc.). A simple
RMS-vs-dBFS gate catches those clips before they reach the model so
they don't pollute the history with phantom transcriptions.
"""

from __future__ import annotations

import logging
import math
import wave
from pathlib import Path
from typing import Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SILENCE_DBFS = -50.0
SILENT_FLOOR_DBFS = -120.0  # what we report when the buffer is empty


def rms_dbfs_from_samples(samples: Union[np.ndarray, None]) -> float:
    """Compute the whole-clip RMS in dBFS.

    Accepts int16 (recorder output) or float32/float64 (already in [-1, 1]).
    Returns ``SILENT_FLOOR_DBFS`` for empty / zero-energy input.
    """
    if samples is None:
        return SILENT_FLOOR_DBFS
    arr = np.asarray(samples)
    if arr.size == 0:
        return SILENT_FLOOR_DBFS
    if arr.dtype.kind == "i":
        max_val = float(np.iinfo(arr.dtype).max) or 1.0
        arr = arr.astype(np.float32) / max_val
    elif arr.dtype.kind == "u":
        max_val = float(np.iinfo(arr.dtype).max) or 1.0
        arr = (arr.astype(np.float32) - max_val / 2.0) / (max_val / 2.0)
    elif arr.dtype.kind == "f":
        arr = arr.astype(np.float32)
    else:
        # Unknown dtype — fail open, let whisper run.
        return 0.0
    rms = float(np.sqrt(np.mean(arr * arr)))
    if rms <= 0.0:
        return SILENT_FLOOR_DBFS
    return 20.0 * math.log10(rms)


def rms_dbfs_from_wav(path: Path) -> float:
    """Compute RMS dBFS from a PCM WAV file (any channel count, 8 or 16-bit).

    Failure to read the file returns 0 dB so the gate fails open and
    whisper still runs — the silence filter is best-effort, never a
    blocker for actual transcription.
    """
    try:
        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    except (OSError, wave.Error) as exc:
        logger.warning(f"⚠️  Silence check could not read {path}: {exc}")
        return 0.0
    if not raw:
        return SILENT_FLOOR_DBFS
    if sampwidth == 2:
        samples = np.frombuffer(raw, dtype=np.int16)
    elif sampwidth == 1:
        # 8-bit WAV is unsigned — recentre to signed range.
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) * 256
    else:
        logger.debug(f"silence check: unsupported sampwidth {sampwidth}")
        return 0.0
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1).astype(np.int16)
    return rms_dbfs_from_samples(samples)


def is_silent(dbfs: float, threshold_dbfs: float = DEFAULT_SILENCE_DBFS) -> bool:
    return dbfs < threshold_dbfs


def is_silent_samples(
    samples: Union[np.ndarray, None],
    threshold_dbfs: float = DEFAULT_SILENCE_DBFS,
) -> Tuple[bool, float]:
    """Gate decision for an in-memory sample buffer.

    Returns ``(silent, dbfs)`` so callers can both branch and surface the
    measured loudness in their own notification. Centralises the
    "compute dBFS then compare" pair shared by the tray and tk surfaces.
    """
    dbfs = rms_dbfs_from_samples(samples)
    return is_silent(dbfs, threshold_dbfs), dbfs


def is_silent_wav(
    path: Path,
    threshold_dbfs: float = DEFAULT_SILENCE_DBFS,
) -> Tuple[bool, float]:
    """Gate decision for a PCM WAV file. Returns ``(silent, dbfs)`` — the
    WAV-path counterpart to :func:`is_silent_samples`, used by the webapp."""
    dbfs = rms_dbfs_from_wav(path)
    return is_silent(dbfs, threshold_dbfs), dbfs
