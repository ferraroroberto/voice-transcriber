"""Unit tests for `src/speaker_label.py` — fabricated speaker-label strip."""

from __future__ import annotations

# Standard library imports
import json
from pathlib import Path

# Third-party imports
import pytest

# Local imports
import src.speaker_label as sl


@pytest.fixture(autouse=True)
def isolate_blocklist(monkeypatch, tmp_path: Path):
    """Point the module at a temp blocklist and clear its mtime cache so
    tests don't read whatever config/speaker_blocklist.json the developer
    happens to have."""
    target = tmp_path / "speaker_blocklist.json"
    monkeypatch.setattr(sl, "_BLOCKLIST_PATH", target)
    monkeypatch.setattr(sl, "_CACHE", (0.0, None))
    yield target


def _write(target: Path, payload) -> None:
    target.write_text(json.dumps(payload), encoding="utf-8")


class TestTitledLabels:
    def test_strips_name_with_phd(self):
        assert (
            sl.strip_speaker_label("Claudio Couto, Ph.D.: Okay, let me understand.")
            == "Okay, let me understand."
        )

    def test_strips_phd_without_periods(self):
        assert sl.strip_speaker_label("Jane Roe, PhD: hello there") == "hello there"

    def test_strips_md(self):
        assert sl.strip_speaker_label("John Smith, M.D.: take two pills") == "take two pills"

    def test_strips_dr_prefix(self):
        assert sl.strip_speaker_label("Dr. Smith: the results are in") == "the results are in"

    def test_strips_professor_prefix(self):
        assert sl.strip_speaker_label("Professor Jones: today we cover") == "today we cover"

    def test_strips_speaker_diarization_tag(self):
        assert sl.strip_speaker_label("Speaker 1: hello") == "hello"
        assert sl.strip_speaker_label("SPEAKER 02: hi") == "hi"


class TestNegativeCases:
    def test_bare_name_colon_is_kept(self):
        # No title, not blocklisted — could be real dictation.
        assert sl.strip_speaker_label("Roberto: call me tomorrow") == "Roberto: call me tomorrow"

    def test_sentence_with_colon_is_kept(self):
        assert sl.strip_speaker_label("Today: I woke up early") == "Today: I woke up early"

    def test_label_only_no_body_is_kept(self):
        # Stripping would empty the text — leave it untouched.
        assert sl.strip_speaker_label("Speaker 1:") == "Speaker 1:"
        assert sl.strip_speaker_label("Dr. Smith: ") == "Dr. Smith: "

    def test_empty_text(self):
        assert sl.strip_speaker_label("") == ""

    def test_no_label_passthrough(self):
        assert sl.strip_speaker_label("just a plain transcript") == "just a plain transcript"

    def test_colon_mid_sentence_untouched(self):
        text = "I have a Ph.D. in this: it is hard"
        assert sl.strip_speaker_label(text) == text


class TestBlocklist:
    def test_strips_blocklisted_bare_name(self, isolate_blocklist):
        _write(isolate_blocklist, {"Claudio Couto": "note"})
        assert sl.strip_speaker_label("Claudio Couto: hello world") == "hello world"

    def test_blocklist_is_case_insensitive(self, isolate_blocklist):
        _write(isolate_blocklist, {"Claudio Couto": "note"})
        assert sl.strip_speaker_label("claudio couto: hi") == "hi"

    def test_comment_key_is_ignored(self, isolate_blocklist):
        _write(isolate_blocklist, {"_comment": "docs", "Foo Bar": "x"})
        assert sl.strip_speaker_label("Foo Bar: text") == "text"
        # The comment text itself is never used as a name.
        assert sl.strip_speaker_label("docs: stay") == "docs: stay"

    def test_non_blocklisted_name_kept(self, isolate_blocklist):
        _write(isolate_blocklist, {"Claudio Couto": "note"})
        assert sl.strip_speaker_label("Someone Else: keep me") == "Someone Else: keep me"

    def test_missing_file_only_titled_rule_applies(self):
        # No blocklist file → titled rule still works, bare name kept.
        assert sl.strip_speaker_label("Speaker 3: hi") == "hi"
        assert sl.strip_speaker_label("Claudio Couto: hi") == "Claudio Couto: hi"

    def test_invalid_blocklist_fails_open(self, isolate_blocklist):
        isolate_blocklist.write_text("not json", encoding="utf-8")
        # Titled rule still applies; bare name kept (blocklist ignored).
        assert sl.strip_speaker_label("Dr. X: ok") == "ok"
        assert sl.strip_speaker_label("Claudio Couto: hi") == "Claudio Couto: hi"

    def test_non_dict_root_ignored(self, isolate_blocklist):
        _write(isolate_blocklist, ["Claudio Couto"])
        assert sl.strip_speaker_label("Claudio Couto: hi") == "Claudio Couto: hi"


class TestSeparatorVariants:
    """whisper glues the phantom name on with whatever separator it invents —
    not just a colon. These are the real recorded leading hallucinations from
    60 days of dictation (issue #67); each must strip to the underlying text."""

    # The exact names shipped in config/speaker_blocklist.json.
    _BLOCKLIST = {
        "Claudio Pagliauvao": "dash",
        "Claudio Coulson": "comma",
        "Claude Coulson": "comma",
        "Claudio Couto": "comma/titled",
        "Claudius C": "period",
        "Claudius S": "colon",
        "Claudius": "comma",
    }

    @pytest.fixture(autouse=True)
    def _seed(self, isolate_blocklist):
        _write(isolate_blocklist, self._BLOCKLIST)
        yield

    def test_period_separator(self):
        assert (
            sl.strip_speaker_label("Claudius C. Okay. Now, I want you to elaborate")
            == "Okay. Now, I want you to elaborate"
        )

    def test_colon_after_initial(self):
        assert (
            sl.strip_speaker_label("Claudius S.: No, I think this is not correct")
            == "No, I think this is not correct"
        )

    def test_dash_separator(self):
        assert (
            sl.strip_speaker_label("Claudio Pagliauvao- I just tried the insights feature")
            == "I just tried the insights feature"
        )

    def test_comma_separator_full_name(self):
        assert (
            sl.strip_speaker_label("Claudio Coulson, Oh yes, you are absolutely right")
            == "Oh yes, you are absolutely right"
        )
        assert (
            sl.strip_speaker_label("Claude Coulson, Yes, Generative Orchestration is on")
            == "Yes, Generative Orchestration is on"
        )

    def test_comma_separator_bare_name(self):
        assert (
            sl.strip_speaker_label("Claudius, No, I don't like it yet")
            == "No, I don't like it yet"
        )

    def test_longer_name_wins_over_bare(self):
        # "Claudius C" / "Claudius S" must beat bare "Claudius": a partial
        # strip would wrongly leave "C. ..." / "S.: ..." behind.
        assert sl.strip_speaker_label("Claudius C. Okay") == "Okay"
        assert sl.strip_speaker_label("Claudius S.: Okay") == "Okay"

    def test_titled_form_still_auto_stripped(self):
        # "Claudio Couto, Ph.D.:" is handled by the titled rule before the
        # blocklist ever runs — no double-strip, clean result.
        assert (
            sl.strip_speaker_label("Claudio Couto, Ph.D.: Okay, let me understand")
            == "Okay, let me understand"
        )

    def test_genuine_comma_opener_untouched(self):
        # Real dictation that opens with a non-blocklisted word + comma must
        # survive — these all occur verbatim in the corpus.
        for text in (
            "Ok, let me think about this",
            "Hello, this is a test",
            "Exactly, that is the point",
            "Yes, I like it very much",
        ):
            assert sl.strip_speaker_label(text) == text

    def test_label_only_take_not_emptied(self):
        # Separator present but nothing real after it → leave untouched.
        assert sl.strip_speaker_label("Claudius, ") == "Claudius, "
