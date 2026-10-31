"""``transcribed`` events store the language as an ISO code.

The requested language arrives as a name from API callers (``"english"``)
and as a code from the PWA (``"en"``); a group-by over ``payload.language``
split one language into two buckets, so both write paths normalise it.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.webapp.routers import sessions as sessions_router
from src import activity_log


def _write_wav(dst: Path) -> None:
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)


@pytest.fixture
def lang_ctx(webapp_client, monkeypatch):
    client, app, overrides = webapp_client

    def _fake_transcode(_src, dst: Path, _rate) -> None:
        _write_wav(Path(dst))

    monkeypatch.setattr(sessions_router, "transcode_to_wav", _fake_transcode)
    monkeypatch.setattr(sessions_router, "is_silent_wav", lambda *_a, **_k: (False, -20.0))
    overrides["transcription"].transcribe_file.return_value = "hello there"
    return client, app


def _event_language(sid: str):
    events = [
        e for e in activity_log.read_events(event_type="transcribed")
        if e["session_id"] == sid
    ]
    assert len(events) == 1
    return events[0]["payload"]["language"]


@pytest.mark.parametrize(
    "requested, expected",
    [("english", "en"), ("Spanish", "es"), ("en", "en"), ("it", "it")],
)
def test_upload_stores_iso_code(lang_ctx, requested, expected):
    client, _app = lang_ctx
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/upload",
        params={"language": requested},
        files={"file": ("take.webm", b"audio-bytes", "audio/webm")},
    )
    assert _event_language(sid) == expected


def test_session_language_name_from_create_is_stored_as_iso(lang_ctx):
    client, _app = lang_ctx
    sid = client.post("/api/sessions", json={"language": "english"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/chunk", content=b"audio-bytes")
    client.post(f"/api/sessions/{sid}/finish")
    assert _event_language(sid) == "en"


def test_partial_reuse_path_stores_iso_code(lang_ctx):
    client, app = lang_ctx
    sid = client.post("/api/sessions", json={"language": "english"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/chunk", content=b"audio-bytes")
    worker = app.state.partial_workers[sid]
    worker.partial_text = "partial text"
    worker.last_bytes_at_partial = app.state.archive.get(sid).raw_path().stat().st_size

    body = client.post(f"/api/sessions/{sid}/finish").json()

    assert body["from_partial"] is True
    assert _event_language(sid) == "en"


def test_response_and_meta_keep_the_requested_spelling(lang_ctx):
    client, app = lang_ctx
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    body = client.post(
        f"/api/sessions/{sid}/upload",
        params={"language": "english"},
        files={"file": ("take.webm", b"audio-bytes", "audio/webm")},
    ).json()
    assert body["language"] == "english"
    assert app.state.archive.get(sid).meta.language == "english"
