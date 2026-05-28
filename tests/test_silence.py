"""Unit tests for `src/silence.py` — RMS dBFS gate before whisper."""

from __future__ import annotations

# Standard library imports
import struct
import wave
from pathlib import Path

# Third-party imports
import numpy as np
import pytest

# Local imports
from src.silence import (
    DEFAULT_SILENCE_DBFS,
    SILENT_FLOOR_DBFS,
    is_silent,
    is_silent_samples,
    is_silent_wav,
    rms_dbfs_from_samples,
    rms_dbfs_from_wav,
)


# ---------------------------------------------------------------------------
# rms_dbfs_from_samples — covers None/empty/int16/float/odd dtype.
# ---------------------------------------------------------------------------

class TestRmsDbfsFromSamples:
    def test_none_returns_floor(self):
        assert rms_dbfs_from_samples(None) == SILENT_FLOOR_DBFS

    def test_empty_array_returns_floor(self):
        assert rms_dbfs_from_samples(np.array([], dtype=np.int16)) == SILENT_FLOOR_DBFS

    def test_all_zero_int16_returns_floor(self):
        arr = np.zeros(1000, dtype=np.int16)
        assert rms_dbfs_from_samples(arr) == SILENT_FLOOR_DBFS

    def test_full_scale_int16_is_near_zero_dbfs(self):
        """A signal that hits ±max int16 should clock in around 0 dBFS."""
        arr = np.full(1000, np.iinfo(np.int16).max, dtype=np.int16)
        db = rms_dbfs_from_samples(arr)
        assert -1.0 < db <= 0.0

    def test_half_scale_int16_is_around_minus_6_dbfs(self):
        arr = np.full(1000, np.iinfo(np.int16).max // 2, dtype=np.int16)
        db = rms_dbfs_from_samples(arr)
        # Half amplitude == -6.02 dBFS.
        assert -7.5 < db < -5.5

    def test_float32_in_range_minus_one_to_one(self):
        arr = np.ones(1000, dtype=np.float32)
        # All-ones float at full scale → 0 dBFS.
        assert rms_dbfs_from_samples(arr) == pytest.approx(0.0, abs=0.01)

    def test_quiet_float_signal_is_well_below_threshold(self):
        arr = np.full(1000, 0.001, dtype=np.float32)  # ~ -60 dBFS
        db = rms_dbfs_from_samples(arr)
        assert db < -50.0

    def test_unknown_dtype_fails_open_returning_zero(self):
        """Complex dtype isn't audio — we return 0 dB so the gate fails
        open and whisper still runs."""
        arr = np.array([1+1j, 0+0j], dtype=np.complex64)
        assert rms_dbfs_from_samples(arr) == 0.0


# ---------------------------------------------------------------------------
# rms_dbfs_from_wav — read 8-bit and 16-bit PCM WAVs.
# ---------------------------------------------------------------------------

def _write_pcm_wav(path: Path, samples: np.ndarray, sampwidth: int = 2, n_channels: int = 1, sample_rate: int = 16000):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


class TestRmsDbfsFromWav:
    def test_missing_file_fails_open(self, tmp_path: Path):
        # No file -> wave.open raises -> we return 0 dB so whisper runs.
        out = rms_dbfs_from_wav(tmp_path / "missing.wav")
        assert out == 0.0

    def test_silent_16bit_wav_returns_floor(self, tmp_path: Path):
        path = tmp_path / "s.wav"
        _write_pcm_wav(path, np.zeros(1000, dtype=np.int16))
        assert rms_dbfs_from_wav(path) == SILENT_FLOOR_DBFS

    def test_loud_16bit_wav_returns_near_zero_dbfs(self, tmp_path: Path):
        path = tmp_path / "l.wav"
        samples = np.full(1000, np.iinfo(np.int16).max, dtype=np.int16)
        _write_pcm_wav(path, samples)
        assert rms_dbfs_from_wav(path) > -1.0

    def test_8bit_wav_silent(self, tmp_path: Path):
        path = tmp_path / "s8.wav"
        # 8-bit PCM is unsigned with bias 128 — write all 128 ≈ silence.
        samples = np.full(1000, 128, dtype=np.uint8)
        _write_pcm_wav(path, samples, sampwidth=1)
        assert rms_dbfs_from_wav(path) < -40.0

    def test_stereo_wav_averages_channels(self, tmp_path: Path):
        path = tmp_path / "stereo.wav"
        n = 1000
        full = np.iinfo(np.int16).max
        # Left = full, Right = 0 -> average ≈ full/2 -> ~-6 dBFS.
        stereo = np.empty(n * 2, dtype=np.int16)
        stereo[0::2] = full
        stereo[1::2] = 0
        _write_pcm_wav(path, stereo, n_channels=2)
        db = rms_dbfs_from_wav(path)
        assert -8.0 < db < -5.0

    def test_unsupported_sampwidth_fails_open(self, tmp_path: Path):
        path = tmp_path / "weird.wav"
        # 24-bit (3-byte) PCM — supported by wave but not by our silence
        # detector. We expect 0 dB so the gate fails open.
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(3)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00\x00" * 100)
        assert rms_dbfs_from_wav(path) == 0.0


# ---------------------------------------------------------------------------
# is_silent — pure threshold predicate.
# ---------------------------------------------------------------------------

class TestIsSilent:
    def test_below_default_threshold(self):
        assert is_silent(SILENT_FLOOR_DBFS) is True

    def test_at_default_threshold_is_not_silent(self):
        # `<` not `<=` so exactly-threshold is treated as audible.
        assert is_silent(DEFAULT_SILENCE_DBFS) is False

    def test_above_default_threshold(self):
        assert is_silent(0.0) is False

    def test_custom_threshold(self):
        assert is_silent(-30.0, threshold_dbfs=-20.0) is True
        assert is_silent(-10.0, threshold_dbfs=-20.0) is False


# ---------------------------------------------------------------------------
# is_silent_samples / is_silent_wav — shared gate helpers returning (silent, dbfs).
# ---------------------------------------------------------------------------

class TestIsSilentSamples:
    def test_empty_buffer_is_silent(self):
        silent, dbfs = is_silent_samples(np.array([], dtype=np.int16))
        assert silent is True
        assert dbfs == SILENT_FLOOR_DBFS

    def test_loud_buffer_is_not_silent(self):
        arr = (np.ones(1000, dtype=np.float32) * 0.5)
        silent, dbfs = is_silent_samples(arr)
        assert silent is False
        assert dbfs > DEFAULT_SILENCE_DBFS

    def test_threshold_passed_through(self):
        arr = (np.ones(1000, dtype=np.float32) * 0.5)
        # ~-6 dBFS signal: silent only when the threshold is raised above it.
        assert is_silent_samples(arr, threshold_dbfs=0.0)[0] is True
        assert is_silent_samples(arr, threshold_dbfs=-50.0)[0] is False


class TestIsSilentWav:
    def test_silent_wav(self, tmp_path: Path):
        path = tmp_path / "silent.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack("<" + "h" * 1600, *([0] * 1600)))
        silent, dbfs = is_silent_wav(path)
        assert silent is True
        assert dbfs == SILENT_FLOOR_DBFS
