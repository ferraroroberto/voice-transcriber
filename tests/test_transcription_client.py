"""Unit tests for `src/transcription_client.py` — whisper-server HTTP client."""

from __future__ import annotations

# Standard library imports
import io
import wave
from unittest.mock import MagicMock

# Third-party imports
import numpy as np
import pytest
import requests

# Local imports
from src.app_config import AppConfig
from src.transcription_client import (
    TranscriptionClient,
    TranscriptionError,
    _extract_text,
    _flatten,
    build_transcription_client,
    samples_to_wav_bytes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_response(text: str):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.return_value = {"text": text}
    resp.text = '{"text": "%s"}' % text
    return resp


# ---------------------------------------------------------------------------
# samples_to_wav_bytes — encode int16 mono to WAV
# ---------------------------------------------------------------------------

class TestSamplesToWavBytes:
    def test_round_trip_through_wave_module(self):
        samples = np.array([100, -200, 300, -400], dtype=np.int16)
        wav = samples_to_wav_bytes(samples, sample_rate=16000)
        with wave.open(io.BytesIO(wav), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 4

    def test_casts_non_int16_dtype(self):
        samples = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        wav = samples_to_wav_bytes(samples, sample_rate=22050)
        with wave.open(io.BytesIO(wav), "rb") as wf:
            assert wf.getsampwidth() == 2


# ---------------------------------------------------------------------------
# _flatten / _extract_text
# ---------------------------------------------------------------------------

class TestFlatten:
    def test_collapses_whitespace_runs(self):
        assert _flatten("a\n\n  b\tc") == "a b c"

    def test_strips_edges(self):
        assert _flatten("   hi   ") == "hi"


class TestExtractText:
    def test_json_with_text_key(self):
        resp = MagicMock(spec=requests.Response)
        resp.json.return_value = {"text": "hello world"}
        resp.text = "ignored"
        assert _extract_text(resp) == "hello world"

    def test_falls_back_to_raw_text_when_json_fails(self):
        resp = MagicMock(spec=requests.Response)
        resp.json.side_effect = ValueError("not json")
        resp.text = "raw\nbody\nhere"
        assert _extract_text(resp) == "raw body here"

    def test_no_text_key_falls_back_to_raw(self):
        resp = MagicMock(spec=requests.Response)
        resp.json.return_value = {"other": "fields"}
        resp.text = "fallback text"
        assert _extract_text(resp) == "fallback text"


# ---------------------------------------------------------------------------
# TranscriptionClient — URL handling & request shaping
# ---------------------------------------------------------------------------

class TestBaseUrl:
    def test_strips_trailing_slash(self):
        c = TranscriptionClient("http://server:8090/")
        assert c.base_url == "http://server:8090"

    def test_translate_falls_back_to_base(self):
        c = TranscriptionClient("http://server:8090")
        assert c.translate_base_url == "http://server:8090"

    def test_translate_uses_separate_url_when_provided(self):
        c = TranscriptionClient(
            "http://main:8090",
            translate_base_url="http://translate:8091",
        )
        assert c.translate_base_url == "http://translate:8091"


class TestTranscribeWavBytes:
    def test_posts_to_transcriptions_endpoint(self, mocker):
        c = TranscriptionClient("http://server:8090")
        fake = mocker.patch.object(c, "_session")
        fake.post.return_value = _ok_response("hi there")
        out = c.transcribe_wav_bytes(b"WAV_BYTES")
        assert out == "hi there"
        url = fake.post.call_args.args[0]
        assert url == "http://server:8090/v1/audio/transcriptions"

    def test_language_resolves_human_name_to_iso(self, mocker):
        c = TranscriptionClient("http://server:8090")
        fake = mocker.patch.object(c, "_session")
        fake.post.return_value = _ok_response("ok")
        c.transcribe_wav_bytes(b"x", language="Spanish")
        data = fake.post.call_args.kwargs["data"]
        assert data["language"] == "es"
        assert "task" not in data

    def test_iso_code_passed_through(self, mocker):
        c = TranscriptionClient("http://server:8090")
        fake = mocker.patch.object(c, "_session")
        fake.post.return_value = _ok_response("ok")
        c.transcribe_wav_bytes(b"x", language="es")
        data = fake.post.call_args.kwargs["data"]
        assert data["language"] == "es"

    def test_translate_routes_to_translate_url(self, mocker):
        c = TranscriptionClient(
            "http://main:8090", translate_base_url="http://tr:8091"
        )
        fake = mocker.patch.object(c, "_session")
        fake.post.return_value = _ok_response("translated")
        c.transcribe_wav_bytes(b"x", language="Spanish", translate=True)
        url = fake.post.call_args.args[0]
        data = fake.post.call_args.kwargs["data"]
        assert url == "http://tr:8091/v1/audio/transcriptions"
        assert data["task"] == "translate"
        # language is dropped on translate to dodge the :8091 proxy bug
        # documented in transcription_client.py.
        assert "language" not in data

    def test_includes_vocab_prompt_when_available(self, mocker, monkeypatch):
        c = TranscriptionClient("http://server:8090")
        fake = mocker.patch.object(c, "_session")
        fake.post.return_value = _ok_response("ok")
        monkeypatch.setattr(
            "src.transcription_client.prompt_for_language",
            lambda iso: "Anthropic, Claude",
        )
        c.transcribe_wav_bytes(b"x", language="en")
        data = fake.post.call_args.kwargs["data"]
        assert data["prompt"] == "Anthropic, Claude"

    def test_response_text_goes_through_apply_snippets(self, mocker, monkeypatch):
        c = TranscriptionClient("http://server:8090")
        fake = mocker.patch.object(c, "_session")
        fake.post.return_value = _ok_response("ttyl friend")
        monkeypatch.setattr(
            "src.transcription_client.apply_snippets",
            lambda t: t.replace("ttyl", "talk to you later"),
        )
        assert c.transcribe_wav_bytes(b"x") == "talk to you later friend"

    def test_network_error_wraps_as_transcription_error(self, mocker):
        c = TranscriptionClient("http://server:8090")
        fake = mocker.patch.object(c, "_session")
        fake.post.side_effect = requests.ConnectionError("down")
        with pytest.raises(TranscriptionError, match="could not reach"):
            c.transcribe_wav_bytes(b"x")

    def test_non_200_status_raises(self, mocker):
        c = TranscriptionClient("http://server:8090")
        fake = mocker.patch.object(c, "_session")
        resp = MagicMock(spec=requests.Response, status_code=500, text="boom")
        fake.post.return_value = resp
        with pytest.raises(TranscriptionError, match="500"):
            c.transcribe_wav_bytes(b"x")


class TestTranscribeFile:
    def test_missing_file_raises(self, tmp_path):
        c = TranscriptionClient("http://server:8090")
        with pytest.raises(TranscriptionError, match="not found"):
            c.transcribe_file(tmp_path / "absent.wav")

    def test_reads_and_posts_file_bytes(self, mocker, tmp_path):
        path = tmp_path / "sample.wav"
        path.write_bytes(b"PCMDATA")
        c = TranscriptionClient("http://server:8090")
        fake = mocker.patch.object(c, "_session")
        fake.post.return_value = _ok_response("transcript")
        out = c.transcribe_file(path)
        assert out == "transcript"
        # File contents are sent as multipart `file` form-field.
        files = fake.post.call_args.kwargs["files"]
        assert files["file"][1] == b"PCMDATA"
        assert files["file"][0] == "sample.wav"


class TestTranscribeArray:
    def test_encodes_array_and_calls_wav_path(self, mocker):
        c = TranscriptionClient("http://server:8090")
        fake = mocker.patch.object(c, "_session")
        fake.post.return_value = _ok_response("ok")
        samples = np.array([1, 2, 3, 4], dtype=np.int16)
        c.transcribe_array(samples, sample_rate=16000)
        files = fake.post.call_args.kwargs["files"]
        # The file body should be a valid WAV starting with RIFF.
        body = files["file"][1]
        assert body[:4] == b"RIFF"


# ---------------------------------------------------------------------------
# Hub-first with local-whisper fallback (#149)
# ---------------------------------------------------------------------------

class TestHubFallback:
    def test_falls_back_to_whisper_on_transport_error(self, mocker):
        # Hub (primary) is unreachable; the local whisper fallback serves.
        c = TranscriptionClient(
            "http://hub:8000", fallback_base_url="http://whisper:8090"
        )
        fake = mocker.patch.object(c, "_session")

        def route(url, **kwargs):
            if "hub:8000" in url:
                raise requests.ConnectionError("refused")
            return _ok_response("from whisper")

        fake.post.side_effect = route
        assert c.transcribe_wav_bytes(b"x") == "from whisper"
        urls = [call.args[0] for call in fake.post.call_args_list]
        # hub tried first, whisper second.
        assert urls[0] == "http://hub:8000/v1/audio/transcriptions"
        assert urls[1] == "http://whisper:8090/v1/audio/transcriptions"

    def test_non_200_from_hub_is_not_failed_over(self, mocker):
        # A real HTTP error is surfaced as-is — never silently substituted.
        c = TranscriptionClient(
            "http://hub:8000", fallback_base_url="http://whisper:8090"
        )
        fake = mocker.patch.object(c, "_session")
        fake.post.return_value = MagicMock(
            spec=requests.Response, status_code=500, text="boom"
        )
        with pytest.raises(TranscriptionError, match="500"):
            c.transcribe_wav_bytes(b"x")
        assert fake.post.call_count == 1  # whisper never tried

    def test_no_fallback_raises_on_transport_error(self, mocker):
        c = TranscriptionClient("http://hub:8000")  # no fallback configured
        fake = mocker.patch.object(c, "_session")
        fake.post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(TranscriptionError, match="could not reach"):
            c.transcribe_wav_bytes(b"x")
        assert fake.post.call_count == 1

    def test_translate_does_not_use_fallback(self, mocker):
        # Translate has no hub role — its own endpoint, no whisper fallback.
        c = TranscriptionClient(
            "http://hub:8000",
            translate_base_url="http://tr:8091",
            fallback_base_url="http://whisper:8090",
        )
        fake = mocker.patch.object(c, "_session")
        fake.post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(TranscriptionError, match="could not reach"):
            c.transcribe_wav_bytes(b"x", translate=True)
        assert fake.post.call_count == 1
        assert "tr:8091" in fake.post.call_args.args[0]

    def test_fallback_equal_to_primary_skips_retry(self, mocker):
        # No pointless second call to the same dead URL.
        c = TranscriptionClient(
            "http://same:8090", fallback_base_url="http://same:8090"
        )
        fake = mocker.patch.object(c, "_session")
        fake.post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(TranscriptionError, match="could not reach"):
            c.transcribe_wav_bytes(b"x")
        assert fake.post.call_count == 1


class TestBuildTranscriptionClient:
    def test_wires_hub_primary_whisper_fallback_and_translate(self):
        cfg = AppConfig(
            transcribe_base_url="http://hub:8000",
            translate_base_url="http://tr:8091",
        )
        c = build_transcription_client(cfg, "http://whisper:8090")
        assert c.base_url == "http://hub:8000"
        assert c.fallback_base_url == "http://whisper:8090"
        assert c.translate_base_url == "http://tr:8091"
