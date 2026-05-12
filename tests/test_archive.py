"""Unit tests for `src/archive.py` — dated session archive."""

from __future__ import annotations

# Standard library imports
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

# Third-party imports
import pytest

# Local imports
from src.archive import (
    META_FILENAME,
    POLISHED_FILENAME,
    POLISH_REQUEST_FILENAME,
    POLISH_RESPONSE_FILENAME,
    RAW_AUDIO_FILENAME,
    Session,
    SessionArchive,
    SessionMeta,
    TRANSCRIPT_FILENAME,
)


@pytest.fixture
def archive(tmp_path: Path) -> SessionArchive:
    return SessionArchive(root=tmp_path / "archive")


# ---------------------------------------------------------------------------
# new_session — folder layout + meta initialisation
# ---------------------------------------------------------------------------

class TestNewSession:
    def test_creates_dated_folder_structure(self, archive: SessionArchive):
        now = datetime(2026, 5, 12, 10, 30, 0)
        s = archive.new_session(language="en", sample_rate=16000, now=now)
        # Folder must be archive/2026/05/12/10-30-00-<hex>
        assert s.folder.parent.parent.parent.parent == archive.root
        assert s.folder.parent.parent.parent.name == "2026"
        assert s.folder.parent.parent.name == "05"
        assert s.folder.parent.name == "12"
        assert s.folder.name.startswith("10-30-00-")

    def test_meta_initialised(self, archive: SessionArchive):
        s = archive.new_session(language="es", sample_rate=22050)
        assert s.meta.language == "es"
        assert s.meta.sample_rate == 22050
        assert s.meta.incognito is False
        assert s.meta.session_id == s.session_id

    def test_meta_file_written_on_create(self, archive: SessionArchive):
        s = archive.new_session()
        assert (s.folder / META_FILENAME).exists()

    def test_incognito_flag_propagates(self, archive: SessionArchive):
        s = archive.new_session(incognito=True)
        assert s.meta.incognito is True


# ---------------------------------------------------------------------------
# Session writers — append, write_transcript, write_polished, mark_failed
# ---------------------------------------------------------------------------

class TestSessionWriters:
    def test_append_raw_chunk_grows_file_and_updates_meta(self, archive):
        s = archive.new_session()
        size1 = s.append_raw_chunk(b"abc")
        size2 = s.append_raw_chunk(b"defgh")
        assert size1 == 3
        assert size2 == 8
        assert s.meta.raw_bytes == 8
        assert (s.folder / RAW_AUDIO_FILENAME).read_bytes() == b"abcdefgh"

    def test_write_wav(self, archive):
        s = archive.new_session()
        path = s.write_wav(b"WAV_DATA")
        assert path.read_bytes() == b"WAV_DATA"

    def test_write_transcript_updates_char_count(self, archive):
        s = archive.new_session()
        s.write_transcript("hello world")
        assert s.meta.transcript_chars == 11
        assert (s.folder / TRANSCRIPT_FILENAME).read_text(encoding="utf-8") == "hello world"

    def test_write_polished_persists_request_and_response(self, archive):
        s = archive.new_session()
        s.write_polished(
            "clean text",
            model="gemini_flash",
            request_payload={"model": "gemini_flash"},
            response_payload={"id": "msg_x"},
            prompt_id="filler-words",
        )
        assert (s.folder / POLISHED_FILENAME).read_text(encoding="utf-8") == "clean text"
        req = json.loads((s.folder / POLISH_REQUEST_FILENAME).read_text(encoding="utf-8"))
        resp = json.loads((s.folder / POLISH_RESPONSE_FILENAME).read_text(encoding="utf-8"))
        assert req["model"] == "gemini_flash"
        assert resp["id"] == "msg_x"
        assert s.meta.polish_model == "gemini_flash"
        assert s.meta.polish_prompt_id == "filler-words"
        assert s.meta.polish_succeeded is True

    def test_mark_polish_failed_sets_error_metadata(self, archive):
        s = archive.new_session()
        s.mark_polish_failed("gemini_flash", "hub unreachable", prompt_id="filler-words")
        assert s.meta.polish_succeeded is False
        assert s.meta.error == "hub unreachable"
        assert s.meta.polish_model == "gemini_flash"


# ---------------------------------------------------------------------------
# Session readers
# ---------------------------------------------------------------------------

class TestSessionReaders:
    def test_read_transcript_returns_text(self, archive):
        s = archive.new_session()
        s.write_transcript("the body")
        assert s.read_transcript() == "the body"

    def test_read_transcript_when_missing(self, archive):
        s = archive.new_session()
        assert s.read_transcript() is None

    def test_read_polished_when_missing(self, archive):
        s = archive.new_session()
        assert s.read_polished() is None


# ---------------------------------------------------------------------------
# list_sessions / get / count / delete
# ---------------------------------------------------------------------------

class TestSessionListing:
    def _make(self, archive: SessionArchive, ts: datetime, incognito: bool = False):
        s = archive.new_session(now=ts, incognito=incognito)
        s.write_meta()
        return s

    def test_list_newest_first(self, archive):
        a = self._make(archive, datetime(2026, 1, 1))
        b = self._make(archive, datetime(2026, 5, 12))
        c = self._make(archive, datetime(2026, 3, 1))
        out = archive.list_sessions()
        ids = [s.session_id for s in out]
        assert ids == [b.session_id, c.session_id, a.session_id]

    def test_list_excludes_incognito(self, archive):
        self._make(archive, datetime(2026, 1, 1))
        self._make(archive, datetime(2026, 1, 2), incognito=True)
        assert len(archive.list_sessions()) == 1

    def test_count_excludes_incognito(self, archive):
        self._make(archive, datetime(2026, 1, 1))
        self._make(archive, datetime(2026, 1, 2), incognito=True)
        assert archive.count_sessions() == 1

    def test_pagination(self, archive):
        for i in range(5):
            self._make(archive, datetime(2026, 1, i + 1))
        page = archive.list_sessions(limit=2, offset=1)
        assert len(page) == 2

    def test_get_by_id(self, archive):
        s = self._make(archive, datetime(2026, 1, 1))
        found = archive.get(s.session_id)
        assert found is not None
        assert found.session_id == s.session_id

    def test_get_unknown_returns_none(self, archive):
        assert archive.get("does-not-exist") is None

    def test_delete_session_removes_folder(self, archive):
        s = self._make(archive, datetime(2026, 1, 1))
        assert archive.delete_session(s.session_id) is True
        assert not s.folder.exists()

    def test_delete_unknown_session_returns_false(self, archive):
        assert archive.delete_session("nope") is False


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_cleanup_older_than_keeps_recent(self, archive, tmp_path):
        recent = archive.new_session(now=datetime.now())
        old = archive.new_session(now=datetime.now() - timedelta(days=10))
        # Backdate the old folder's mtime so the cleanup picks it up.
        old_time = time.time() - 100 * 86400
        import os
        os.utime(old.folder, (old_time, old_time))
        removed = archive.cleanup_older_than(days=30)
        assert removed == 1
        assert recent.folder.exists()
        assert not old.folder.exists()

    def test_cleanup_all_wipes_everything(self, archive):
        archive.new_session()
        archive.new_session(now=datetime(2026, 5, 12))
        n = archive.cleanup_all()
        assert n == 2
        assert archive.list_sessions() == []


# ---------------------------------------------------------------------------
# _hydrate — recovers gracefully from stale/missing meta
# ---------------------------------------------------------------------------

class TestHydrate:
    def test_hydrate_missing_meta_falls_back_to_folder_name(self, archive):
        s = archive.new_session()
        (s.folder / META_FILENAME).unlink()
        again = archive.get(s.session_id)
        assert again is not None
        assert again.meta.session_id == s.session_id

    def test_hydrate_corrupt_meta_falls_back(self, archive):
        s = archive.new_session()
        (s.folder / META_FILENAME).write_text("not json", encoding="utf-8")
        again = archive.get(s.session_id)
        assert again is not None
        assert again.meta.session_id == s.session_id

    def test_hydrate_preserves_polish_model_string(self, archive):
        """Old archived sessions with the previous `agentic_light` model
        name should still load — the field is a free string with no
        enum constraint."""
        s = archive.new_session()
        meta = json.loads((s.folder / META_FILENAME).read_text(encoding="utf-8"))
        meta["polish_model"] = "agentic_light"  # legacy value
        (s.folder / META_FILENAME).write_text(
            json.dumps(meta), encoding="utf-8"
        )
        again = archive.get(s.session_id)
        assert again is not None
        assert again.meta.polish_model == "agentic_light"
