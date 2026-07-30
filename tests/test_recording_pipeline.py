"""Unit tests for `src/recording_pipeline.finalize_transcript`.

Extracted from `app/gui/tray.py` and `app/gui/app.py` (voice-transcriber#160)
so the strip -> append-merge -> clipboard-copy tail lives in exactly one
place. Both GUI surfaces are otherwise untested (tk), so this module is the
only regression net for that tail's ordering.
"""

from __future__ import annotations

import pytest

from src import recording_pipeline
from src.recording_pipeline import finalize_transcript


@pytest.fixture(autouse=True)
def _stub_clipboard(monkeypatch):
    """Never touch the real OS clipboard from a test run."""
    calls = []
    monkeypatch.setattr(
        recording_pipeline.pyperclip, "copy", lambda text: calls.append(text)
    )
    return calls


class TestFinalizeTranscript:
    def test_empty_text_returns_none(self, _stub_clipboard):
        result = finalize_transcript(
            "   ", last_transcription=None, append_mode=False, auto_copy=True,
        )
        assert result is None
        assert _stub_clipboard == []

    def test_strips_whitespace(self, _stub_clipboard):
        result = finalize_transcript(
            "  hello world  ",
            last_transcription=None,
            append_mode=False,
            auto_copy=False,
        )
        assert result == "hello world"

    def test_append_mode_merges_with_blank_line(self, _stub_clipboard):
        result = finalize_transcript(
            "second take",
            last_transcription="first take",
            append_mode=True,
            auto_copy=False,
        )
        assert result == "first take\n\nsecond take"

    def test_append_mode_with_no_prior_text_is_a_noop(self, _stub_clipboard):
        result = finalize_transcript(
            "only take", last_transcription=None, append_mode=True, auto_copy=False,
        )
        assert result == "only take"

    def test_append_mode_off_ignores_last_transcription(self, _stub_clipboard):
        result = finalize_transcript(
            "fresh take",
            last_transcription="stale prior take",
            append_mode=False,
            auto_copy=False,
        )
        assert result == "fresh take"

    def test_auto_copy_true_copies_final_text(self, _stub_clipboard):
        result = finalize_transcript(
            "copy me", last_transcription=None, append_mode=False, auto_copy=True,
        )
        assert result == "copy me"
        assert _stub_clipboard == ["copy me"]

    def test_auto_copy_false_does_not_touch_clipboard(self, _stub_clipboard):
        finalize_transcript(
            "don't copy", last_transcription=None, append_mode=False, auto_copy=False,
        )
        assert _stub_clipboard == []

    def test_empty_text_never_touches_clipboard_even_with_auto_copy(
        self, _stub_clipboard,
    ):
        """Regression pin: the tray used to skip the clipboard entirely on
        an empty transcription; the tk window used to still copy `""`,
        clobbering it. finalize_transcript now owns the single behaviour —
        never touch the clipboard on empty input."""
        result = finalize_transcript(
            "", last_transcription=None, append_mode=False, auto_copy=True,
        )
        assert result is None
        assert _stub_clipboard == []

    def test_clipboard_failure_does_not_raise(self, monkeypatch):
        def _boom(_text):
            raise RuntimeError("no clipboard access")

        monkeypatch.setattr(recording_pipeline.pyperclip, "copy", _boom)
        result = finalize_transcript(
            "still returned", last_transcription=None, append_mode=False, auto_copy=True,
        )
        assert result == "still returned"
