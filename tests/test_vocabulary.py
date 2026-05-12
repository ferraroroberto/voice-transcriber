"""Unit tests for `src/vocabulary.py` — language-keyed whisper prompts."""

from __future__ import annotations

# Standard library imports
import json
from pathlib import Path

# Third-party imports
import pytest

# Local imports
import src.vocabulary as vocab


@pytest.fixture(autouse=True)
def isolate_vocab(monkeypatch, tmp_path: Path):
    """Point the module at a temp file and clear its mtime cache so
    tests don't read whatever the developer happens to have in
    config/vocabulary.json."""
    target = tmp_path / "vocabulary.json"
    monkeypatch.setattr(vocab, "_VOCAB_PATH", target)
    monkeypatch.setattr(vocab, "_CACHE", (0.0, {}))
    yield target


def _write(target: Path, payload):
    target.write_text(json.dumps(payload), encoding="utf-8")


class TestPromptForLanguage:
    def test_returns_none_when_file_missing(self):
        assert vocab.prompt_for_language("en") is None

    def test_returns_none_when_file_empty_object(self, isolate_vocab):
        _write(isolate_vocab, {})
        assert vocab.prompt_for_language("en") is None

    def test_merges_all_bucket_into_language(self, isolate_vocab):
        _write(isolate_vocab, {
            "all": ["Roberto", "Ferraro"],
            "en": ["Anthropic", "Claude"],
        })
        out = vocab.prompt_for_language("en")
        assert out is not None
        # `all` comes first, then language-specific.
        assert out == "Roberto, Ferraro, Anthropic, Claude"

    def test_iso_code_is_case_insensitive(self, isolate_vocab):
        _write(isolate_vocab, {"en": ["whisper.cpp"]})
        assert vocab.prompt_for_language("EN") == "whisper.cpp"

    def test_missing_language_just_uses_all(self, isolate_vocab):
        _write(isolate_vocab, {"all": ["Roberto"], "es": ["hola"]})
        assert vocab.prompt_for_language("en") == "Roberto"

    def test_deduplicates_terms(self, isolate_vocab):
        _write(isolate_vocab, {
            "all": ["Anthropic"],
            "en": ["Anthropic", "Claude"],
        })
        assert vocab.prompt_for_language("en") == "Anthropic, Claude"

    def test_iso_none_just_returns_all(self, isolate_vocab):
        _write(isolate_vocab, {"all": ["Global"]})
        assert vocab.prompt_for_language(None) == "Global"

    def test_strips_whitespace_and_drops_empty(self, isolate_vocab):
        _write(isolate_vocab, {"en": ["  Anthropic  ", "", "  "]})
        assert vocab.prompt_for_language("en") == "Anthropic"

    def test_invalid_value_returns_none(self, isolate_vocab):
        isolate_vocab.write_text("not json", encoding="utf-8")
        assert vocab.prompt_for_language("en") is None

    def test_non_dict_root_returns_none(self, isolate_vocab):
        _write(isolate_vocab, ["list", "not", "dict"])
        assert vocab.prompt_for_language("en") is None

    def test_non_list_values_are_skipped(self, isolate_vocab):
        _write(isolate_vocab, {"en": "should-be-list", "es": ["válido"]})
        assert vocab.prompt_for_language("en") is None
        # The valid bucket still works.
        assert vocab.prompt_for_language("es") == "válido"


class TestCacheReload:
    def test_cache_hot_reloads_on_mtime_change(self, isolate_vocab):
        import os, time
        _write(isolate_vocab, {"en": ["one"]})
        # First call primes the cache.
        assert vocab.prompt_for_language("en") == "one"
        # Update the contents, then bump the mtime — bumping mtime AFTER
        # write because _write itself touches the file (writing resets
        # mtime to now). The cache key compares mtime, so we need it to
        # differ from the cached value.
        _write(isolate_vocab, {"en": ["two"]})
        future = time.time() + 10
        os.utime(isolate_vocab, (future, future))
        assert vocab.prompt_for_language("en") == "two"
