"""FastAPI tests for `GET /api/activity` — persistent activity log API."""

from __future__ import annotations


class TestGetActivity:
    def test_empty_log_returns_empty_list(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.get("/api/activity")
        assert resp.status_code == 200
        body = resp.json()
        assert body["events"] == []
        assert body["count"] == 0

    def test_session_creation_is_logged(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.post("/api/sessions", json={"source": "test-source"})
        assert resp.status_code == 200

        activity = client.get("/api/activity").json()
        assert activity["count"] == 1
        event = activity["events"][0]
        assert event["event_type"] == "session_created"
        assert event["source"] == "test-source"
        assert event["session_id"] == resp.json()["session_id"]

    def test_incognito_session_creation_is_not_logged(self, webapp_client):
        client, _, _ = webapp_client
        resp = client.post("/api/sessions", json={"incognito": True})
        assert resp.status_code == 200

        activity = client.get("/api/activity").json()
        assert activity["count"] == 0

    def test_filters_by_event_type(self, webapp_client):
        client, _, _ = webapp_client
        client.post("/api/sessions", json={})
        client.post("/api/save-text", json={"text": "hello world"})

        activity = client.get("/api/activity", params={"event_type": "session_created"})
        body = activity.json()
        assert body["count"] == 2
        assert all(e["event_type"] == "session_created" for e in body["events"])

    def test_limit_is_clamped(self, webapp_client):
        client, _, _ = webapp_client
        for _ in range(3):
            client.post("/api/sessions", json={})
        resp = client.get("/api/activity", params={"limit": 1})
        assert resp.json()["count"] == 1
