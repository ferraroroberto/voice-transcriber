"""Unit tests for `src/recording_pipeline.finalize_transcript` and
`handle_take`.

Extracted from `app/gui/tray.py` and `app/gui/app.py` (voice-transcriber#160,
then the full take-processing workflow in #174) so the process-recording ->
error/silent arms -> strip -> append-merge -> clipboard-copy sequence lives
in exactly one place. Both GUI surfaces are otherwise untested (tk), so this
module is the only regression net for that sequence's ordering.
"""

from __future__ import annotations

import pytest

from src import recording_pipeline
from src.recording_pipeline import SilentTake, finalize_transcript, handle_take
from src.transcription_client import TranscriptionError


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


class TestHandleTake:
    """`handle_take` is the single implementation of the tray's and the tk
    window's take-processing workflow (voice-transcriber#174) — these pin
    the four possible outcomes so the two GUI surfaces can trust
    `TakeResult` instead of each re-deriving them from `process_recording`.

    `process_recording` itself (silence gate -> gain boost -> transcribe)
    is stubbed out here; only the arms `handle_take` adds around it —
    TranscriptionError, SilentTake, and finalize -> empty/text — are under
    test.
    """

    def _stub_process_recording(self, monkeypatch, *, returns=None, raises=None, capture=None):
        def _fake(recording, config, webapp_cfg, client, *, translate=False):
            if capture is not None:
                capture["translate"] = translate
            if raises is not None:
                raise raises
            return returns

        monkeypatch.setattr(recording_pipeline, "process_recording", _fake)

    def test_success_returns_finalized_text(self, monkeypatch, _stub_clipboard):
        self._stub_process_recording(monkeypatch, returns="  hello  ")
        result = handle_take(
            object(), object(), None, object(),
            last_transcription=None, append_mode=False, auto_copy=False,
        )
        assert result.text == "hello"
        assert result.error is None
        assert result.silent is None

    def test_silent_take_short_circuits_before_finalize(self, monkeypatch, _stub_clipboard):
        silent = SilentTake(dbfs=-60.0, threshold=-40.0)
        self._stub_process_recording(monkeypatch, returns=silent)
        result = handle_take(
            object(), object(), None, object(),
            last_transcription=None, append_mode=False, auto_copy=True,
        )
        assert result.silent is silent
        assert result.text is None
        assert result.error is None
        assert _stub_clipboard == []  # finalize (and its clipboard copy) never ran

    def test_transcription_error_becomes_error_result(self, monkeypatch, _stub_clipboard):
        self._stub_process_recording(monkeypatch, raises=TranscriptionError("server down"))
        result = handle_take(
            object(), object(), None, object(),
            last_transcription=None, append_mode=False, auto_copy=False,
        )
        assert result.error == "server down"
        assert result.text is None
        assert result.silent is None

    def test_empty_transcription_after_finalize_is_a_distinct_state(self, monkeypatch, _stub_clipboard):
        """Whitespace-only whisper output finalizes to None -- distinct from
        both the error and silent-gate arms, so callers know to show their
        own "empty transcription" notice rather than a blank result."""
        self._stub_process_recording(monkeypatch, returns="   ")
        result = handle_take(
            object(), object(), None, object(),
            last_transcription=None, append_mode=False, auto_copy=True,
        )
        assert result.text is None
        assert result.error is None
        assert result.silent is None
        assert _stub_clipboard == []

    def test_forwards_translate_flag_to_process_recording(self, monkeypatch, _stub_clipboard):
        capture: dict = {}
        self._stub_process_recording(monkeypatch, returns="hi", capture=capture)
        handle_take(
            object(), object(), None, object(),
            last_transcription=None, append_mode=False, auto_copy=False,
            translate=True,
        )
        assert capture["translate"] is True
