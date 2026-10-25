"""FastAPI tests for `GET /api/analytics/summary` — usage analytics API."""

from __future__ import annotations


class TestGetAnalyticsSummary:
    def test_empty_shape(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/api/analytics/summary")
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "date", "take_count", "total_words", "total_duration_seconds",
            "words_per_minute", "time_saved_minutes",
        ):
            assert key in body
        assert body["take_count"] == 0
        assert body["words_per_minute"] is None

    def test_reflects_a_new_session(self, webapp_client):
        client, _, _ = webapp_client
        client.post("/api/sessions", json={})
        # The words/min and time-saved computation from a real transcribed
        # take is covered directly against the persistent log in
        # test_analytics.py; this just confirms the route is wired to
        # today's take_count.
        summary = client.get("/api/analytics/summary").json()
        assert summary["take_count"] == 1
