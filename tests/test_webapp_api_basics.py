"""FastAPI tests for basic webapp routes: /, /healthz, /api/config, /api/status."""

from __future__ import annotations

# Standard library imports
import json
import re
from pathlib import Path

# Third-party imports
import pytest

# Local imports
from src.static_versioning import BuildInfo, asset_hash

_STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "webapp" / "static"

# Every asset index.html references directly with a ?v=__NAME__ placeholder —
# root assets plus the vendored component CSS (issue #107). Deliberately a
# hand-kept mirror of src.static_versioning.HTML_STAMPED_ASSETS, not an import
# of it: dropping an asset from the source tuple must fail here.
_STAMPED_ASSETS = (
    "app.js",
    "styles.css",
    "_vendored/base/base.css",
    "_vendored/nav/nav-tabs.css",
    "_vendored/card/card.css",
    "_vendored/home-head/home-head.css",
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
            if asset.endswith(".js"):
                # A script's stamp covers its import graph (issue #220), so
                # it is recomputed from disk rather than its own bytes.
                expected = BuildInfo(_STATIC_DIR, _STATIC_DIR).asset_hashes[asset]
            assert match.group(1) == expected, (
                f"{asset} stamp {match.group(1)} diverges from the on-disk "
                f"hash {expected} — a stale deploy or a missed bust"
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


class TestNestedModuleCacheBusting:
    """A change deep in the ES-module graph must change every URL on the path
    from the entry document down to it — each module is served immutably, so
    a client that cached ``app.js`` only re-fetches the changed module if the
    URL it is told to fetch moved. Issue #220."""

    @staticmethod
    def _build(tmp_path: Path, nested_body: str):
        static = tmp_path / "static"
        (static / "sub").mkdir(parents=True, exist_ok=True)
        (static / "index.html").write_text(
            '<script type="module" src="/static/app.js?v=__APP_JS__"></script>',
            encoding="utf-8",
        )
        (static / "app.js").write_text("import './mid.js';\n", encoding="utf-8")
        (static / "mid.js").write_text("import './sub/leaf.js';\n", encoding="utf-8")
        (static / "sub" / "leaf.js").write_text(nested_body, encoding="utf-8")
        (static / "other.js").write_text("export const x = 1;\n", encoding="utf-8")
        info = BuildInfo(static, tmp_path)
        html = info.stamp_html((static / "index.html").read_text(encoding="utf-8"))
        return info, html

    def test_nested_only_change_moves_the_entry_url(self, tmp_path):
        _, before = self._build(tmp_path, "export const a = 1;\n")
        _, after = self._build(tmp_path, "export const a = 2;\n")
        assert before != after, (
            "only sub/leaf.js changed, yet the app.js URL index.html serves is "
            "identical — a phone with app.js cached keeps the old import stamps"
        )

    def test_every_module_on_the_import_path_moves(self, tmp_path):
        old, _ = self._build(tmp_path, "export const a = 1;\n")
        new, _ = self._build(tmp_path, "export const a = 2;\n")
        for name in ("app.js", "mid.js", "sub/leaf.js"):
            assert old.asset_hashes[name] != new.asset_hashes[name], name

    def test_unrelated_module_keeps_its_url(self, tmp_path):
        old, _ = self._build(tmp_path, "export const a = 1;\n")
        new, _ = self._build(tmp_path, "export const a = 2;\n")
        assert old.asset_hashes["other.js"] == new.asset_hashes["other.js"]

    def test_import_cycle_is_hashed_without_recursing_forever(self, tmp_path):
        static = tmp_path / "static"
        static.mkdir()
        (static / "a.js").write_text("import './b.js';\n", encoding="utf-8")
        (static / "b.js").write_text("import './a.js';\n", encoding="utf-8")
        first = BuildInfo(static, tmp_path).asset_hashes["a.js"]
        (static / "b.js").write_text("import './a.js'; // edit\n", encoding="utf-8")
        assert BuildInfo(static, tmp_path).asset_hashes["a.js"] != first

    def test_served_import_stamp_is_the_module_url_stamp(self, tmp_path):
        """The ``?v=`` an importer names must equal the stamp a client would
        compute for that module — one value, not two."""
        info, _ = self._build(tmp_path, "export const a = 1;\n")
        body = info.rewrite_js_imports("import './mid.js';\n")
        assert f"./mid.js?v={info.asset_hashes['mid.js']}" in body


class TestEntryRevalidation:
    """ETag + 304 on ``/`` so a relaunch doesn't re-download the page —
    perf-review finding, issue #212. A 304 must never outlive a build."""

    def test_index_carries_etag(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/")
        assert re.fullmatch(r'W/"[0-9a-f]{20}"', resp.headers.get("etag", ""))

    def test_matching_if_none_match_answers_304(self, webapp_client):
        client, _, _ = webapp_client
        etag = client.get("/").headers["etag"]
        resp = client.get("/", headers={"If-None-Match": etag})
        assert resp.status_code == 304
        assert resp.content == b""
        assert resp.headers["etag"] == etag
        # Still revalidates next launch, and a bodyless 304 is never gzipped.
        assert "no-cache" in resp.headers.get("cache-control", "")
        assert "content-encoding" not in resp.headers

    @pytest.mark.parametrize(
        "wrap", [lambda e: e[2:], lambda e: "*", lambda e: f'"zzz", {e}'],
        ids=["strong-form", "star", "list"],
    )
    def test_if_none_match_variants_answer_304(self, webapp_client, wrap):
        client, _, _ = webapp_client
        etag = client.get("/").headers["etag"]
        resp = client.get("/", headers={"If-None-Match": wrap(etag)})
        assert resp.status_code == 304

    def test_stale_if_none_match_answers_200_with_body(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/", headers={"If-None-Match": 'W/"0000000000"'})
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert resp.text

    def test_new_commit_invalidates_etag(self, webapp_client, monkeypatch):
        client, app, _ = webapp_client
        old = client.get("/").headers["etag"]
        monkeypatch.setattr(app.state.build_info, "git_sha", "feedbee")
        resp = client.get("/", headers={"If-None-Match": old})
        assert resp.status_code == 200
        assert resp.headers["etag"] != old

    def test_changed_transitive_module_invalidates_etag(
        self, webapp_client, monkeypatch
    ):
        """The stamped HTML only names app.js + CSS; an edit to a module
        reached through ``import`` must still move the validator."""
        client, app, _ = webapp_client
        old = client.get("/").headers["etag"]
        hashes = dict(app.state.build_info.asset_hashes)
        transitive = next(k for k in hashes if k not in _STAMPED_ASSETS)
        hashes[transitive] = "ffffffff"
        monkeypatch.setattr(app.state.build_info, "asset_hashes", hashes)
        resp = client.get("/", headers={"If-None-Match": old})
        assert resp.status_code == 200
        assert resp.headers["etag"] != old

    def test_edited_index_invalidates_etag(self, webapp_client, monkeypatch, tmp_path):
        client, _, _ = webapp_client
        from app.webapp.routers import misc

        old = client.get("/").headers["etag"]
        edited = tmp_path / "static"
        edited.mkdir()
        (edited / "index.html").write_text(
            (_STATIC_DIR / "index.html").read_text(encoding="utf-8") + "<!-- edit -->",
            encoding="utf-8",
        )
        monkeypatch.setattr(misc, "STATIC_DIR", edited)
        resp = client.get("/", headers={"If-None-Match": old})
        assert resp.status_code == 200
        assert resp.headers["etag"] != old


class TestCompression:
    """Entry document + static assets are gzipped for a phone on cellular
    — perf-review finding, issue #212."""

    def test_index_is_gzipped(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/", headers={"Accept-Encoding": "gzip"})
        assert resp.status_code == 200
        assert resp.headers.get("content-encoding") == "gzip"
        # The client transparently inflates; the stamped document is intact.
        assert re.search(r"/static/app\.js\?v=[0-9a-f]{8}", resp.text)

    def test_static_js_is_gzipped(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
        assert resp.headers.get("content-encoding") == "gzip"

    def test_no_gzip_without_accept_encoding(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/", headers={"Accept-Encoding": "identity"})
        assert "content-encoding" not in resp.headers

    def test_tiny_responses_stay_uncompressed(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/healthz", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in resp.headers


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
