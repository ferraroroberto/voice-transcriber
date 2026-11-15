"""POST /api/sessions/{id}/task-os: the setting gate and the take text sent."""

from __future__ import annotations

# Standard library imports
from typing import Any, Dict, List

# Third-party imports
import pytest

from src import task_os


class _Resp:
    def __init__(self, status: int, body: Dict[str, Any]) -> None:
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self) -> Dict[str, Any]:
        return self._body


@pytest.fixture
def posts(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """task-os stand-in: 201 for a new external_id, 200 for a replay."""
    seen: List[Dict[str, Any]] = []

    def fake(method: str, url: str, *, timeout: float, **kwargs: Any) -> _Resp:
        replay = any(p["json"]["external_id"] == kwargs["json"]["external_id"] for p in seen)
        seen.append({"url": url, **kwargs})
        return _Resp(200 if replay else 201, {"id": 42})

    monkeypatch.setattr(task_os, "pooled_request", fake)
    return seen


def _take(app, transcript: str, polished: str = "") -> str:
    s = app.state.archive.new_session()
    s.write_transcript(transcript)
    if polished:
        s.write_polished(polished, model="stub", request_payload={}, response_payload={})
    s.write_meta()
    return s.session_id


def _enable(client) -> None:
    resp = client.post("/api/config", json={"task_os_enabled": True})
    assert resp.status_code == 200
    assert resp.json()["config"]["task_os_enabled"] is True


def test_off_by_default_and_url_stays_server_side(webapp_client, posts):
    client, app, _ = webapp_client
    cfg = client.get("/api/config").json()
    assert cfg["task_os_enabled"] is False
    assert "task_os_url" not in cfg
    resp = client.post(f"/api/sessions/{_take(app, 'Buy milk')}/task-os")
    assert resp.status_code == 409
    assert posts == []


def test_sends_polished_text_once(webapp_client, posts):
    client, app, _ = webapp_client
    _enable(client)
    sid = _take(app, "uh buy milk", polished="Buy milk")
    first = client.post(f"/api/sessions/{sid}/task-os")
    assert first.status_code == 200
    assert first.json()["outcome"] == "created"
    assert posts[0]["json"]["title"] == "Buy milk"
    assert posts[0]["json"]["external_id"] == f"voice-transcriber:take:{sid}"
    assert posts[0]["url"] == "https://127.0.0.1:8448/api/tasks"
    again = client.post(f"/api/sessions/{sid}/task-os")
    assert again.json()["outcome"] == "existing"


def test_unknown_take_is_404(webapp_client, posts):
    client, _, _ = webapp_client
    _enable(client)
    assert client.post("/api/sessions/nope/task-os").status_code == 404


def test_unreachable_task_os_is_503_with_its_message(webapp_client, monkeypatch):
    import requests

    client, app, _ = webapp_client
    _enable(client)

    def down(*_a: Any, **_k: Any) -> None:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(task_os, "pooled_request", down)
    resp = client.post(f"/api/sessions/{_take(app, 'Buy milk')}/task-os")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "task-os is not reachable"
