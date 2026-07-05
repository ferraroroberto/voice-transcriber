"""Usage analytics — today's take count, words/min, estimated time saved.

Derived entirely from the persistent activity log (:mod:`src.activity_log`),
not the archive — so the numbers stay correct even after a session's
``archive/`` folder has been pruned by the 30-day retention sweep. See
issue #95.

``words_per_minute`` and ``time_saved_minutes`` are estimates, not precise
measurements: word count is a whitespace split of the transcript, and
"time saved" assumes a fixed baseline typing speed (see ``TYPING_WPM_BASELINE``)
against which dictation is compared. Both are ``None`` when there is no
``transcribed`` event with a recorded duration yet today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Optional

from src import activity_log

# A generic average adult typing speed, used only to estimate how long the
# same words would have taken to type by hand. Not user-specific.
TYPING_WPM_BASELINE = 40


@dataclass
class UsageSummary:
    date: str  # ISO YYYY-MM-DD, local calendar day this summary covers
    take_count: int
    total_words: int
    total_duration_seconds: float
    words_per_minute: Optional[float]
    time_saved_minutes: Optional[float]


def _today_start_epoch() -> int:
    today = datetime.now().date()
    return int(datetime.combine(today, dtime.min).timestamp())


def compute_usage_summary() -> UsageSummary:
    """Aggregate today's activity-log events into a :class:`UsageSummary`."""
    since = _today_start_epoch()
    today = datetime.fromtimestamp(since).date().isoformat()

    take_count = activity_log.count_events(event_type="session_created", since=since)

    total_words = 0
    total_duration = 0.0
    for evt in activity_log.read_events(event_type="transcribed", since=since, limit=10_000):
        duration = evt.get("duration_seconds")
        words = evt.get("word_count")
        if not duration or duration <= 0 or not words:
            continue
        total_words += int(words)
        total_duration += float(duration)

    words_per_minute: Optional[float] = None
    time_saved_minutes: Optional[float] = None
    if total_duration > 0:
        words_per_minute = round(total_words / (total_duration / 60), 1)
        typing_minutes = total_words / TYPING_WPM_BASELINE
        actual_minutes = total_duration / 60
        time_saved_minutes = round(max(0.0, typing_minutes - actual_minutes), 1)

    return UsageSummary(
        date=today,
        take_count=take_count,
        total_words=total_words,
        total_duration_seconds=round(total_duration, 1),
        words_per_minute=words_per_minute,
        time_saved_minutes=time_saved_minutes,
    )
