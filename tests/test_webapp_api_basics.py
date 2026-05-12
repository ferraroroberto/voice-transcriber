"""FastAPI tests for basic webapp routes: /, /healthz, /api/config, /api/status."""

from __future__ import annotations

# Standard library imports
import json

# Third-party imports
import pytest


class TestHealth:
    def test_healthz_ok(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["service"] == "voice-transcriber-webapp"

    def test_index_returns_html(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/")
        assert resp.status_code == 200
        # FileResponse for the static index — should be text/html-ish.
        assert "text/html" in resp.headers.get("content-type", "")


class TestApiConfig:
    def test_get_returns_polish_models_and_languages(self, webapp_client, sample_polish_payload):
        client, _, _ = webapp_client
        resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        # The polish list comes from sample.json — confirm it round-trips.
        assert body["polish_models_available"] == sample_polish_payload["polish_models_available"]
        assert body["polish_model_default"] == sample_polish_payload["polish_model_default"]
        # Languages should be a list of {iso,label}, sorted alphabetically by label.
        langs = body["languages"]
        assert isinstance(langs, list)
        labels = [l["label"] for l in langs]
        assert labels == sorted(labels)

    def test_get_lists_polish_prompts(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/api/config")
        prompts = resp.json()["polish_prompts"]
        assert isinstance(prompts, list) and len(prompts) >= 1
        assert all("id" in p and "system" in p for p in prompts)

    def test_post_patches_allowed_fields(self, webapp_client, tmp_path, monkeypatch):
        client, app, _ = webapp_client
        # Redirect the persisted config path so we don't clobber the real one.
        target = tmp_path / "webapp_config.json"
        monkeypatch.setattr(
            "src.webapp_config.DEFAULT_CONFIG_PATH", target
        )
        # Seed the file with the in-memory config so update_webapp_config
        # has something to patch.
        from src.webapp_config import save_webapp_config
        save_webapp_config(app.state.webapp_config, target)

        resp = client.post(
            "/api/config",
            json={
                "history_retention_days": 14,
                "preferred_mic_id": "MicX",
                "auth_token": "should-be-ignored-not-in-allowlist",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["config"]["history_retention_days"] == 14
        assert body["config"]["preferred_mic_id"] == "MicX"
        # auth_token isn't in the allowlist → not returned in the config dict.
        assert "auth_token" not in body["config"]

    def test_post_rejects_invalid_config(self, webapp_client, tmp_path, monkeypatch):
        client, app, _ = webapp_client
        target = tmp_path / "webapp_config.json"
        monkeypatch.setattr(
            "src.webapp_config.DEFAULT_CONFIG_PATH", target
        )
        from src.webapp_config import save_webapp_config
        save_webapp_config(app.state.webapp_config, target)

        # default outside the allowed list → 400.
        resp = client.post(
            "/api/config",
            json={"polish_model_default": "not-a-real-model"},
        )
        assert resp.status_code == 400


class TestApiStatus:
    def test_returns_three_sections(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/api/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "whisper" in body
        assert "llm_hub" in body
        assert "ffmpeg_present" in body
        assert body["ffmpeg_present"] is False  # stubbed in fixture
        assert body["llm_hub"]["reachable"] is True
        assert body["whisper"]["base_url"] == "http://stub:8090"
