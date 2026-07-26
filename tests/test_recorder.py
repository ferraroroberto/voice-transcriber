"""Tests for `src/recorder.py`'s PortAudio device hot-plug recovery.

Reproduces the USB-hub disconnect/reconnect bug (issue: mic dies when the
monitor it rides is unplugged, F8 stops working until a full tray restart)
and pins the fix: PortAudio's device table is force-refreshed on every
record() call, and a stream-open failure triggers one refresh + retry
before giving up.
"""

from __future__ import annotations

# Standard library imports
from unittest.mock import MagicMock, patch

# Third-party imports
import numpy as np
import pytest

# Local imports
from src import recorder as recorder_module
from src.recorder import AudioRecorder, RecordingError


MIC_INFO = {"name": "USB Mic (on hub)", "max_input_channels": 1}


def _fake_input_stream_factory(*, fails: bool):
    """Build a fake `sd.InputStream` context manager that either raises on
    entry (simulating a stale/invalid PortAudio device handle) or feeds one
    chunk of audio through the callback."""

    class _FakeStream:
        def __init__(self, *args, **kwargs) -> None:
            self._callback = kwargs["callback"]

        def __enter__(self):
            if fails:
                raise RuntimeError("Invalid device (stale PortAudio handle)")
            self._callback(np.array([[0.5]], dtype=np.float32), 1, None, None)
            return self

        def __exit__(self, *exc_info) -> None:
            return None

    return _FakeStream


def test_record_refreshes_portaudio_devices_before_every_attempt() -> None:
    """PortAudio caches its device table at process start and never notices
    hot-plugged devices — every record() call must force a fresh scan so a
    mic that dropped and came back is picked up without an app restart."""
    rec = AudioRecorder(preferred_mics=["USB Mic"])
    rec._stop_event.set()  # stop immediately after the first callback fires

    with patch.object(recorder_module, "_refresh_portaudio_devices") as mock_refresh, \
            patch.object(recorder_module.sd, "query_devices", return_value=[MIC_INFO]), \
            patch.object(recorder_module.sd, "InputStream", _fake_input_stream_factory(fails=False)):
        rec.record(max_seconds=5)

    mock_refresh.assert_called_once()


def test_record_retries_once_after_refresh_when_stream_open_fails() -> None:
    """A stale device handle (mic reconnected but PortAudio's table is
    still frozen pre-disconnect) must self-heal within a single F8 press:
    the first attempt fails, a refresh happens, the second attempt
    succeeds — no exception should reach the caller."""
    rec = AudioRecorder(preferred_mics=["USB Mic"])

    attempt = {"n": 0}

    class _FlakyStream:
        def __init__(self, *args, **kwargs) -> None:
            self._callback = kwargs["callback"]

        def __enter__(self):
            attempt["n"] += 1
            if attempt["n"] == 1:
                raise RuntimeError("Invalid device (stale PortAudio handle)")
            self._callback(np.array([[0.5]], dtype=np.float32), 1, None, None)
            return self

        def __exit__(self, *exc_info) -> None:
            return None

    def stop_after_callback(*_a, **_kw):
        rec._stop_event.set()
        return False

    with patch.object(recorder_module, "_refresh_portaudio_devices") as mock_refresh, \
            patch.object(recorder_module.sd, "query_devices", return_value=[MIC_INFO]), \
            patch.object(recorder_module.sd, "InputStream", _FlakyStream), \
            patch.object(rec._stop_event, "wait", side_effect=stop_after_callback):
        result = rec.record(max_seconds=5)

    assert attempt["n"] == 2
    assert mock_refresh.call_count == 2
    assert result.peak_level == pytest.approx(0.5)


def test_record_raises_after_exhausting_retry() -> None:
    """A genuinely dead device (still gone after the refresh) must still
    surface as a RecordingError, not hang or silently succeed."""
    rec = AudioRecorder(preferred_mics=["USB Mic"])

    with patch.object(recorder_module, "_refresh_portaudio_devices"), \
            patch.object(recorder_module.sd, "query_devices", return_value=[MIC_INFO]), \
            patch.object(recorder_module.sd, "InputStream", _fake_input_stream_factory(fails=True)):
        with pytest.raises(RecordingError, match="audio stream failed"):
            rec.record(max_seconds=5)


def test_record_raises_when_no_devices_present_after_refresh() -> None:
    rec = AudioRecorder()

    with patch.object(recorder_module, "_refresh_portaudio_devices"), \
            patch.object(recorder_module.sd, "query_devices", return_value=[]):
        with pytest.raises(RecordingError, match="no input devices available"):
            rec.record(max_seconds=5)


def test_record_with_explicit_device_does_not_retry() -> None:
    """An explicitly-pinned device (not the hotkey/CLI default path) skips
    the retry loop — the caller made the choice, don't second-guess it."""
    rec = AudioRecorder()

    with patch.object(recorder_module, "_refresh_portaudio_devices") as mock_refresh, \
            patch.object(recorder_module.sd, "InputStream", _fake_input_stream_factory(fails=True)):
        with pytest.raises(RecordingError):
            rec.record(max_seconds=5, device=3)

    mock_refresh.assert_called_once()


def test_refresh_portaudio_devices_terminates_and_reinitializes() -> None:
    calls = []
    with patch.object(recorder_module.sd, "_terminate", side_effect=lambda: calls.append("terminate")), \
            patch.object(recorder_module.sd, "_initialize", side_effect=lambda: calls.append("initialize")):
        recorder_module._refresh_portaudio_devices()

    assert calls == ["terminate", "initialize"]


def test_refresh_portaudio_devices_survives_terminate_failure() -> None:
    """If PortAudio was never fully initialized (edge state), terminate can
    raise — re-initialize must still run so the app doesn't get stuck."""
    with patch.object(recorder_module.sd, "_terminate", side_effect=RuntimeError("not initialized")) as mock_term, \
            patch.object(recorder_module.sd, "_initialize") as mock_init:
        recorder_module._refresh_portaudio_devices()

    mock_term.assert_called_once()
    mock_init.assert_called_once()
