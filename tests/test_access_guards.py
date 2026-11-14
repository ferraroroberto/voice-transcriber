"""Guards that run before the webapp is reachable from outside the box.

``src.tunnel.publish_refusal_reason`` is consulted by both launchers before
cloudflared is spawned, so a publicly-reachable origin can't come up while the
request gate is configured off. Cheap and easy to regress, because it never
fires in the normal loopback flow.
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
        from app.tray import service_supervisor as svc_mod

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
