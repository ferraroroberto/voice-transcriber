"""Unit tests for `src/snippets.py` — keyword expansion."""

from __future__ import annotations

# Standard library imports
import json
import os
import time
from pathlib import Path

# Third-party imports
import pytest

# Local imports
import src.snippets as snippets


@pytest.fixture(autouse=True)
def isolate_snippets(tmp_path: Path):
    target = tmp_path / "snippets.json"
    snippets._loader.reset(target)
    yield target


def _write(target: Path, payload):
    target.write_text(json.dumps(payload), encoding="utf-8")


class TestApplySnippets:
    def test_returns_text_unchanged_when_no_file(self):
        assert snippets.apply_snippets("hello ttyl") == "hello ttyl"

    def test_returns_text_unchanged_when_empty_input(self):
        assert snippets.apply_snippets("") == ""

    def test_replaces_known_key(self, isolate_snippets):
        _write(isolate_snippets, {"ttyl": "talk to you later"})
        assert snippets.apply_snippets("see you, ttyl") == "see you, talk to you later"

    def test_case_insensitive_match_preserves_replacement(self, isolate_snippets):
        _write(isolate_snippets, {"ttyl": "talk to you later"})
        # Upper / mixed case still matches and expands to the canonical value.
        assert "talk to you later" in snippets.apply_snippets("TTYL friend")
        assert "talk to you later" in snippets.apply_snippets("Ttyl friend")

    def test_word_boundary_does_not_match_inside_word(self, isolate_snippets):
        _write(isolate_snippets, {"my": "MINE"})
        # `my` inside `myself` should NOT expand.
        assert snippets.apply_snippets("myself") == "myself"
        # Standalone `my` does expand.
        assert snippets.apply_snippets("my book") == "MINE book"

    def test_longer_key_wins_when_both_match(self, isolate_snippets):
        _write(isolate_snippets, {
            "foo": "SHORT",
            "foo bar": "LONG",
        })
        assert snippets.apply_snippets("hello foo bar end") == "hello LONG end"

    def test_unknown_keys_left_alone(self, isolate_snippets):
        _write(isolate_snippets, {"ttyl": "talk to you later"})
        assert snippets.apply_snippets("brb later") == "brb later"

    def test_invalid_json_returns_text_unchanged(self, isolate_snippets):
        isolate_snippets.write_text("not json", encoding="utf-8")
        assert snippets.apply_snippets("ttyl") == "ttyl"

    def test_non_dict_root_ignored(self, isolate_snippets):
        _write(isolate_snippets, ["not", "a", "dict"])
        assert snippets.apply_snippets("ttyl") == "ttyl"

    def test_empty_mapping_returns_text_unchanged(self, isolate_snippets):
        _write(isolate_snippets, {})
        assert snippets.apply_snippets("ttyl") == "ttyl"

    def test_blank_keys_are_dropped(self, isolate_snippets):
        _write(isolate_snippets, {"   ": "blank", "ttyl": "ok"})
        assert snippets.apply_snippets("ttyl") == "ok"


class TestCacheHotReload:
    def test_mtime_change_reloads_pattern(self, isolate_snippets):
        _write(isolate_snippets, {"ttyl": "v1"})
        assert snippets.apply_snippets("ttyl") == "v1"
        # Write THEN bump mtime — writing the file resets mtime to now,
        # so the bump must come last for the cache key to differ.
        _write(isolate_snippets, {"ttyl": "v2"})
        future = time.time() + 10
        os.utime(isolate_snippets, (future, future))
        assert snippets.apply_snippets("ttyl") == "v2"
