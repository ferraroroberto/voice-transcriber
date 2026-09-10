"""Unit tests for `src/disfluency.py` — spoken filler-word strip (issue #198)."""

from __future__ import annotations

# Standard library imports
import json
from pathlib import Path

# Third-party imports
import pytest

# Local imports
import src.disfluency as df


@pytest.fixture(autouse=True)
def isolate_config(tmp_path: Path):
    """Point the module at a temp config and clear its mtime cache so tests
    never read whatever config/disfluencies.json the developer happens to
    have. With no file written, the built-in defaults apply."""
    target = tmp_path / "disfluencies.json"
    df._loader.reset(target)
    yield target


def _write(target: Path, payload) -> None:
    target.write_text(json.dumps(payload), encoding="utf-8")


class TestDefaults:
    def test_strips_uh_mid_sentence(self):
        assert (
            df.strip_disfluencies("I want to make uh a fun infographic")
            == "I want to make a fun infographic"
        )

    def test_strips_um(self):
        assert df.strip_disfluencies("and um yeah the whole thing") == "and yeah the whole thing"

    def test_strips_elongations(self):
        assert df.strip_disfluencies("some uhhh tedious work") == "some tedious work"
        assert df.strip_disfluencies("and ummm okay") == "and okay"

    def test_collapses_a_run_of_fillers(self):
        assert (
            df.strip_disfluencies("the message that is uh uh clearly wrong")
            == "the message that is clearly wrong"
        )

    def test_collapses_a_comma_separated_run(self):
        assert df.strip_disfluencies("well um, uh okay then") == "well okay then"

    def test_strips_mmm_but_not_bare_mm(self):
        assert df.strip_disfluencies("some mmm some tedious") == "some some tedious"
        assert df.strip_disfluencies("advance on the MM report") == "advance on the MM report"

    def test_leaves_oh_and_ah_alone(self):
        # Genuine discourse markers — excluded from the built-in list on purpose.
        text = "Oh, and by the way, he said ah it is probably fine."
        assert df.strip_disfluencies(text) == text

    def test_leaves_filler_free_text_byte_identical(self):
        text = "Okay, the report is ready for review."
        assert df.strip_disfluencies(text) is text

    def test_does_not_match_inside_words(self):
        text = "The uhuru movement and a huh-style hummus recipe"
        assert df.strip_disfluencies(text) == text

    def test_empty_text_is_returned_unchanged(self):
        assert df.strip_disfluencies("") == ""


class TestPunctuationAndCase:
    def test_leading_filler_takes_its_comma_and_hands_over_the_capital(self):
        assert (
            df.strip_disfluencies("Uh, instead of a trainee, we give you a job.")
            == "Instead of a trainee, we give you a job."
        )

    def test_sentence_opening_filler_after_a_period(self):
        assert (
            df.strip_disfluencies("That was the plan. Uh, then everything changed.")
            == "That was the plan. Then everything changed."
        )

    def test_filler_as_its_own_sentence_leaves_no_orphan_period(self):
        assert (
            df.strip_disfluencies("Uh. Instead of a trainee, a job.")
            == "Instead of a trainee, a job."
        )

    def test_mid_sentence_filler_before_a_period_keeps_the_period(self):
        assert df.strip_disfluencies("that is the whole story uh.") == "that is the whole story."

    def test_no_double_spaces_left_behind(self):
        out = df.strip_disfluencies("one uh two uh three")
        assert out == "one two three"
        assert "  " not in out

    def test_capital_carries_past_a_non_letter_chunk(self):
        assert df.strip_disfluencies("Uh, 3 of them left.") == "3 of them left."

    def test_fillers_only_take_is_never_emptied(self):
        assert df.strip_disfluencies("Uh, um, uh.") == "Uh, um, uh."

    def test_sentence_opening_filler_takes_a_whole_ellipsis(self):
        # Swallowing one dot of three would leave a worse artefact than the
        # filler ("Um... what" -> ".. what").
        assert df.strip_disfluencies("Um... what do you mean?") == "What do you mean?"

    def test_no_space_left_dangling_before_a_newline(self):
        assert df.strip_disfluencies("line one uh\nline two") == "line one\nline two"


class TestConfig:
    def test_disabled_keeps_verbatim_text(self, isolate_config: Path):
        _write(isolate_config, {"enabled": False})
        text = "I want to make uh a fun infographic"
        assert df.strip_disfluencies(text) is text

    def test_custom_list_replaces_the_builtin_one(self, isolate_config: Path):
        _write(isolate_config, {"fillers": ["oh"]})
        # "oh" now strips; "uh" no longer does — the list replaces, not extends.
        assert df.strip_disfluencies("well oh yes uh really") == "well yes uh really"

    def test_empty_list_strips_nothing(self, isolate_config: Path):
        _write(isolate_config, {"fillers": []})
        text = "I want to make uh a fun infographic"
        assert df.strip_disfluencies(text) is text

    def test_hot_reload_picks_up_an_edit(self, isolate_config: Path):
        _write(isolate_config, {"fillers": ["uh"]})
        assert df.strip_disfluencies("well uh okay") == "well okay"
        _write(isolate_config, {"enabled": False})
        # mtime changed → the loader re-reads without a restart.
        assert df.strip_disfluencies("well uh okay") == "well uh okay"

    def test_malformed_config_falls_back_to_defaults(self, isolate_config: Path):
        isolate_config.write_text("{not json", encoding="utf-8")
        assert df.strip_disfluencies("well uh okay") == "well okay"

    def test_wrong_shape_falls_back_to_defaults(self, isolate_config: Path):
        _write(isolate_config, ["uh", "um"])
        assert df.strip_disfluencies("well uh okay") == "well okay"

    def test_null_in_the_list_does_not_become_the_word_none(self, isolate_config: Path):
        # str(None) would pass the isalpha/length guards and start stripping
        # the real word "none" — non-strings must be rejected outright.
        _write(isolate_config, {"fillers": [None, "uh"]})
        assert df.strip_disfluencies("none of them uh left") == "none of them left"

    def test_single_letter_terms_are_rejected(self, isolate_config: Path):
        # "a" would compile to `a+` and eat every article — must be ignored.
        _write(isolate_config, {"fillers": ["a", "uh"]})
        assert df.strip_disfluencies("a uh plan") == "a plan"


class TestSampleConfig:
    def test_committed_sample_matches_the_builtin_defaults(self):
        sample = (
            Path(__file__).resolve().parent.parent / "config" / "disfluencies.sample.json"
        )
        payload = json.loads(sample.read_text(encoding="utf-8"))
        assert payload["enabled"] is True
        assert payload["fillers"] == list(df.DEFAULT_FILLERS)
