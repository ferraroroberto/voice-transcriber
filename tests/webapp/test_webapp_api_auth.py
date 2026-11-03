"""FastAPI tests for the BearerTokenMiddleware.

Loopback callers (the TestClient is one — 127.0.0.1) always bypass, so
to exercise the gate we override `request.client.host` via the ASGI
``scope``-rewriting middleware below.
"""

from __future__ import annotations

# Standard library imports
import threading

# Third-party imports
import pytest
from fastapi.testclient import TestClient

from app.webapp.routers.auth import make_login_alert
from src.login_lockout import FREE_ATTEMPTS, LoginLockout


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

    def test_non_ascii_wrong_token_returns_401(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret-token"
        header = non_loopback_client.get(
            "/api/config", headers={"Authorization": "Bearer café".encode("latin-1")},
        )
        query = non_loopback_client.get("/api/config?token=se%C3%B1or")
        assert header.status_code == 401
        assert query.status_code == 401

    def test_non_ascii_token_is_accepted(self, webapp_client, non_loopback_client):
        _, app, _ = webapp_client
        app.state.webapp_config.auth_token = "señor-token"
        resp = non_loopback_client.get("/api/config?token=se%C3%B1or-token")
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

    def test_401_on_non_ascii_bad_password(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "letmein"
        app.state.webapp_config.auth_token = "secret-token"
        resp = client.post("/api/login", json={"password": "letmèin"})
        assert resp.status_code == 401

    def test_200_with_non_ascii_owner_password(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "passñword"
        app.state.webapp_config.auth_token = "secret-token"
        resp = client.post("/api/login", json={"password": "passñword"})
        assert resp.status_code == 200
        assert resp.json()["token"] == "secret-token"

    def test_200_with_token_on_good_password(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_password = "letmein"
        app.state.webapp_config.auth_token = "tok"
        resp = client.post("/api/login", json={"password": "letmein"})
        assert resp.status_code == 200
        assert resp.json() == {"token": "tok"}


class _FakeClock:
    """A monotonic clock the test advances by hand, so nothing sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.delivered = threading.Event()

    def send_text(self, text: str) -> None:
        self.sent.append(text)
        self.delivered.set()


@pytest.fixture
def lockout_rig(webapp_client):
    """The app with a lock-out on a fake clock and a fake alert notifier."""
    client, app, _ = webapp_client
    cfg = app.state.webapp_config
    cfg.auth_password = "correct horse battery"
    cfg.auth_token = "tok"
    cfg.notifier = "telegram"
    cfg.telegram_bot_token = "bot-secret"
    cfg.telegram_alert_chat_id = "chat-secret"
    clock = _FakeClock()
    notifier = _FakeNotifier()
    builds: list[str] = []

    def build(name, telegram):
        builds.append(name)
        return notifier

    app.state.login_lockout = LoginLockout(
        clock=clock, on_alert=make_login_alert(lambda: cfg, build=build)
    )
    return client, app, clock, notifier, builds


class TestLoginLockoutRoute:
    def test_wrong_passwords_then_429_then_success_after_the_wait(self, lockout_rig):
        client, _, clock, _, _ = lockout_rig
        codes = [
            client.post("/api/login", json={"password": f"guess-{i}"}).status_code
            for i in range(FREE_ATTEMPTS + 1)
        ]
        assert codes == [401] * (FREE_ATTEMPTS + 1)
        locked = client.post("/api/login", json={"password": "correct horse battery"})
        assert locked.status_code == 429
        assert int(locked.headers["Retry-After"]) >= 1
        clock.now += float(locked.headers["Retry-After"])
        ok = client.post("/api/login", json={"password": "correct horse battery"})
        assert ok.status_code == 200
        assert ok.json() == {"token": "tok"}

    def test_three_wrong_passwords_alert_the_owner_once(self, lockout_rig):
        client, _, _, notifier, builds = lockout_rig
        for i in range(2):
            client.post("/api/login", json={"password": f"guess-{i}"})
        assert builds == []
        client.post("/api/login", json={"password": "guess-2"})
        assert notifier.delivered.wait(timeout=5)
        for i in range(3, 6):
            client.post("/api/login", json={"password": f"guess-{i}"})
        assert builds == ["telegram"]
        assert len(notifier.sent) == 1
        text = notifier.sent[0]
        assert "voice-transcriber" in text
        assert "3 wrong passwords" in text
        assert "testclient" in text
        for secret in ("guess-", "correct horse", "tok", "bot-secret", "chat-secret"):
            assert secret not in text

    def test_notifier_none_sends_nothing(self, lockout_rig):
        client, app, _, notifier, _ = lockout_rig
        app.state.webapp_config.notifier = "none"
        app.state.login_lockout = LoginLockout(
            on_alert=make_login_alert(lambda: app.state.webapp_config)
        )
        for i in range(4):
            assert client.post("/api/login", json={"password": f"g{i}"}).status_code == 401
        assert notifier.sent == []

    def test_the_pc_itself_is_never_locked_out(self, lockout_rig, loopback_client):
        _, _, _, notifier, builds = lockout_rig
        codes = [
            loopback_client.post("/api/login", json={"password": f"guess-{i}"}).status_code
            for i in range(FREE_ATTEMPTS + 4)
        ]
        assert codes == [401] * (FREE_ATTEMPTS + 4)
        ok = loopback_client.post("/api/login", json={"password": "correct horse battery"})
        assert ok.status_code == 200
        assert builds == [] and notifier.sent == []


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
