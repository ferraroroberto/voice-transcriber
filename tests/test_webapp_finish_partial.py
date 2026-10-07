"""`POST /api/sessions/{id}/finish` — rolling-partial shortcut guards (#244).

The shortcut serves the worker's last partial instead of running a final
whisper pass. Partials are never translated and always use the session's
own language, so a translate or different-language request must fall
through to the real transcription path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.webapp.routers import sessions as sessions_router

PARTIAL = "partial source-language text"
FINAL = "final transcript"


@pytest.fixture
def finish_ctx(webapp_client, monkeypatch):
    client, app, overrides = webapp_client

    def _fake_transcode(_src, dst: Path, _rate) -> None:
        Path(dst).write_bytes(b"RIFFstub")

    monkeypatch.setattr(sessions_router, "transcode_to_wav", _fake_transcode)
    monkeypatch.setattr(sessions_router, "is_silent_wav", lambda *_a, **_k: (False, -20.0))
    overrides["transcription"].transcribe_file.return_value = FINAL

    sid = client.post("/api/sessions", json={"language": "en"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/chunk", content=b"audio-bytes")
    # Pretend the rolling worker already transcribed every byte received.
    worker = app.state.partial_workers[sid]
    worker.partial_text = PARTIAL
    worker.last_bytes_at_partial = app.state.archive.get(sid).raw_path().stat().st_size
    return client, overrides, sid


def test_finish_reuses_covering_partial(finish_ctx):
    client, overrides, sid = finish_ctx
    body = client.post(f"/api/sessions/{sid}/finish").json()
    assert body["from_partial"] is True
    assert body["transcript"] == PARTIAL
    overrides["transcription"].transcribe_file.assert_not_called()


def test_finish_translate_skips_partial_shortcut(finish_ctx):
    client, overrides, sid = finish_ctx
    body = client.post(f"/api/sessions/{sid}/finish", params={"translate": "true"}).json()
    assert "from_partial" not in body
    assert body["transcript"] == FINAL
    assert overrides["transcription"].transcribe_file.call_args.args[2] is True


def test_finish_different_language_skips_partial_shortcut(finish_ctx):
    client, overrides, sid = finish_ctx
    body = client.post(f"/api/sessions/{sid}/finish", params={"language": "es"}).json()
    assert "from_partial" not in body
    assert body["transcript"] == FINAL
    assert overrides["transcription"].transcribe_file.call_args.args[1] == "es"


def test_finish_same_language_still_reuses_partial(finish_ctx):
    client, overrides, sid = finish_ctx
    body = client.post(f"/api/sessions/{sid}/finish", params={"language": "en"}).json()
    assert body["from_partial"] is True
    overrides["transcription"].transcribe_file.assert_not_called()
