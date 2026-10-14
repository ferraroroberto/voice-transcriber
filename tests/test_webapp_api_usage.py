"""The vendored usage log, mounted in the webapp (#277).

Events go to the app's own runtime-data SQLite file (isolated per test by
``VT_USAGE_DB_PATH`` in conftest), behind the same bearer-token gate as the
rest of ``/api``. The privacy filter itself is the vendored module's job;
these tests pin the wiring and one end-to-end drop.
"""

from __future__ import annotations

# Standard library imports
import sqlite3
from pathlib import Path

# Third-party imports
import pytest
from fastapi.testclient import TestClient


def _batch(**event) -> dict:
    base = {"kind": "tap", "id": "text.copy"}
    base.update(event)
    return {
        "session_id": "3f0e8c2a-7a52-4b6e-9d57-1c2f6f0a9b11",
        "device_class": "phone",
        "os_class": "ios",
        "events": [base],
    }


def _rows(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


@pytest.fixture
def non_loopback_client(webapp_client):
    _, app, _ = webapp_client

    async def fake_remote(scope, receive, send):
        if scope.get("type") == "http":
            scope["client"] = ("10.0.0.42", 12345)
        await app(scope, receive, send)

    return TestClient(fake_remote)


class TestUsageLog:
    def test_event_lands_in_the_isolated_database(self, webapp_client, tmp_path):
        client, _, _ = webapp_client
        resp = client.post("/api/usage/events", json=_batch())
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 1
        assert len(_rows(tmp_path / "usage.sqlite3")) == 1

    def test_free_text_id_is_dropped(self, webapp_client, tmp_path):
        """An id that is a sentence (element text, a transcript) never lands."""
        client, _, _ = webapp_client
        resp = client.post("/api/usage/events", json=_batch(id="Copy my last transcript"))
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 0

    def test_non_loopback_needs_the_token(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        assert non_loopback_client.post("/api/usage/events", json=_batch()).status_code == 401
        ok = non_loopback_client.post(
            "/api/usage/events",
            json=_batch(),
            headers={"Authorization": "Bearer secret-token"},
        )
        assert ok.status_code == 200
