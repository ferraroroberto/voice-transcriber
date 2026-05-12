"""Unit tests for `src/webapp_config.py`.

The module just got refactored so polish-model defaults live in the
committed sample JSON, not in Python literals. These tests pin that
contract: editing `config/webapp_config.sample.json` must be enough
to surface a new model — no Python change required.
"""

from __future__ import annotations

# Standard library imports
import json
from pathlib import Path

# Third-party imports
import pytest

# Local imports
from src.webapp_config import (
    DEFAULT_HOST,
    DEFAULT_LLM_HUB_URL,
    DEFAULT_PORT,
    DEFAULT_POLISH_PROMPT_ID,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SILENCE_DBFS_THRESHOLD,
    SAMPLE_CONFIG_PATH,
    WebappConfig,
    _sample_polish_defaults,
    _validate,
    append_auth_token,
    load_webapp_config,
    save_webapp_config,
    update_webapp_config,
)


# ---------------------------------------------------------------------------
# Source-of-truth contract: no model-name literals in Python.
# ---------------------------------------------------------------------------

class TestNoModelLiteralsInPython:
    """The refactor's whole point: hub aliases live in JSON, not .py."""

    def test_sample_config_is_committed(self):
        assert SAMPLE_CONFIG_PATH.exists(), (
            "config/webapp_config.sample.json must be committed — it is "
            "the source of truth for first-run polish-model defaults."
        )

    def test_sample_lists_six_aliases_with_gemini_flash_default(
        self, sample_polish_payload
    ):
        # Soft pin: if the user evolves the list, only this assertion
        # needs updating — no test elsewhere encodes the alias names.
        assert sample_polish_payload["polish_model_default"] == "gemini_flash"
        assert sample_polish_payload["polish_models_available"] == [
            "claude_haiku",
            "claude_sonnet",
            "claude_opus",
            "gemini_lite",
            "gemini_flash",
            "gemini_pro",
        ]

    def test_python_module_does_not_hardcode_model_names(self):
        """Grep src/webapp_config.py for the known alias strings — none
        should appear. This guards against regressions where someone
        re-adds a `DEFAULT_POLISH_MODEL = "gemini_flash"` constant."""
        module_src = (
            Path(__file__).resolve().parent.parent
            / "src" / "webapp_config.py"
        ).read_text(encoding="utf-8")
        forbidden = [
            '"claude_haiku"', '"claude_sonnet"', '"claude_opus"',
            '"gemini_lite"', '"gemini_flash"', '"gemini_pro"',
            "'claude_haiku'", "'gemini_flash'",
        ]
        for token in forbidden:
            assert token not in module_src, (
                f"webapp_config.py must not embed {token!r} — model "
                f"names belong in config/webapp_config.sample.json so "
                f"the list can evolve without a code change."
            )


# ---------------------------------------------------------------------------
# _sample_polish_defaults() — the bridge between JSON and Python.
# ---------------------------------------------------------------------------

class TestSamplePolishDefaults:
    def test_reads_committed_sample(self, sample_polish_payload):
        default, available = _sample_polish_defaults()
        assert default == sample_polish_payload["polish_model_default"]
        assert available == sample_polish_payload["polish_models_available"]

    def test_returns_empty_when_sample_is_unreadable(self, monkeypatch, tmp_path):
        """When the committed sample is missing/corrupt, we return
        empty defaults rather than crashing — first-run on a botched
        checkout still boots, just with an empty dropdown."""
        bad = tmp_path / "does-not-exist.json"
        monkeypatch.setattr("src.webapp_config.SAMPLE_CONFIG_PATH", bad)
        assert _sample_polish_defaults() == ("", [])

    def test_returns_empty_when_sample_is_not_json(
        self, monkeypatch, tmp_path
    ):
        corrupt = tmp_path / "bad.json"
        corrupt.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr("src.webapp_config.SAMPLE_CONFIG_PATH", corrupt)
        assert _sample_polish_defaults() == ("", [])


# ---------------------------------------------------------------------------
# WebappConfig() default construction.
# ---------------------------------------------------------------------------

class TestDefaultWebappConfig:
    def test_defaults_mirror_sample(self, sample_polish_payload):
        cfg = WebappConfig()
        assert cfg.polish_model_default == sample_polish_payload["polish_model_default"]
        assert cfg.polish_models_available == sample_polish_payload["polish_models_available"]
        assert cfg.polish_prompt_default == DEFAULT_POLISH_PROMPT_ID
        assert cfg.llm_hub_url == DEFAULT_LLM_HUB_URL
        assert cfg.host == DEFAULT_HOST
        assert cfg.port == DEFAULT_PORT
        assert cfg.history_retention_days == DEFAULT_RETENTION_DAYS
        assert cfg.silence_dbfs_threshold == DEFAULT_SILENCE_DBFS_THRESHOLD
        assert cfg.auth_token == ""
        assert cfg.auth_password == ""

    def test_each_instance_gets_its_own_models_list(self):
        """Catch the field-default-mutable-default footgun."""
        a, b = WebappConfig(), WebappConfig()
        a.polish_models_available.append("zzz_test")
        assert "zzz_test" not in b.polish_models_available


# ---------------------------------------------------------------------------
# load_webapp_config — first-run and override paths.
# ---------------------------------------------------------------------------

class TestLoadWebappConfig:
    def test_missing_file_falls_back_to_defaults(
        self, tmp_path, sample_polish_payload
    ):
        missing = tmp_path / "absent.json"
        cfg = load_webapp_config(missing)
        assert cfg.polish_model_default == sample_polish_payload["polish_model_default"]
        assert cfg.polish_models_available == sample_polish_payload["polish_models_available"]

    def test_corrupt_file_falls_back_to_defaults(
        self, tmp_path, sample_polish_payload
    ):
        target = tmp_path / "broken.json"
        target.write_text("not json", encoding="utf-8")
        cfg = load_webapp_config(target)
        # _validate runs only on the happy path; on parse failure we
        # get a fresh WebappConfig() with sample-derived models.
        assert cfg.polish_model_default == sample_polish_payload["polish_model_default"]

    def test_explicit_overrides_win(self, tmp_path, sample_polish_payload):
        target = tmp_path / "cfg.json"
        payload = {
            "polish_model_default": sample_polish_payload["polish_models_available"][0],
            "polish_models_available": sample_polish_payload["polish_models_available"],
            "polish_prompt_default": "custom-style",
            "llm_hub_url": "http://example.invalid:9999",
            "host": "127.0.0.1",
            "port": 9000,
            "history_retention_days": 7,
            "force_builtin_mic_default": True,
            "preferred_mic_id": "mic-xyz",
            "auth_token": "deadbeef",
            "auth_password": "shibboleth",
            "silence_dbfs_threshold": -42.0,
        }
        target.write_text(json.dumps(payload), encoding="utf-8")
        cfg = load_webapp_config(target)
        assert cfg.polish_model_default == payload["polish_model_default"]
        assert cfg.polish_prompt_default == "custom-style"
        assert cfg.llm_hub_url == "http://example.invalid:9999"
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9000
        assert cfg.history_retention_days == 7
        assert cfg.force_builtin_mic_default is True
        assert cfg.preferred_mic_id == "mic-xyz"
        assert cfg.auth_token == "deadbeef"
        assert cfg.auth_password == "shibboleth"
        assert cfg.silence_dbfs_threshold == pytest.approx(-42.0)

    def test_missing_keys_fall_back_to_sample(
        self, tmp_path, sample_polish_payload
    ):
        """User's config might be partial — keys not present should
        bootstrap from the sample, not from a Python literal."""
        target = tmp_path / "partial.json"
        target.write_text(json.dumps({"port": 4444}), encoding="utf-8")
        cfg = load_webapp_config(target)
        assert cfg.port == 4444
        assert cfg.polish_model_default == sample_polish_payload["polish_model_default"]
        assert cfg.polish_models_available == sample_polish_payload["polish_models_available"]

    def test_empty_models_array_falls_back_to_sample(
        self, tmp_path, sample_polish_payload
    ):
        """`polish_models_available: []` (or null) means "use the
        sample" — useful when the user wants to reset to the default
        without deleting the whole file."""
        target = tmp_path / "empty.json"
        target.write_text(
            json.dumps({
                "polish_model_default": sample_polish_payload["polish_model_default"],
                "polish_models_available": [],
            }),
            encoding="utf-8",
        )
        cfg = load_webapp_config(target)
        assert cfg.polish_models_available == sample_polish_payload["polish_models_available"]

    def test_validation_rejects_default_outside_available(self, tmp_path):
        target = tmp_path / "bad.json"
        target.write_text(
            json.dumps({
                "polish_model_default": "claude_haiku",
                "polish_models_available": ["gemini_pro"],
            }),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="polish_model_default"):
            load_webapp_config(target)


# ---------------------------------------------------------------------------
# save_webapp_config — round-trip & atomic write.
# ---------------------------------------------------------------------------

class TestSaveWebappConfig:
    def test_round_trip(self, tmp_path, sample_polish_payload):
        target = tmp_path / "cfg.json"
        cfg = WebappConfig(
            polish_model_default=sample_polish_payload["polish_model_default"],
            polish_models_available=list(sample_polish_payload["polish_models_available"]),
            auth_token="abc123",
            port=8888,
        )
        save_webapp_config(cfg, target)
        loaded = load_webapp_config(target)
        assert loaded.auth_token == "abc123"
        assert loaded.port == 8888
        assert loaded.polish_model_default == cfg.polish_model_default

    def test_creates_parent_dir(self, tmp_path, sample_polish_payload):
        target = tmp_path / "deep" / "nested" / "cfg.json"
        cfg = WebappConfig(
            polish_model_default=sample_polish_payload["polish_model_default"],
            polish_models_available=list(sample_polish_payload["polish_models_available"]),
        )
        save_webapp_config(cfg, target)
        assert target.exists()

    def test_atomic_write_does_not_leave_tmp_on_success(
        self, tmp_path, sample_polish_payload
    ):
        target = tmp_path / "cfg.json"
        cfg = WebappConfig(
            polish_model_default=sample_polish_payload["polish_model_default"],
            polish_models_available=list(sample_polish_payload["polish_models_available"]),
        )
        save_webapp_config(cfg, target)
        assert target.exists()
        assert not target.with_suffix(target.suffix + ".tmp").exists()


# ---------------------------------------------------------------------------
# update_webapp_config — read/patch/save.
# ---------------------------------------------------------------------------

class TestUpdateWebappConfig:
    def test_patches_in_place(self, tmp_path, monkeypatch, sample_polish_payload):
        target = tmp_path / "cfg.json"
        seed = WebappConfig(
            polish_model_default=sample_polish_payload["polish_model_default"],
            polish_models_available=list(sample_polish_payload["polish_models_available"]),
        )
        save_webapp_config(seed, target)
        monkeypatch.setattr("src.webapp_config.DEFAULT_CONFIG_PATH", target)

        patched = update_webapp_config(port=7777)
        assert patched.port == 7777
        reloaded = load_webapp_config(target)
        assert reloaded.port == 7777


# ---------------------------------------------------------------------------
# _validate — invariants enforced on every load/save.
# ---------------------------------------------------------------------------

class TestValidate:
    def test_default_must_be_in_available(self):
        bad = WebappConfig(
            polish_model_default="claude_haiku",
            polish_models_available=["gemini_pro"],
        )
        with pytest.raises(ValueError, match="polish_model_default"):
            _validate(bad)

    def test_retention_must_be_positive(self, sample_polish_payload):
        bad = WebappConfig(
            polish_model_default=sample_polish_payload["polish_model_default"],
            polish_models_available=list(sample_polish_payload["polish_models_available"]),
            history_retention_days=0,
        )
        with pytest.raises(ValueError, match="history_retention_days"):
            _validate(bad)

    def test_port_must_be_in_range(self, sample_polish_payload):
        for bad_port in (0, -1, 70000):
            cfg = WebappConfig(
                polish_model_default=sample_polish_payload["polish_model_default"],
                polish_models_available=list(sample_polish_payload["polish_models_available"]),
                port=bad_port,
            )
            with pytest.raises(ValueError, match="port"):
                _validate(cfg)


# ---------------------------------------------------------------------------
# append_auth_token — URL helper used by the tray "copy mobile URL".
# ---------------------------------------------------------------------------

class TestAppendAuthToken:
    def test_no_token_is_passthrough(self):
        url = "https://example.com/path"
        assert append_auth_token(url, "") == url
        assert append_auth_token(url, None) == url

    def test_appends_token_when_query_is_empty(self):
        out = append_auth_token("https://x.com/p", "secret")
        assert out == "https://x.com/p?token=secret"

    def test_preserves_existing_query(self):
        out = append_auth_token("https://x.com/p?a=1", "secret")
        assert out == "https://x.com/p?a=1&token=secret"

    def test_urlencodes_the_token(self):
        out = append_auth_token("https://x.com/p", "a b+c/d")
        # urlencode escapes the space and the plus; the slash escapes
        # to %2F. The exact encoding is what tests pin.
        assert "token=a+b%2Bc%2Fd" in out
