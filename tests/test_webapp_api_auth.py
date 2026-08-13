"""FastAPI tests for the BearerTokenMiddleware.

Loopback callers (the TestClient is one — 127.0.0.1) always bypass, so
to exercise the gate we override `request.client.host` via the ASGI
``scope``-rewriting middleware below.
"""

from __future__ import annotations

# Third-party imports
import pytest
from fastapi.testclient import TestClient

from app.webapp.routers.auth import FREE_ATTEMPTS, AttemptLimiter


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


class TestLoginAttemptLimiter:
    """The unit, driven with an injected clock so no test sleeps."""

    def test_free_attempts_are_not_delayed(self):
        lim = AttemptLimiter(free_attempts=3, base_delay=2.0)
        for _ in range(3):
            lim.record_failure("c", now=0.0)
        assert lim.retry_after("c", now=0.0) == 0.0

    def test_delay_starts_after_the_free_allowance(self):
        lim = AttemptLimiter(free_attempts=3, base_delay=2.0)
        for _ in range(4):
            lim.record_failure("c", now=0.0)
        assert lim.retry_after("c", now=0.0) == pytest.approx(2.0)

    def test_delay_doubles_and_is_capped(self):
        lim = AttemptLimiter(free_attempts=0, base_delay=1.0, max_delay=4.0)
        seen = []
        for _ in range(5):
            lim.record_failure("c", now=0.0)
            seen.append(lim.retry_after("c", now=0.0))
        assert seen == [1.0, 2.0, 4.0, 4.0, 4.0]

    def test_waiting_out_the_delay_clears_it(self):
        lim = AttemptLimiter(free_attempts=0, base_delay=5.0)
        lim.record_failure("c", now=0.0)
        assert lim.retry_after("c", now=4.9) > 0
        assert lim.retry_after("c", now=5.0) == 0.0

    def test_clients_are_tracked_independently(self):
        lim = AttemptLimiter(free_attempts=0, base_delay=5.0)
        lim.record_failure("a", now=0.0)
        assert lim.retry_after("a", now=0.0) > 0
        assert lim.retry_after("b", now=0.0) == 0.0

    def test_reset_clears_the_history(self):
        lim = AttemptLimiter(free_attempts=0, base_delay=5.0)
        lim.record_failure("c", now=0.0)
        lim.reset("c")
        assert lim.retry_after("c", now=0.0) == 0.0


class TestLoginThrottling:
    """End-to-end: repeated rejections stop being answered on their merits."""

    def test_repeated_rejections_eventually_return_429(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "correct horse battery"
        app.state.webapp_config.auth_token = "tok"

        codes = [
            client.post("/api/login", json={"password": f"guess-{i}"}).status_code
            for i in range(FREE_ATTEMPTS + 2)
        ]
        # The free allowance is answered normally; past it the endpoint
        # stops evaluating the guess at all.
        assert codes[:FREE_ATTEMPTS] == [401] * FREE_ATTEMPTS
        assert codes[-1] == 429

    def test_throttled_response_carries_retry_after(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "correct horse battery"
        app.state.webapp_config.auth_token = "tok"
        for i in range(FREE_ATTEMPTS + 2):
            resp = client.post("/api/login", json={"password": f"guess-{i}"})
        assert resp.status_code == 429
        assert int(resp.headers["Retry-After"]) >= 1

    def test_correct_password_still_works_inside_the_allowance(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "correct horse battery"
        app.state.webapp_config.auth_token = "tok"
        for i in range(FREE_ATTEMPTS - 1):
            client.post("/api/login", json={"password": f"guess-{i}"})
        resp = client.post(
            "/api/login", json={"password": "correct horse battery"}
        )
        assert resp.status_code == 200

    def test_success_resets_the_counter(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "correct horse battery"
        app.state.webapp_config.auth_token = "tok"
        for i in range(FREE_ATTEMPTS - 1):
            client.post("/api/login", json={"password": f"guess-{i}"})
        client.post("/api/login", json={"password": "correct horse battery"})
        # Counter cleared — the next wrong guess is answered on its merits,
        # not throttled.
        resp = client.post("/api/login", json={"password": "wrong-again"})
        assert resp.status_code == 401
