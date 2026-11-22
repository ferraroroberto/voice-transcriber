"""The build readout's restart route is mounted behind the bearer gate.

`src/self_restart.py` is vendored verbatim and tested in project-scaffolding; this covers the app-side
wiring: remote callers need the token, a cross-origin POST is refused, and no test posts a same-origin
restart through the real app, because that would run the real `tray.bat --restart`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.webapp.middleware import require_authenticated
from src.self_restart import TRAY_RESTART, RestartTarget, restart_router

TOKEN = "t" * 32
ORIGIN = {"Origin": "http://testserver"}
ROOT = Path(__file__).resolve().parents[2]


def _remote(app: FastAPI) -> TestClient:
    async def fake_remote(scope, receive, send):
        if scope.get("type") == "http":
            scope["client"] = ("10.0.0.42", 12345)
        await app(scope, receive, send)

    return TestClient(fake_remote)


def test_the_app_declares_the_tray_recipe_as_its_only_target(webapp_client) -> None:
    client, _, _ = webapp_client
    res = client.get("/api/restart")
    assert res.status_code == 200
    assert [t["id"] for t in res.json()["targets"]] == ["app"]


def test_a_remote_caller_without_the_token_cannot_read_or_start_a_restart(webapp_client) -> None:
    _, app, _ = webapp_client
    app.state.webapp_config.auth_token = TOKEN
    remote = _remote(app)
    assert remote.get("/api/restart").status_code == 401
    assert remote.post("/api/restart", json={"target": "app"}, headers=ORIGIN).status_code == 401


def test_a_cross_origin_post_is_refused_even_with_the_token(webapp_client) -> None:
    _, app, _ = webapp_client
    app.state.webapp_config.auth_token = TOKEN
    res = _remote(app).post(
        "/api/restart", json={"target": "app"},
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": "https://elsewhere.example"},
    )
    assert res.status_code == 403


def test_the_route_refuses_when_the_middleware_is_missing(tmp_path: Path) -> None:
    bare = FastAPI()
    bare.include_router(restart_router(
        targets=[RestartTarget("app", "Restart app", TRAY_RESTART)],
        status_dir=tmp_path / "restart", guard=require_authenticated, root=tmp_path,
        spawn=lambda spec, root: pytest.fail("a restart started without auth"),
    ))
    assert TestClient(bare).post("/api/restart", json={"target": "app"}, headers=ORIGIN).status_code == 401


def test_the_recipe_runs_the_repos_own_tray_bat() -> None:
    assert TRAY_RESTART[-2:] == (".\\tray.bat", "--restart")
    assert (ROOT / "tray.bat").is_file()
