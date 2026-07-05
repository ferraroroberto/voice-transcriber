"""Quiet-environment gain boost — amplifies captured audio before whisper.

Distinct from `silence.py`'s hallucination gate: that module decides whether
to skip whisper entirely on a near-silent clip; this module amplifies
genuinely quiet-but-real speech so whisper transcribes it more reliably.
The two are orthogonal by construction — callers must run the silence gate
first, against the *original* (un-boosted) audio, then apply gain boost
only to takes that already passed it. Boosting before the silence check
would inflate a clip's measured loudness and defeat the gate's calibration.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_GAIN_BOOST_DB = 12.0
MIN_GAIN_BOOST_DB = 0.0
MAX_GAIN_BOOST_DB = 24.0


def apply_gain_db(samples: np.ndarray, gain_db: float) -> np.ndarray:
    """Amplify int16 mono samples by ``gain_db`` decibels, clipped to int16 range."""
    if gain_db == 0:
        return samples
    factor = 10.0 ** (gain_db / 20.0)
    boosted = samples.astype(np.float32) * factor
    return np.clip(boosted, -32768, 32767).astype(np.int16)


def apply_gain_to_wav(path: Path, gain_db: float) -> None:
    """Boost a 16-bit PCM WAV file in place.

    No-ops (fails open, leaves the file untouched) on anything but 16-bit
    PCM or on a read failure — gain boost is best-effort, never a blocker
    for transcription.
    """
    if gain_db == 0:
        return
    try:
        with wave.open(str(path), "rb") as wf:
            params = wf.getparams()
            raw = wf.readframes(wf.getnframes())
    except (OSError, wave.Error) as exc:
        logger.warning(f"⚠️  Gain boost could not read {path}: {exc}")
        return
    if params.sampwidth != 2:
        logger.debug(f"gain boost: unsupported sampwidth {params.sampwidth}, skipping")
        return

    samples = np.frombuffer(raw, dtype=np.int16)
    boosted = apply_gain_db(samples, gain_db)

    with wave.open(str(path), "wb") as wf:
        wf.setparams(params)
        wf.writeframes(boosted.tobytes())
