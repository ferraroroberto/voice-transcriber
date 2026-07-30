"""Regression coverage for the `record`/`transcribe` `--language` flag.

Before voice-transcriber#164, both subcommands declared
`choices=LANGUAGE_MODES` — the legacy 3-entry tuple
`("english", "spanish", "italian")` — so any of the other 97
Whisper-supported languages (ISO code or English name) was rejected by
argparse with exit code 2, even though `resolve_iso()` (and the tk
window / webapp pickers) already accepted the full set.
"""

from __future__ import annotations

# Standard library imports
import argparse

# Third-party imports
import pytest

from app.cli.commands.base import language_type
from app.cli.main import _build_parser


class TestLanguageType:
    @pytest.mark.parametrize("value,expected", [
        ("en", "en"),
        ("de", "de"),
        ("haw", "haw"),
        ("yue", "yue"),
        ("german", "german"),
        ("english", "english"),
        ("ITALIAN", "ITALIAN"),
    ])
    def test_accepts_any_resolvable_language(self, value, expected):
        assert language_type(value) == expected

    @pytest.mark.parametrize("value", ["klingon", "zz", ""])
    def test_rejects_unresolvable_language(self, value):
        with pytest.raises(argparse.ArgumentTypeError):
            language_type(value)


class TestRecordTranscribeParsers:
    """End-to-end through the real CLI parser, not just the raw helper —
    pins the wiring (`add_parser` -> `type=language_type`), not only the
    validator function in isolation."""

    @pytest.mark.parametrize("command,extra_args", [
        ("record", []),
        ("transcribe", ["some_file.wav"]),
    ])
    @pytest.mark.parametrize("value", ["en", "de", "haw", "yue", "german"])
    def test_non_legacy_language_is_accepted(self, command, extra_args, value):
        parser = _build_parser()
        args = parser.parse_args([command, *extra_args, "--language", value])
        assert args.language == value

    def test_unresolvable_language_exits_with_usage_error(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["record", "--language", "klingon"])
        assert exc_info.value.code == 2
        assert "klingon" in capsys.readouterr().err
