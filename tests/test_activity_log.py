"""Unit tests for `src/activity_log.py` — persistent SQLite events log."""

from __future__ import annotations

# Standard library imports
import time
from pathlib import Path

# Third-party imports
import pytest

# Local imports
from src.activity_log import (
    count_events,
    init_db,
    prune_older_than,
    read_events,
    record_event,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "activity.sqlite3"


class TestInitAndRecord:
    def test_init_db_is_idempotent(self, db_path: Path):
        init_db(db_path)
        init_db(db_path)  # second call must not raise
        assert db_path.exists()

    def test_record_event_creates_db_lazily(self, db_path: Path):
        # record_event should work even without an explicit init_db() call.
        record_event("transcribed", path=db_path, word_count=5, duration_seconds=2.0)
        events = read_events(path=db_path)
        assert len(events) == 1
        assert events[0]["event_type"] == "transcribed"
        assert events[0]["word_count"] == 5
        assert events[0]["duration_seconds"] == 2.0
        assert events[0]["outcome"] == "ok"

    def test_record_event_payload_roundtrips(self, db_path: Path):
        record_event(
            "polished", path=db_path, session_id="abc123",
            payload={"model": "gemini_flash"},
        )
        events = read_events(path=db_path)
        assert events[0]["session_id"] == "abc123"
        assert events[0]["payload"] == {"model": "gemini_flash"}

    def test_record_event_error_outcome(self, db_path: Path):
        record_event(
            "transcribe_failed", path=db_path, outcome="error", error="boom",
        )
        events = read_events(path=db_path)
        assert events[0]["outcome"] == "error"
        assert events[0]["error"] == "boom"

    def test_record_event_never_raises_on_bad_path(self, tmp_path: Path):
        # A parent path that can't be created as a directory (it's a file)
        # should be swallowed, not raised — an activity log must not break
        # the action it is recording.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        bad_path = blocker / "activity.sqlite3"
        record_event("transcribed", path=bad_path)  # must not raise


class TestReadEvents:
    def test_newest_first(self, db_path: Path):
        record_event("session_created", path=db_path, ts=100)
        record_event("session_created", path=db_path, ts=300)
        record_event("session_created", path=db_path, ts=200)
        events = read_events(path=db_path)
        assert [e["ts"] for e in events] == [300, 200, 100]

    def test_filters_by_event_type(self, db_path: Path):
        record_event("session_created", path=db_path, ts=1)
        record_event("transcribed", path=db_path, ts=2)
        events = read_events(event_type="transcribed", path=db_path)
        assert len(events) == 1
        assert events[0]["event_type"] == "transcribed"

    def test_filters_by_since_and_until(self, db_path: Path):
        for ts in (100, 200, 300, 400):
            record_event("transcribed", path=db_path, ts=ts)
        events = read_events(since=150, until=350, path=db_path)
        assert sorted(e["ts"] for e in events) == [200, 300]

    def test_limit_clamped_to_at_least_one(self, db_path: Path):
        record_event("transcribed", path=db_path)
        events = read_events(limit=0, path=db_path)
        assert len(events) == 1


class TestCountEvents:
    def test_counts_without_fetching_rows(self, db_path: Path):
        record_event("session_created", path=db_path, ts=1)
        record_event("session_created", path=db_path, ts=2)
        record_event("transcribed", path=db_path, ts=3)
        assert count_events(event_type="session_created", path=db_path) == 2
        assert count_events(path=db_path) == 3

    def test_counts_with_since_filter(self, db_path: Path):
        record_event("session_created", path=db_path, ts=100)
        record_event("session_created", path=db_path, ts=200)
        assert count_events(since=150, path=db_path) == 1


class TestPruneOlderThan:
    def test_removes_old_events_only(self, db_path: Path):
        now = int(time.time())
        record_event("transcribed", path=db_path, ts=now - 400 * 86400)
        record_event("transcribed", path=db_path, ts=now - 10 * 86400)
        removed = prune_older_than(365, path=db_path)
        assert removed == 1
        remaining = read_events(path=db_path)
        assert len(remaining) == 1
        assert remaining[0]["ts"] == now - 10 * 86400

    def test_returns_zero_when_nothing_to_prune(self, db_path: Path):
        record_event("transcribed", path=db_path)
        assert prune_older_than(365, path=db_path) == 0
