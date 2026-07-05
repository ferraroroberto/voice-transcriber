"""Unit tests for `src/gain.py` — quiet-environment gain boost."""

from __future__ import annotations

# Standard library imports
import wave
from pathlib import Path

# Third-party imports
import numpy as np
import pytest

# Local imports
from src.gain import apply_gain_db, apply_gain_to_wav


class TestApplyGainDb:
    def test_zero_db_is_a_no_op(self):
        arr = np.array([100, -200, 300], dtype=np.int16)
        out = apply_gain_db(arr, 0.0)
        assert out is arr

    def test_boost_doubles_amplitude_at_6db(self):
        arr = np.array([1000, -1000], dtype=np.int16)
        out = apply_gain_db(arr, 6.0)
        # 6 dB ≈ x2 — allow rounding slack.
        assert out[0] == pytest.approx(2000, abs=20)
        assert out[1] == pytest.approx(-2000, abs=20)

    def test_boost_clips_to_int16_range(self):
        arr = np.array([30000, -30000], dtype=np.int16)
        out = apply_gain_db(arr, 12.0)
        assert out[0] == 32767
        assert out[1] == -32768

    def test_output_dtype_is_int16(self):
        arr = np.array([100], dtype=np.int16)
        out = apply_gain_db(arr, 12.0)
        assert out.dtype == np.int16


def _write_pcm_wav(path: Path, samples: np.ndarray, sampwidth: int = 2, sample_rate: int = 16000):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


class TestApplyGainToWav:
    def test_zero_db_leaves_file_untouched(self, tmp_path: Path):
        path = tmp_path / "a.wav"
        samples = np.array([1000, -1000], dtype=np.int16)
        _write_pcm_wav(path, samples)
        before = path.read_bytes()
        apply_gain_to_wav(path, 0.0)
        assert path.read_bytes() == before

    def test_boosts_samples_in_place(self, tmp_path: Path):
        path = tmp_path / "b.wav"
        samples = np.array([1000, -1000] * 100, dtype=np.int16)
        _write_pcm_wav(path, samples)
        apply_gain_to_wav(path, 6.0)
        with wave.open(str(path), "rb") as wf:
            boosted = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        assert boosted[0] == pytest.approx(2000, abs=20)

    def test_preserves_wav_params(self, tmp_path: Path):
        path = tmp_path / "c.wav"
        samples = np.array([500, -500] * 50, dtype=np.int16)
        _write_pcm_wav(path, samples, sample_rate=16000)
        apply_gain_to_wav(path, 12.0)
        with wave.open(str(path), "rb") as wf:
            assert wf.getframerate() == 16000
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2

    def test_missing_file_fails_open(self, tmp_path: Path):
        # Should not raise — best-effort, never a blocker for transcription.
        apply_gain_to_wav(tmp_path / "missing.wav", 12.0)

    def test_unsupported_sampwidth_fails_open(self, tmp_path: Path):
        path = tmp_path / "weird.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(3)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00\x00" * 100)
        before = path.read_bytes()
        apply_gain_to_wav(path, 12.0)
        assert path.read_bytes() == before
