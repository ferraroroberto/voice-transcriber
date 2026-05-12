"""Unit tests for `src/polish_prompts.py` — named system-prompt library."""

from __future__ import annotations

# Standard library imports
import json
from pathlib import Path

# Third-party imports
import pytest

# Local imports
from src.polish_prompts import (
    DEFAULT_PROMPT_ID,
    PolishPrompt,
    get_prompt,
    load_polish_prompts,
)


# ---------------------------------------------------------------------------
# load_polish_prompts — file present / absent / corrupt.
# ---------------------------------------------------------------------------

class TestLoadPolishPrompts:
    def test_missing_file_falls_back_to_builtins(self, tmp_path: Path):
        prompts = load_polish_prompts(tmp_path / "does-not-exist.json")
        assert len(prompts) == 1
        assert prompts[0].id == DEFAULT_PROMPT_ID
        assert "filler" in prompts[0].label.lower()

    def test_corrupt_json_falls_back_to_builtins(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        prompts = load_polish_prompts(bad)
        assert prompts[0].id == DEFAULT_PROMPT_ID

    def test_non_list_root_falls_back_to_builtins(self, tmp_path: Path):
        target = tmp_path / "object.json"
        target.write_text(json.dumps({"id": "x"}), encoding="utf-8")
        prompts = load_polish_prompts(target)
        assert prompts[0].id == DEFAULT_PROMPT_ID

    def test_empty_list_falls_back_to_builtins(self, tmp_path: Path):
        target = tmp_path / "empty.json"
        target.write_text("[]", encoding="utf-8")
        prompts = load_polish_prompts(target)
        assert prompts[0].id == DEFAULT_PROMPT_ID

    def test_valid_entries_loaded(self, tmp_path: Path):
        target = tmp_path / "good.json"
        target.write_text(
            json.dumps([
                {
                    "id": "filler-words",
                    "label": "Filler cleanup",
                    "description": "Remove filler",
                    "system": "You are a polisher.",
                },
                {
                    "id": "literal",
                    "label": "Verbatim",
                    "description": "No edits",
                    "system": "Echo verbatim.",
                },
            ]),
            encoding="utf-8",
        )
        prompts = load_polish_prompts(target)
        ids = [p.id for p in prompts]
        assert ids == ["filler-words", "literal"]

    def test_entries_missing_id_or_system_are_skipped(self, tmp_path: Path):
        target = tmp_path / "mixed.json"
        target.write_text(
            json.dumps([
                {"id": "", "system": "x"},          # missing id
                {"id": "ok", "system": ""},          # missing system
                {"id": "ok", "system": "real"},      # valid
                "not a dict",                        # wrong type
            ]),
            encoding="utf-8",
        )
        prompts = load_polish_prompts(target)
        assert len(prompts) == 1
        assert prompts[0].id == "ok"
        assert prompts[0].system == "real"

    def test_duplicate_ids_keep_first(self, tmp_path: Path):
        target = tmp_path / "dupes.json"
        target.write_text(
            json.dumps([
                {"id": "one", "system": "first"},
                {"id": "one", "system": "second"},
            ]),
            encoding="utf-8",
        )
        prompts = load_polish_prompts(target)
        assert len(prompts) == 1
        assert prompts[0].system == "first"

    def test_label_defaults_to_id(self, tmp_path: Path):
        target = tmp_path / "no-label.json"
        target.write_text(
            json.dumps([{"id": "raw", "system": "x"}]), encoding="utf-8"
        )
        prompts = load_polish_prompts(target)
        assert prompts[0].label == "raw"

    def test_committed_prompts_file_loads(self, project_root: Path):
        """Sanity: the file checked into config/ parses cleanly and
        contains at least the default 'filler-words' entry."""
        committed = project_root / "config" / "polish_prompts.json"
        if not committed.exists():
            pytest.skip("config/polish_prompts.json not present")
        prompts = load_polish_prompts(committed)
        ids = {p.id for p in prompts}
        assert DEFAULT_PROMPT_ID in ids


# ---------------------------------------------------------------------------
# get_prompt — resolve by id with fallback.
# ---------------------------------------------------------------------------

class TestGetPrompt:
    def _sample(self):
        return [
            PolishPrompt(
                id="filler-words", label="L1", description="D1", system="S1"
            ),
            PolishPrompt(
                id="literal", label="L2", description="D2", system="S2"
            ),
        ]

    def test_finds_matching_id(self):
        out = get_prompt("literal", prompts=self._sample())
        assert out.id == "literal"

    def test_unknown_id_falls_back_to_first(self):
        out = get_prompt("nonexistent", prompts=self._sample())
        assert out.id == "filler-words"

    def test_none_id_falls_back_to_first(self):
        out = get_prompt(None, prompts=self._sample())
        assert out.id == "filler-words"

    def test_empty_string_id_falls_back_to_first(self):
        out = get_prompt("", prompts=self._sample())
        assert out.id == "filler-words"


# ---------------------------------------------------------------------------
# PolishPrompt dataclass
# ---------------------------------------------------------------------------

class TestPolishPromptDataclass:
    def test_is_frozen(self):
        p = PolishPrompt(id="x", label="X", description="D", system="S")
        with pytest.raises((AttributeError, Exception)):
            p.id = "y"  # type: ignore[misc]
