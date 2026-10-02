"""FastAPI tests for basic webapp routes: /, /healthz, /api/config, /api/status."""

from __future__ import annotations

# Standard library imports
import json
import re
from pathlib import Path

# Third-party imports
import pytest

# Local imports
from src.static_versioning import asset_hash

_STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "webapp" / "static"

# Every asset index.html references directly with a ?v=__NAME__ placeholder —
# root assets plus the vendored component CSS (issue #107). Deliberately a
# hand-kept mirror of src.static_versioning.HTML_STAMPED_ASSETS, not an import
# of it: dropping an asset from the source tuple must fail here.
_STAMPED_ASSETS = (
    "app.js",
    "styles.css",
    "_vendored/nav/nav-tabs.css",
    "_vendored/card/card.css",
    "_vendored/switch/switch.css",
    "_vendored/select-native/select-native.css",
    "_vendored/modal/modal.css",
    "_vendored/empty-state/empty-state.css",
    "_vendored/button/button.css",
)


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
        assert "text/html" in resp.headers.get("content-type", "")


class TestBuildVersion:
    """Cache hygiene + build identity — see issue #13."""

    def test_version_endpoint_shape(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/api/version")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("git_sha", "built_at", "asset_hash"):
            assert key in body and isinstance(body[key], str) and body[key]

    def test_index_is_content_hash_stamped(self, webapp_client):
        client, _, _ = webapp_client
        html = client.get("/").text
        # The manual ?v=N stamps and their placeholders are both gone —
        # replaced by an 8-hex content hash computed at startup.
        assert "__APP_JS__" not in html and "__STYLES_CSS__" not in html
        assert re.search(r"/static/app\.js\?v=[0-9a-f]{8}", html)
        assert re.search(r"/static/styles\.css\?v=[0-9a-f]{8}", html)

    def test_index_revalidates(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-cache" in cc, f"index.html must revalidate; got {cc!r}"

    def test_static_assets_are_long_cached(self, webapp_client):
        client, _, _ = webapp_client
        for asset in _STAMPED_ASSETS:
            cc = client.get(f"/static/{asset}").headers.get("cache-control", "")
            assert "max-age=31536000" in cc and "immutable" in cc, (
                f"{asset} must be immutably cached; got {cc!r}"
            )

    def test_index_stamps_match_on_disk(self, webapp_client):
        """Catches "edited a JS/CSS file but the served stamp is stale"."""
        client, _, _ = webapp_client
        html = client.get("/").text
        # Every placeholder must have been substituted at render time.
        leftovers = re.findall(r"\?v=__[A-Z_]+__", html)
        assert not leftovers, f"unstamped placeholders served: {leftovers}"
        for asset in _STAMPED_ASSETS:
            match = re.search(rf"/static/{re.escape(asset)}\?v=([0-9a-f]{{8}})", html)
            assert match, f"{asset} is not content-hash stamped in index.html"
            expected = asset_hash(_STATIC_DIR / asset)
            assert match.group(1) == expected, (
                f"{asset} stamp {match.group(1)} diverges from the on-disk "
                f"content hash {expected} — a stale deploy or a missed bust"
            )

    def test_version_asset_hash_matches_app_js_stamp(self, webapp_client):
        client, _, _ = webapp_client
        version = client.get("/api/version").json()
        html = client.get("/").text
        match = re.search(r"/static/app\.js\?v=([0-9a-f]{8})", html)
        assert match and match.group(1) == version["asset_hash"], (
            "/api/version asset_hash must equal the app.js stamp in index.html"
        )

    def test_icons_revalidate_daily(self, webapp_client):
        client, _, _ = webapp_client
        cc = client.get("/static/favicon.ico").headers.get("cache-control", "")
        assert "max-age=86400" in cc


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

    def test_post_with_empty_body_does_not_500(self, webapp_client, tmp_path, monkeypatch):
        """Regression pin for issue #117: patch_config used to call
        `await request.json()` directly instead of the shared
        `maybe_json` helper, so a malformed/empty POST body raised an
        unhandled JSONDecodeError -> bare 500 instead of the established
        empty-patch-is-a-no-op behaviour every other body-optional
        endpoint uses."""
        client, app, _ = webapp_client
        target = tmp_path / "webapp_config.json"
        monkeypatch.setattr(
            "src.webapp_config.DEFAULT_CONFIG_PATH", target
        )
        from src.webapp_config import save_webapp_config
        save_webapp_config(app.state.webapp_config, target)

        resp = client.post("/api/config")  # no body, no content-type
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


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
