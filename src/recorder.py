"""Microphone capture — thin, torch/whisper-free."""

from __future__ import annotations

# Standard library imports
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

# Third-party imports
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, float], None]  # (remaining_seconds, level_0_to_1)

_portaudio_lock = threading.Lock()


def _refresh_portaudio_devices() -> None:
    """Force PortAudio to re-scan host audio devices.

    PortAudio enumerates its device table once at ``Pa_Initialize()`` (i.e.
    once per process) and never notices devices that come or go afterwards.
    A USB mic riding a monitor's USB hub drops out when the monitor is
    disconnected, and PortAudio keeps serving the stale pre-disconnect table
    even after Windows re-attaches the device — recordings then fail with no
    way to recover short of restarting the whole app. Terminating and
    re-initializing PortAudio forces a fresh scan so a reconnected mic is
    picked up on the very next recording attempt.
    """
    with _portaudio_lock:
        try:
            sd._terminate()
        except Exception as e:
            logger.debug(f"PortAudio terminate before device refresh: {e}")
        try:
            sd._initialize()
        except Exception as e:
            logger.warning(f"⚠️  PortAudio re-initialize failed: {e}")


class RecordingError(Exception):
    """Raised when recording fails (no device, no signal, etc.)."""


@dataclass
class Recording:
    samples: np.ndarray  # int16, mono
    sample_rate: int
    peak_level: float


class AudioRecorder:
    """Capture audio from the preferred microphone into memory.

    Usage:
        rec = AudioRecorder(sample_rate=16000, preferred_mics=[...])
        result = rec.record(max_seconds=300, progress=cb)
        rec.request_stop()  # from another thread to end early
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        preferred_mics: Optional[List[str]] = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.preferred_mics = preferred_mics or []
        self._stop_event = threading.Event()

    # ------------------------------------------------------------ device pick

    def list_input_devices(self) -> List[Tuple[int, dict]]:
        return [
            (i, d) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]

    def select_device(self) -> Optional[int]:
        devices = self.list_input_devices()
        if not devices:
            return None
        for wanted in self.preferred_mics:
            for idx, info in devices:
                if wanted.lower() in info["name"].lower():
                    logger.info(f"🎙️  Using preferred mic: {info['name']}")
                    return idx
        idx, info = devices[0]
        logger.info(f"🎙️  Falling back to first input device: {info['name']}")
        return idx

    # --------------------------------------------------------------- capture

    def request_stop(self) -> None:
        self._stop_event.set()

    def record(
        self,
        max_seconds: int,
        device: Optional[int] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> Recording:
        self._stop_event.clear()
        last_error: Optional[RecordingError] = None
        attempts = 1 if device is not None else 2
        for attempt in range(attempts):
            _refresh_portaudio_devices()
            chosen = device if device is not None else self.select_device()
            if chosen is None:
                last_error = RecordingError("no input devices available")
                continue
            try:
                return self._capture(chosen, max_seconds, progress)
            except RecordingError as e:
                last_error = e
                if attempt < attempts - 1:
                    logger.warning(
                        f"⚠️  Recording attempt failed ({e}) — refreshing "
                        "audio devices and retrying once"
                    )
        assert last_error is not None
        raise last_error

    def _capture(
        self,
        device: int,
        max_seconds: int,
        progress: Optional[ProgressCallback],
    ) -> Recording:
        chunks: List[np.ndarray] = []
        last_level: List[float] = [0.0]
        start = time.time()

        def callback(indata, frames, time_info, status) -> None:
            if status:
                logger.debug(f"🎤 Stream status: {status}")
            chunks.append(indata.copy())
            last_level[0] = float(np.max(np.abs(indata)))

        try:
            with sd.InputStream(
                device=device,
                channels=1,
                samplerate=self.sample_rate,
                dtype="float32",
                callback=callback,
            ):
                while True:
                    elapsed = time.time() - start
                    if elapsed >= max_seconds:
                        break
                    if progress is not None:
                        progress(max(0.0, max_seconds - elapsed), last_level[0])
                    if self._stop_event.wait(0.05):
                        break
        except Exception as e:
            raise RecordingError(f"audio stream failed: {e}") from e

        if not chunks:
            raise RecordingError("no audio was captured")

        samples = np.concatenate(chunks).flatten()
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        if peak < 0.001:
            logger.warning(f"⚠️  Very low audio level (peak={peak:.4f}) — check mic")
        int16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        return Recording(samples=int16, sample_rate=self.sample_rate, peak_level=peak)
