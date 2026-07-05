"""Unit tests for `src/analytics.py` — today's usage summary.

`compute_usage_summary()` has no path override (it always reads the
default activity-log DB), so every test monkeypatches
`activity_log.DEFAULT_DB_PATH` into a temp file instead.
"""

from __future__ import annotations

# Standard library imports
from pathlib import Path

# Third-party imports
import pytest

# Local imports
from src import activity_log
from src.analytics import TYPING_WPM_BASELINE, compute_usage_summary


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(activity_log, "DEFAULT_DB_PATH", tmp_path / "activity.sqlite3")


def _today_ts(offset_seconds: int = 60) -> int:
    """A timestamp just after today's local midnight."""
    from src.analytics import _today_start_epoch

    return _today_start_epoch() + offset_seconds


def _yesterday_ts(offset_seconds: int = 60) -> int:
    """A timestamp just before today's local midnight (i.e. still yesterday)."""
    from src.analytics import _today_start_epoch

    return _today_start_epoch() - offset_seconds


class TestEmptyDay:
    def test_no_events_returns_zeros_and_none(self):
        summary = compute_usage_summary()
        assert summary.take_count == 0
        assert summary.total_words == 0
        assert summary.total_duration_seconds == 0.0
        assert summary.words_per_minute is None
        assert summary.time_saved_minutes is None


class TestTakeCount:
    def test_counts_only_todays_session_created_events(self):
        activity_log.record_event("session_created", ts=_today_ts())
        activity_log.record_event("session_created", ts=_today_ts(120))
        activity_log.record_event("session_created", ts=_yesterday_ts())
        summary = compute_usage_summary()
        assert summary.take_count == 2

    def test_ignores_other_event_types(self):
        activity_log.record_event("session_created", ts=_today_ts())
        activity_log.record_event("transcribed", ts=_today_ts(), word_count=10, duration_seconds=5)
        summary = compute_usage_summary()
        assert summary.take_count == 1


class TestWordsPerMinuteAndTimeSaved:
    def test_computes_from_transcribed_events_today(self):
        # 300 words in 120s (2 min) -> 150 wpm.
        activity_log.record_event(
            "transcribed", ts=_today_ts(), word_count=300, duration_seconds=120,
        )
        summary = compute_usage_summary()
        assert summary.total_words == 300
        assert summary.total_duration_seconds == 120.0
        assert summary.words_per_minute == 150.0
        # Typing 300 words at the baseline wpm would take 300/40 = 7.5 min;
        # actual was 2 min -> saved 5.5 min.
        expected_saved = round(300 / TYPING_WPM_BASELINE - 2.0, 1)
        assert summary.time_saved_minutes == expected_saved

    def test_aggregates_multiple_takes(self):
        activity_log.record_event(
            "transcribed", ts=_today_ts(), word_count=100, duration_seconds=60,
        )
        activity_log.record_event(
            "transcribed", ts=_today_ts(120), word_count=200, duration_seconds=60,
        )
        summary = compute_usage_summary()
        assert summary.total_words == 300
        assert summary.total_duration_seconds == 120.0

    def test_excludes_events_missing_duration(self):
        # A paste-only session_created (via polish-text/save-text) can back a
        # "polished"/"transcribed"-shaped record with no duration — must not
        # skew the wpm ratio.
        activity_log.record_event(
            "transcribed", ts=_today_ts(), word_count=50, duration_seconds=None,
        )
        summary = compute_usage_summary()
        assert summary.total_words == 0
        assert summary.words_per_minute is None

    def test_excludes_events_missing_word_count(self):
        activity_log.record_event(
            "transcribed", ts=_today_ts(), word_count=None, duration_seconds=30,
        )
        summary = compute_usage_summary()
        assert summary.total_duration_seconds == 0.0
        assert summary.words_per_minute is None

    def test_excludes_yesterdays_transcribed_events(self):
        activity_log.record_event(
            "transcribed", ts=_yesterday_ts(), word_count=1000, duration_seconds=60,
        )
        summary = compute_usage_summary()
        assert summary.total_words == 0
        assert summary.words_per_minute is None

    def test_time_saved_floors_at_zero(self):
        # Very slow dictation (far below typing speed) must not go negative.
        activity_log.record_event(
            "transcribed", ts=_today_ts(), word_count=10, duration_seconds=6000,
        )
        summary = compute_usage_summary()
        assert summary.time_saved_minutes == 0.0
