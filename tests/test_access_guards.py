"""Guards that run before the webapp is reachable from outside the box.

Two independent checkpoints, both cheap and both easy to regress because
neither fires in the normal loopback flow:

- ``src.tunnel.publish_refusal_reason`` — consulted by both launchers
  before cloudflared is spawned, so a publicly-reachable origin can't come
  up while the request gate is configured off.
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


def _load_run_named_tunnel():
    """Import ``scripts/run_named_tunnel.py`` by path, like ``set_password``."""
    spec = importlib.util.spec_from_file_location(
        "_run_named_tunnel_under_test", PROJECT_ROOT / "scripts" / "run_named_tunnel.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestHeadlessLauncherConsultsTheGuard:
    """The no-tray launcher spawns the same tunnel, so it needs the same check."""

    @pytest.fixture
    def launcher(self, tmp_path, monkeypatch):
        # Run directly, the script finds its sibling helpers via sys.path[0];
        # loaded by path it needs scripts/ on the path explicitly.
        monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
        module = _load_run_named_tunnel()
        config = tmp_path / "cloudflared.yml"
        config.write_text(
            "ingress:\n  - hostname: voice.example.test\n"
            "    service: https://localhost:8443\n  - service: http_status:404\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLOUDFLARED_CONFIG", str(config))
        spawned = []
        proc = type("Proc", (), {"stdout": None, "wait": lambda self: 0})()
        monkeypatch.setattr(
            module, "spawn_cloudflared",
            lambda *a, **k: spawned.append("cloudflared") or proc,
        )
        monkeypatch.setattr(
            module, "_spawn_uvicorn", lambda *a, **k: spawned.append("uvicorn"),
        )
        monkeypatch.setattr(module, "_have_listener", lambda port: True)
        monkeypatch.setattr(
            module, "persist_tunnel_url", lambda *a, **k: spawned.append("url"),
        )
        monkeypatch.setattr(module, "stop_popen", lambda *a, **k: None)
        monkeypatch.setattr(module, "remove_tunnel_url_file", lambda *a, **k: None)
        module.spawned = spawned
        yield module
        sys.modules.pop("_run_named_tunnel_under_test", None)

    @pytest.mark.parametrize("token", ["", "   "])
    def test_headless_launcher_returns_before_spawning(self, launcher, monkeypatch, token):
        monkeypatch.setattr(launcher, "configured_auth_token", lambda: token)

        assert launcher.main() == 1
        assert launcher.spawned == [], "nothing may start without a token"

    def test_headless_launcher_proceeds_with_a_token(self, launcher, monkeypatch):
        monkeypatch.setattr(
            launcher, "configured_auth_token", lambda: "s3cr3t-token-value",
        )

        assert launcher.main() == 0
        assert "cloudflared" in launcher.spawned


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
