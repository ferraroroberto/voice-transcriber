"""FastAPI tests for the polish endpoints: /api/polish-text, /api/save-text."""

from __future__ import annotations

# Standard library imports
from unittest.mock import MagicMock

# Local imports
from src.polish import PolishError, PolishResult


def _make_polish_result(text: str = "clean") -> PolishResult:
    return PolishResult(
        polished_text=text,
        model="gemini_flash",
        request_payload={"model": "gemini_flash"},
        response_payload={"id": "msg_x"},
    )


class TestPolishText:
    def test_happy_path(self, webapp_client):
        client, _, overrides = webapp_client
        overrides["polish"].polish.return_value = _make_polish_result("cleaned text")
        resp = client.post(
            "/api/polish-text",
            json={
                "text": "uh, the dirty input",
                "model": "gemini_flash",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["polished"] == "cleaned text"
        assert body["model"] == "gemini_flash"
        # A new session was created and the polish was invoked on it.
        assert body["session_id"]
        overrides["polish"].polish.assert_called_once()

    def test_uses_default_model_when_omitted(self, webapp_client, sample_polish_payload):
        client, _, overrides = webapp_client
        overrides["polish"].polish.return_value = _make_polish_result()
        resp = client.post("/api/polish-text", json={"text": "x"})
        assert resp.status_code == 200
        # The fixture's webapp_config defaults to the sample's default model.
        call = overrides["polish"].polish.call_args
        assert call.kwargs["model"] == sample_polish_payload["polish_model_default"]

    def test_rejects_empty_text(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.post("/api/polish-text", json={"text": "   "})
        assert resp.status_code == 400
        assert "text is required" in resp.json()["detail"]

    def test_rejects_unknown_model_with_400(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.post(
            "/api/polish-text",
            json={"text": "x", "model": "fake_model"},
        )
        assert resp.status_code == 400
        assert "unknown polish model" in resp.json()["detail"]

    def test_polish_failure_returns_424(self, webapp_client):
        client, _, overrides = webapp_client
        overrides["polish"].polish.side_effect = PolishError("hub unreachable")
        resp = client.post(
            "/api/polish-text",
            json={"text": "x", "model": "gemini_flash"},
        )
        # 424 (Failed Dependency) is the deliberate choice so Cloudflare
        # doesn't rewrite the error to its own HTML page on a 5xx.
        assert resp.status_code == 424
        assert "hub unreachable" in resp.json()["detail"]


class TestSaveText:
    def test_happy_path_creates_session_without_polish(self, webapp_client):
        client, _, overrides = webapp_client
        resp = client.post("/api/save-text", json={"text": "save me"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["transcript"] == "save me"
        assert body["session_id"]
        overrides["polish"].polish.assert_not_called()

    def test_rejects_empty_text(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.post("/api/save-text", json={"text": ""})
        assert resp.status_code == 400

    def test_rejects_non_string_text(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.post("/api/save-text", json={"text": 42})
        assert resp.status_code == 400

    def test_ignores_non_string_language(self, webapp_client):
        client, _, _ = webapp_client
        # language=42 should be coerced to None rather than 400; the save
        # endpoint is lenient because the JS sometimes omits the field.
        resp = client.post(
            "/api/save-text",
            json={"text": "ok", "language": 42},
        )
        assert resp.status_code == 200


class TestPolishHelpers:
    def test_resolve_model_default_when_missing(self, webapp_client, sample_polish_payload):
        from app.webapp.routers.sessions import _resolve_model
        cfg = MagicMock()
        cfg.polish_model_default = sample_polish_payload["polish_model_default"]
        cfg.polish_models_available = sample_polish_payload["polish_models_available"]
        assert _resolve_model(None, cfg) == sample_polish_payload["polish_model_default"]
        assert _resolve_model("", cfg) == sample_polish_payload["polish_model_default"]
        assert _resolve_model("  ", cfg) == sample_polish_payload["polish_model_default"]

    def test_resolve_model_passes_through_valid(self, webapp_client, sample_polish_payload):
        from app.webapp.routers.sessions import _resolve_model
        cfg = MagicMock()
        cfg.polish_model_default = sample_polish_payload["polish_model_default"]
        cfg.polish_models_available = sample_polish_payload["polish_models_available"]
        assert _resolve_model("claude_opus", cfg) == "claude_opus"

    def test_resolve_model_rejects_unknown(self, webapp_client, sample_polish_payload):
        from app.webapp.routers.sessions import _resolve_model
        from fastapi import HTTPException
        import pytest
        cfg = MagicMock()
        cfg.polish_model_default = sample_polish_payload["polish_model_default"]
        cfg.polish_models_available = sample_polish_payload["polish_models_available"]
        with pytest.raises(HTTPException) as exc_info:
            _resolve_model("bogus_model", cfg)
        assert exc_info.value.status_code == 400


class TestPreview:
    def test_preview_returns_none_for_empty(self):
        from app.webapp.routers.sessions import _preview
        assert _preview(None, 100) is None
        assert _preview("", 100) is None

    def test_preview_returns_short_text_intact(self):
        from app.webapp.routers.sessions import _preview
        assert _preview("short", 100) == "short"

    def test_preview_truncates_with_ellipsis(self):
        from app.webapp.routers.sessions import _preview
        out = _preview("a" * 300, 50)
        assert out is not None
        assert out.endswith("…")
        assert len(out) == 50

    def test_preview_flattens_newlines(self):
        from app.webapp.routers.sessions import _preview
        assert _preview("line1\nline2", 100) == "line1 line2"
