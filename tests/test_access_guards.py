"""Guards that run before the webapp is reachable from outside the box.

Two independent checkpoints, both cheap and both easy to regress because
neither fires in the normal loopback flow:

- ``src.tunnel.publish_refusal_reason`` — consulted before cloudflared is
  spawned, so a publicly-reachable origin can't come up while the request
  gate is configured off.
- ``scripts/set_password.py``'s length floor — the value it writes is the
  one reachable over that same public hostname.
"""

from __future__ import annotations

# Standard library imports
import importlib.util
import sys
from pathlib import Path

# Third-party imports
import pytest

from src.tunnel import publish_refusal_reason

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_set_password():
    """Import ``scripts/set_password.py`` by path — ``scripts/`` is a
    package but the module is normally run as a __main__ script."""
    spec = importlib.util.spec_from_file_location(
        "_set_password_under_test", PROJECT_ROOT / "scripts" / "set_password.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestPublishRefusal:
    def test_refuses_when_no_token_is_configured(self):
        reason = publish_refusal_reason("")
        assert reason is not None
        assert "auth_token" in reason

    def test_refuses_on_whitespace_only_token(self):
        assert publish_refusal_reason("   ") is not None

    def test_allows_when_a_token_is_configured(self):
        assert publish_refusal_reason("s3cr3t-token-value") is None


class TestTrayConsultsTheGuard:
    """The predicate is only useful if the spawn path actually calls it."""

    def test_tunnel_worker_returns_before_spawning(self, monkeypatch):
        from app.gui import service_supervisor as svc_mod

        spawned = []
        monkeypatch.setattr(
            svc_mod, "spawn_cloudflared",
            lambda *a, **k: spawned.append(a) or object(),
        )
        monkeypatch.setattr(svc_mod, "current_auth_token", lambda: "")

        notes = []
        fake = object.__new__(svc_mod.ServiceSupervisor)
        fake._notify = lambda *a: notes.append(a)  # type: ignore[attr-defined]
        svc_mod.ServiceSupervisor.start_tunnel(fake)

        assert spawned == [], "cloudflared must not be spawned without a token"
        assert notes, "the refusal must be surfaced to the user, not swallowed"


class TestPasswordFloor:
    @pytest.fixture
    def script(self):
        module = _load_set_password()
        yield module
        sys.modules.pop("_set_password_under_test", None)

    def test_floor_is_enforced(self, script, monkeypatch, capsys):
        saved = []
        cfg = type("Cfg", (), {"auth_token": "tok", "auth_password": ""})()
        monkeypatch.setattr(script, "load_webapp_config", lambda: cfg)
        monkeypatch.setattr(script, "save_webapp_config", lambda c: saved.append(c))
        monkeypatch.setattr(sys, "argv", ["set_password.py", "320100"])

        assert script.main() == 1
        assert saved == [], "a value under the floor must not be persisted"
        assert cfg.auth_password == ""

    def test_value_at_or_above_the_floor_is_accepted(
        self, script, monkeypatch
    ):
        saved = []
        cfg = type("Cfg", (), {"auth_token": "tok", "auth_password": ""})()
        monkeypatch.setattr(script, "load_webapp_config", lambda: cfg)
        monkeypatch.setattr(script, "save_webapp_config", lambda c: saved.append(c))
        long_enough = "x" * script.MIN_PASSWORD_LENGTH
        monkeypatch.setattr(sys, "argv", ["set_password.py", long_enough])

        assert script.main() == 0
        assert saved == [cfg]
        assert cfg.auth_password == long_enough

    def test_clear_still_works(self, script, monkeypatch):
        saved = []
        cfg = type("Cfg", (), {"auth_token": "tok", "auth_password": "existing"})()
        monkeypatch.setattr(script, "load_webapp_config", lambda: cfg)
        monkeypatch.setattr(script, "save_webapp_config", lambda c: saved.append(c))
        monkeypatch.setattr(sys, "argv", ["set_password.py", "--clear"])

        assert script.main() == 0
        assert cfg.auth_password == ""
