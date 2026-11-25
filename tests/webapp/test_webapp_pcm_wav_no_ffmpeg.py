"""A PC with no ffmpeg transcribes a take that is already 16 kHz mono PCM WAV.

The browser captures PCM and streams one open-ended WAV; only other formats
(webm, mp4) still need ffmpeg and keep the 503 when it is absent.
"""

from __future__ import annotations

import struct

import pytest

from app.webapp import audio

RATE = 16000
OPEN_ENDED = 0xFFFFFFFF


def _wav(frames: int, *, open_ended: bool) -> bytes:
    pcm = struct.pack("<h", 8000) * frames  # loud enough to pass the real silence gate
    size = OPEN_ENDED if open_ended else len(pcm)
    riff = OPEN_ENDED if open_ended else 36 + len(pcm)
    return (
        b"RIFF" + struct.pack("<I", riff) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
        + b"data" + struct.pack("<I", size) + pcm
    )


@pytest.fixture
def no_ffmpeg_ctx(webapp_client, monkeypatch):
    client, app, overrides = webapp_client
    monkeypatch.setattr(audio, "find_ffmpeg", lambda *_a, **_k: None)
    overrides["transcription"].transcribe_file.return_value = "hello there"
    return client, overrides


@pytest.mark.parametrize("open_ended", [False, True], ids=["closed", "open-ended"])
def test_upload_of_pcm_wav_transcribes_without_ffmpeg(no_ffmpeg_ctx, open_ended):
    client, overrides = no_ffmpeg_ctx
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    resp = client.post(
        f"/api/sessions/{sid}/upload",
        files={"file": ("take.wav", _wav(RATE, open_ended=open_ended), "audio/wav")},
    )
    assert resp.status_code == 200
    assert resp.json()["transcript"] == "hello there"
    overrides["transcription"].transcribe_file.assert_called_once()


def test_streamed_open_ended_chunks_finish_without_ffmpeg(no_ffmpeg_ctx):
    client, _overrides = no_ffmpeg_ctx
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    body = _wav(RATE * 2, open_ended=True)
    half = len(body) // 2 + 1  # an odd cut, as a chunk boundary can land mid-frame
    client.post(f"/api/sessions/{sid}/chunk", content=body[:half])
    client.post(f"/api/sessions/{sid}/chunk", content=body[half:])
    resp = client.post(f"/api/sessions/{sid}/finish")
    assert resp.status_code == 200
    assert resp.json()["transcript"] == "hello there"


def test_webm_upload_without_ffmpeg_is_still_503(no_ffmpeg_ctx):
    client, _overrides = no_ffmpeg_ctx
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    resp = client.post(
        f"/api/sessions/{sid}/upload",
        files={"file": ("take.webm", b"\x1a\x45\xdf\xa3webm", "audio/webm")},
    )
    assert resp.status_code == 503
