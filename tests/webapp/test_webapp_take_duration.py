"""A take's duration is derived server-side when the client omits it.

API callers never send ``duration_seconds`` in the finish body, so the
``transcribed`` event used to log NULL for them. The server holds the
transcoded WAV for every take, so the length is read from that instead.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.webapp import audio
from app.webapp.routers import sessions as sessions_router
from src import activity_log

WAV_SECONDS = 2.0
SAMPLE_RATE = 16000


def _write_wav(dst: Path, seconds: float = WAV_SECONDS) -> None:
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"\x00\x00" * int(SAMPLE_RATE * seconds))


@pytest.fixture
def take_ctx(webapp_client, monkeypatch):
    client, app, overrides = webapp_client

    def _fake_transcode(_src, dst: Path, _rate) -> None:
        _write_wav(Path(dst))

    monkeypatch.setattr(sessions_router, "transcode_to_wav", _fake_transcode)
    monkeypatch.setattr(sessions_router, "is_silent_wav", lambda *_a, **_k: (False, -20.0))
    overrides["transcription"].transcribe_file.return_value = "hello there"
    return client, app, overrides


def _transcribed_event(sid: str) -> dict:
    events = [
        e for e in activity_log.read_events(event_type="transcribed")
        if e["session_id"] == sid
    ]
    assert len(events) == 1
    return events[0]


def test_upload_without_client_duration_logs_wav_length(take_ctx):
    client, _app, _overrides = take_ctx
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    resp = client.post(
        f"/api/sessions/{sid}/upload",
        files={"file": ("take.webm", b"audio-bytes", "audio/webm")},
    )
    assert resp.status_code == 200
    duration = _transcribed_event(sid)["duration_seconds"]
    assert duration is not None
    assert duration == pytest.approx(WAV_SECONDS, abs=0.5)


def test_upload_persists_derived_duration_on_the_take(take_ctx):
    client, app, _overrides = take_ctx
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/upload",
        files={"file": ("take.webm", b"audio-bytes", "audio/webm")},
    )
    meta = app.state.archive.get(sid).meta
    assert meta.duration_seconds == pytest.approx(WAV_SECONDS, abs=0.5)


def test_finish_keeps_client_supplied_duration(take_ctx):
    client, _app, _overrides = take_ctx
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/chunk", content=b"audio-bytes")
    client.post(f"/api/sessions/{sid}/finish", json={"duration_seconds": 7.25})
    assert _transcribed_event(sid)["duration_seconds"] == pytest.approx(7.25)


def test_finish_without_client_duration_logs_wav_length(take_ctx):
    client, _app, _overrides = take_ctx
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/chunk", content=b"audio-bytes")
    client.post(f"/api/sessions/{sid}/finish")
    assert _transcribed_event(sid)["duration_seconds"] == pytest.approx(
        WAV_SECONDS, abs=0.5
    )


def test_partial_reuse_path_derives_duration_from_the_partial_wav(take_ctx):
    client, app, _overrides = take_ctx
    sid = client.post("/api/sessions", json={"language": "en"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/chunk", content=b"audio-bytes")
    session = app.state.archive.get(sid)
    # The rolling worker leaves the WAV of every byte received on disk.
    _write_wav(session.wav_path())
    worker = app.state.partial_workers[sid]
    worker.partial_text = "partial text"
    worker.last_bytes_at_partial = session.raw_path().stat().st_size

    body = client.post(f"/api/sessions/{sid}/finish").json()

    assert body["from_partial"] is True
    assert _transcribed_event(sid)["duration_seconds"] == pytest.approx(
        WAV_SECONDS, abs=0.5
    )


def test_wav_duration_seconds_reads_frames_over_rate(tmp_path):
    wav = tmp_path / "x.wav"
    _write_wav(wav, seconds=1.5)
    assert audio.wav_duration_seconds(wav) == pytest.approx(1.5)


@pytest.mark.parametrize("content", [None, b"RIFFstub"])
def test_wav_duration_seconds_is_none_when_unreadable(tmp_path, content):
    wav = tmp_path / "x.wav"
    if content is not None:
        wav.write_bytes(content)
    assert audio.wav_duration_seconds(wav) is None
