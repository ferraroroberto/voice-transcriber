"""Unit tests for `src/app_config.py` — top-level app config + language map."""

from __future__ import annotations

# Standard library imports
import json
from pathlib import Path

# Third-party imports
import pytest

# Local imports
from src.app_config import (
    AppConfig,
    LANGUAGE_MODES,
    VALID_LOG_LEVELS,
    WHISPER_LANGUAGES,
    load_app_config,
    resolve_iso,
)


# ---------------------------------------------------------------------------
# resolve_iso — language name normalisation
# ---------------------------------------------------------------------------

class TestResolveIso:
    @pytest.mark.parametrize("inp,expected", [
        ("en", "en"),
        ("es", "es"),
        ("haw", "haw"),
        ("English", "en"),
        ("english", "en"),
        ("Spanish", "es"),
        ("ITALIAN", "it"),
    ])
    def test_known_values(self, inp, expected):
        assert resolve_iso(inp) == expected

    @pytest.mark.parametrize("inp", [None, "", "   ", "klingon", "zz"])
    def test_unknown_values(self, inp):
        assert resolve_iso(inp) is None

    def test_legacy_mode_names_resolve(self):
        for mode in LANGUAGE_MODES:
            assert resolve_iso(mode) is not None


# ---------------------------------------------------------------------------
# WHISPER_LANGUAGES — 99-entry static map
# ---------------------------------------------------------------------------

class TestWhisperLanguages:
    def test_size_matches_whisper_cpp_table(self):
        # whisper.cpp's g_lang table — keep above the conservative
        # floor so an accidental mass-deletion is caught, but allow
        # additions when upstream whisper.cpp grows the table.
        assert len(WHISPER_LANGUAGES) >= 99

    def test_contains_core_languages(self):
        for iso in ("en", "es", "it", "fr", "de", "zh", "ja"):
            assert iso in WHISPER_LANGUAGES

    def test_all_labels_are_strings(self):
        for iso, label in WHISPER_LANGUAGES.items():
            assert isinstance(iso, str) and iso
            assert isinstance(label, str) and label


# ---------------------------------------------------------------------------
# AppConfig defaults + properties
# ---------------------------------------------------------------------------

class TestAppConfigDefaults:
    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.language == "english"
        assert cfg.max_record_seconds == 300
        assert cfg.sample_rate == 16000
        assert cfg.hotkey == "<ctrl>+<alt>+<space>"
        assert cfg.auto_copy is True
        assert cfg.show_notifications is True
        assert cfg.log_level == "INFO"

    def test_whisper_language_resolves_default(self):
        assert AppConfig().whisper_language == "en"

    def test_whisper_language_returns_none_for_unknown(self):
        assert AppConfig(language="klingon").whisper_language is None

    def test_hotkey_label_capitalises(self):
        assert AppConfig(hotkey="<ctrl>+<alt>+<space>").hotkey_label == "Ctrl+Alt+Space"

    def test_hotkey_label_uppercases_single_chars(self):
        assert AppConfig(hotkey="<f>+a").hotkey_label == "F+A"

    def test_enabled_language_map_full_when_none(self):
        m = AppConfig().enabled_language_map()
        assert m == dict(WHISPER_LANGUAGES)
        assert m["en"] == "English"

    def test_enabled_language_map_filters_to_allowlist(self):
        cfg = AppConfig(enabled_languages=["en", "es"])
        m = cfg.enabled_language_map()
        assert set(m.keys()) == {"en", "es"}

    def test_enabled_language_map_silently_drops_unknown(self):
        cfg = AppConfig(enabled_languages=["en", "zz_unknown"])
        m = cfg.enabled_language_map()
        assert set(m.keys()) == {"en"}


class TestResolvePreferredMics:
    def test_explicit_list_wins(self):
        cfg = AppConfig(preferred_mics=["MicA"])
        assert cfg.resolve_preferred_mics() == ["MicA"]

    def test_machine_map_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setattr("src.app_config._machine_name", lambda: "test-host")
        cfg = AppConfig(
            preferred_mics=None,
            machine_specific_mics={"test-host": ["MicB"]},
        )
        assert cfg.resolve_preferred_mics() == ["MicB"]

    def test_empty_when_no_match(self, monkeypatch):
        monkeypatch.setattr("src.app_config._machine_name", lambda: "other")
        cfg = AppConfig(machine_specific_mics={"test-host": ["MicB"]})
        assert cfg.resolve_preferred_mics() == []


# ---------------------------------------------------------------------------
# load_app_config — file + validation
# ---------------------------------------------------------------------------

class TestLoadAppConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = load_app_config(tmp_path / "absent.json")
        assert cfg.language == "english"

    def test_loads_minimal_config(self, tmp_path):
        target = tmp_path / "c.json"
        target.write_text(
            json.dumps({"language": "spanish", "log_level": "DEBUG"}),
            encoding="utf-8",
        )
        cfg = load_app_config(target)
        assert cfg.language == "spanish"
        assert cfg.log_level == "DEBUG"

    def test_rejects_unknown_language(self, tmp_path):
        target = tmp_path / "c.json"
        target.write_text(
            json.dumps({"language": "klingon"}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="language"):
            load_app_config(target)

    def test_rejects_invalid_log_level(self, tmp_path):
        target = tmp_path / "c.json"
        target.write_text(
            json.dumps({"log_level": "VERBOSE"}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="log_level"):
            load_app_config(target)

    def test_rejects_non_positive_max_seconds(self, tmp_path):
        target = tmp_path / "c.json"
        target.write_text(
            json.dumps({"max_record_seconds": 0}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="max_record_seconds"):
            load_app_config(target)

    def test_rejects_enabled_languages_with_unknown_iso(self, tmp_path):
        target = tmp_path / "c.json"
        target.write_text(
            json.dumps({"enabled_languages": ["en", "klingon"]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="unknown ISO"):
            load_app_config(target)

    def test_rejects_language_not_in_enabled_list(self, tmp_path):
        target = tmp_path / "c.json"
        target.write_text(
            json.dumps({
                "language": "english",
                "enabled_languages": ["es", "fr"],
            }),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="not in"):
            load_app_config(target)

    def test_rejects_non_string_enabled_languages(self, tmp_path):
        target = tmp_path / "c.json"
        target.write_text(
            json.dumps({"enabled_languages": ["en", 42]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="list of ISO"):
            load_app_config(target)

    def test_valid_log_levels_are_canonical(self):
        assert VALID_LOG_LEVELS == ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
