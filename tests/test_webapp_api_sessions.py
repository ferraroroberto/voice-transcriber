"""FastAPI tests for the session endpoints: create / list / get-text /
delete / older-than. Polish on existing sessions covered in this file too."""

from __future__ import annotations

# Standard library imports
from datetime import datetime
from unittest.mock import MagicMock

# Local imports
from src.polish import PolishError, PolishResult


class TestCreateSession:
    def test_creates_with_defaults(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"]
        assert body["incognito"] is False

    def test_incognito_flag_propagates(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.post("/api/sessions", json={"incognito": True})
        assert resp.json()["incognito"] is True


class TestListSessions:
    def test_returns_empty_when_no_sessions(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sessions"] == []
        assert body["total"] == 0

    def test_returns_newest_first(self, webapp_client):
        client, app, _ = webapp_client
        archive = app.state.archive
        a = archive.new_session(now=datetime(2026, 1, 1))
        b = archive.new_session(now=datetime(2026, 5, 12))
        a.write_meta(); b.write_meta()
        resp = client.get("/api/sessions")
        ids = [s["session_id"] for s in resp.json()["sessions"]]
        assert ids == [b.session_id, a.session_id]

    def test_pagination(self, webapp_client):
        client, app, _ = webapp_client
        archive = app.state.archive
        for i in range(5):
            s = archive.new_session(now=datetime(2026, 1, i + 1))
            s.write_meta()
        resp = client.get("/api/sessions?limit=2&offset=1")
        body = resp.json()
        assert len(body["sessions"]) == 2
        assert body["total"] == 5

    def test_excludes_incognito(self, webapp_client):
        client, app, _ = webapp_client
        archive = app.state.archive
        archive.new_session(now=datetime(2026, 1, 1)).write_meta()
        archive.new_session(now=datetime(2026, 1, 2), incognito=True).write_meta()
        body = client.get("/api/sessions").json()
        assert body["total"] == 1

    def test_includes_transcript_preview(self, webapp_client):
        client, app, _ = webapp_client
        s = app.state.archive.new_session()
        s.write_transcript("hello world")
        s.write_meta()
        body = client.get("/api/sessions").json()
        assert body["sessions"][0]["transcript_preview"] == "hello world"


class TestListSessionsWindow:
    def test_days_window_filters_old(self, webapp_client):
        from datetime import timedelta
        client, app, _ = webapp_client
        archive = app.state.archive
        archive.new_session(now=datetime.now() - timedelta(days=30)).write_meta()
        archive.new_session(now=datetime.now() - timedelta(hours=1)).write_meta()
        body = client.get("/api/sessions?days=7").json()
        assert body["total"] == 2  # total ignores the window
        assert len(body["sessions"]) == 1  # only the recent one in the window

    def test_days_zero_is_400(self, webapp_client):
        client, _, _ = webapp_client
        assert client.get("/api/sessions?days=0").status_code == 400

    def test_unparseable_since_is_400(self, webapp_client):
        client, _, _ = webapp_client
        assert client.get("/api/sessions?since=not-a-date").status_code == 400


class TestBulkTranscripts:
    def test_returns_full_transcripts_in_window(self, webapp_client):
        from datetime import timedelta
        client, app, _ = webapp_client
        archive = app.state.archive
        recent = archive.new_session(now=datetime.now() - timedelta(hours=1))
        recent.write_transcript("x" * 300)
        recent.write_meta()
        old = archive.new_session(now=datetime.now() - timedelta(days=30))
        old.write_transcript("ancient")
        old.write_meta()
        body = client.get("/api/sessions/transcripts?days=7").json()
        assert body["count"] == 1
        assert len(body["transcripts"][0]["transcript"]) == 300
        assert body["transcripts"][0]["session_id"] == recent.session_id

    def test_omits_empty_transcripts(self, webapp_client):
        client, app, _ = webapp_client
        s = app.state.archive.new_session()
        s.write_meta()  # no transcript written
        body = client.get("/api/sessions/transcripts").json()
        assert body["count"] == 0

    def test_excludes_incognito(self, webapp_client):
        client, app, _ = webapp_client
        s = app.state.archive.new_session(incognito=True)
        s.write_transcript("secret")
        s.write_meta()
        body = client.get("/api/sessions/transcripts").json()
        assert body["count"] == 0


class TestGetSessionText:
    def test_returns_full_text(self, webapp_client):
        client, app, _ = webapp_client
        s = app.state.archive.new_session()
        s.write_transcript("a" * 300)
        s.write_meta()
        resp = client.get(f"/api/sessions/{s.session_id}/text")
        body = resp.json()
        assert len(body["transcript"]) == 300
        assert body["polished"] == ""

    def test_404_for_unknown_session(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/api/sessions/does-not-exist/text")
        assert resp.status_code == 404


class TestDeleteSessions:
    def test_delete_one(self, webapp_client):
        client, app, _ = webapp_client
        s = app.state.archive.new_session()
        s.write_meta()
        resp = client.delete(f"/api/sessions/{s.session_id}")
        assert resp.status_code == 200
        assert resp.json()["removed"] == s.session_id
        # Subsequent get returns 404.
        assert client.get(f"/api/sessions/{s.session_id}/text").status_code == 404

    def test_delete_unknown_returns_404(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.delete("/api/sessions/does-not-exist")
        assert resp.status_code == 404

    def test_delete_all(self, webapp_client):
        client, app, _ = webapp_client
        for _ in range(3):
            app.state.archive.new_session().write_meta()
        resp = client.delete("/api/sessions")
        assert resp.status_code == 200
        assert resp.json()["removed"] == 3

    def test_delete_older_than_validates_days(self, webapp_client):
        client, _, _ = webapp_client
        assert client.delete("/api/sessions/older-than/0").status_code == 400
        # Valid value returns 0 deletions when there's nothing to prune.
        ok = client.delete("/api/sessions/older-than/30")
        assert ok.status_code == 200
        assert ok.json()["removed"] == 0


class TestPolishSession:
    def test_happy_path(self, webapp_client):
        client, app, overrides = webapp_client
        s = app.state.archive.new_session()
        s.write_transcript("uh, dirty input")
        s.write_meta()
        overrides["polish"].polish.return_value = PolishResult(
            polished_text="dirty input",
            model="gemini_flash",
            request_payload={},
            response_payload={"id": "msg_x"},
        )
        resp = client.post(
            f"/api/sessions/{s.session_id}/polish",
            json={"model": "gemini_flash"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["polished"] == "dirty input"

    def test_404_when_session_missing(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.post(
            "/api/sessions/does-not-exist/polish",
            json={"model": "gemini_flash"},
        )
        assert resp.status_code == 404

    def test_400_when_no_transcript_yet(self, webapp_client):
        client, app, _ = webapp_client
        s = app.state.archive.new_session()
        s.write_meta()
        resp = client.post(
            f"/api/sessions/{s.session_id}/polish",
            json={"model": "gemini_flash"},
        )
        assert resp.status_code == 400
        assert "no transcript" in resp.json()["detail"]

    def test_edited_transcript_overwrites_archive(self, webapp_client):
        client, app, overrides = webapp_client
        s = app.state.archive.new_session()
        s.write_transcript("original")
        s.write_meta()
        overrides["polish"].polish.return_value = PolishResult(
            polished_text="ok", model="gemini_flash",
            request_payload={}, response_payload={},
        )
        client.post(
            f"/api/sessions/{s.session_id}/polish",
            json={"model": "gemini_flash", "transcript": "edited"},
        )
        # Confirm the on-disk transcript was overwritten.
        same = app.state.archive.get(s.session_id)
        assert same.read_transcript() == "edited"

    def test_polish_failure_returns_424(self, webapp_client):
        client, app, overrides = webapp_client
        s = app.state.archive.new_session()
        s.write_transcript("body")
        s.write_meta()
        overrides["polish"].polish.side_effect = PolishError("upstream down")
        resp = client.post(
            f"/api/sessions/{s.session_id}/polish",
            json={"model": "gemini_flash"},
        )
        assert resp.status_code == 424
        # Failure metadata is persisted on the session.
        same = app.state.archive.get(s.session_id)
        assert same.meta.polish_succeeded is False
        assert same.meta.error == "upstream down"
