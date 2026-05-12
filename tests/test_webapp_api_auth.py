"""FastAPI tests for the BearerTokenMiddleware.

Loopback callers (the TestClient is one — 127.0.0.1) always bypass, so
to exercise the gate we override `request.client.host` via the ASGI
``scope``-rewriting middleware below.
"""

from __future__ import annotations

# Third-party imports
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def non_loopback_client(webapp_client):
    """Wrap the app in a tiny middleware that rewrites the ASGI client
    tuple so the bearer-token middleware sees us as a non-loopback
    caller. Lets us test the gate end-to-end without monkeypatching
    Starlette internals."""
    _, app, _ = webapp_client

    async def fake_remote(scope, receive, send):
        if scope.get("type") == "http":
            scope["client"] = ("10.0.0.42", 12345)
        await app(scope, receive, send)

    return TestClient(fake_remote)


@pytest.fixture
def loopback_client(webapp_client):
    """Counterpart of non_loopback_client — pins the ASGI client to
    127.0.0.1 so the middleware's loopback bypass kicks in. The default
    TestClient reports ``("testclient", 50000)``, which is NOT in
    ``_LOOPBACK_HOSTS``."""
    _, app, _ = webapp_client

    async def fake_loopback(scope, receive, send):
        if scope.get("type") == "http":
            scope["client"] = ("127.0.0.1", 12345)
        await app(scope, receive, send)

    return TestClient(fake_loopback)


class TestNoTokenConfigured:
    def test_unauthenticated_requests_succeed(self, webapp_client):
        client, app, _ = webapp_client
        # Default fixture has auth_token == "" so the gate is off.
        assert app.state.webapp_config.auth_token == ""
        resp = client.get("/api/config")
        assert resp.status_code == 200


class TestLoopbackBypass:
    def test_loopback_skips_token_even_when_configured(
        self, webapp_client, loopback_client
    ):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        # No Authorization header — loopback still allowed.
        resp = loopback_client.get("/api/config")
        assert resp.status_code == 200


class TestNonLoopback:
    def test_missing_token_returns_401(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        resp = non_loopback_client.get("/api/config")
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")

    def test_wrong_token_returns_401(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        resp = non_loopback_client.get(
            "/api/config",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

    def test_correct_token_in_header(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        resp = non_loopback_client.get(
            "/api/config",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert resp.status_code == 200

    def test_correct_token_in_query_string(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        resp = non_loopback_client.get("/api/config?token=secret-token")
        assert resp.status_code == 200

    def test_healthz_is_exempt(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        resp = non_loopback_client.get("/healthz")
        assert resp.status_code == 200

    def test_static_is_exempt(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        # Static mount serves the JS file; the gate must not block it.
        resp = non_loopback_client.get("/static/app.js")
        # 200 if app.js exists, 404 if missing — but NEVER 401.
        assert resp.status_code != 401

    def test_login_is_exempt(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        app.state.webapp_config.auth_password = "letmein"
        # No token presented — login should still be reachable.
        resp = non_loopback_client.post(
            "/api/login",
            json={"password": "letmein"},
        )
        # 200 with the token handed back.
        assert resp.status_code == 200
        assert resp.json()["token"] == "secret-token"


class TestLoginEndpoint:
    def test_503_when_no_password_configured(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = ""
        resp = client.post("/api/login", json={"password": "x"})
        assert resp.status_code == 503

    def test_503_when_no_token_configured(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "letmein"
        app.state.webapp_config.auth_token = ""
        resp = client.post("/api/login", json={"password": "letmein"})
        assert resp.status_code == 503

    def test_401_on_bad_password(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "letmein"
        app.state.webapp_config.auth_token = "tok"
        resp = client.post("/api/login", json={"password": "wrong"})
        assert resp.status_code == 401

    def test_200_with_token_on_good_password(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "letmein"
        app.state.webapp_config.auth_token = "tok"
        resp = client.post("/api/login", json={"password": "letmein"})
        assert resp.status_code == 200
        assert resp.json() == {"token": "tok"}
