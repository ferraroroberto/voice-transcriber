"""Selector event-loop shim (issue #113) — root cause of the :8443 wedge.

asyncio's default Windows proactor event loop closes its listening socket
on any aborted client connection (WinError 64); the selector loop's accept
path doesn't. These tests cover the wiring (every uvicorn spawn of
``app.webapp.server:app`` picks the shim) and the actual accept-loop
resilience the shim buys.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
from pathlib import Path

import pytest

from app.webapp.event_loop import LOOP_FACTORY, selector_loop_factory
from app.webapp.manager import WebappManager, WebappRuntimeConfig, build_uvicorn_command

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_selector_loop_factory_returns_selector_instance_on_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    sentinel = object()
    monkeypatch.setattr(asyncio, "SelectorEventLoop", lambda: sentinel)
    assert selector_loop_factory() is sentinel


def test_selector_loop_factory_defers_on_other_platforms(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    sentinel = object()
    monkeypatch.setattr(asyncio, "new_event_loop", lambda: sentinel)
    assert selector_loop_factory() is sentinel


def test_selector_loop_factory_is_zero_arg_and_returns_an_instance():
    """Regression pin: uvicorn imports a *custom* --loop target and calls
    it as a bare Callable[[], AbstractEventLoop] -- no use_subprocess kwarg,
    and it must return an instantiated loop, not a loop class (app-launcher
    #388's original bug: returning the class left Runner calling unbound
    methods)."""
    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
    finally:
        loop.close()


def test_manager_build_command_passes_loop_factory():
    mgr = WebappManager(WebappRuntimeConfig(port=18443))
    cmd = mgr._build_command()
    assert "--loop" in cmd
    assert cmd[cmd.index("--loop") + 1] == LOOP_FACTORY


def test_manager_build_command_trusts_loopback_proxy_headers():
    """Regression pin for issue #117: without --proxy-headers/
    --forwarded-allow-ips, request.client.host in middleware.py sees the
    raw TCP peer -- cloudflared connecting over loopback -- and every
    tunnel request would hit the loopback auth bypass meant only for the
    local tk window."""
    mgr = WebappManager(WebappRuntimeConfig(port=18443))
    cmd = mgr._build_command()
    assert "--proxy-headers" in cmd
    assert "--forwarded-allow-ips" in cmd
    assert cmd[cmd.index("--forwarded-allow-ips") + 1] == "127.0.0.1"


def test_e2e_autoboot_wires_loop_factory():
    """conftest.py's disposable-webapp spawn isn't independently importable
    (module-scoped fixture with subprocess side effects) — a static check
    that its wa_cmd references the same shim is enough to catch drift."""
    src = (_REPO_ROOT / "tests" / "e2e" / "conftest.py").read_text(encoding="utf-8")
    assert "from app.webapp.event_loop import LOOP_FACTORY" in src
    assert '"--loop",\n            LOOP_FACTORY,' in src


def test_webapp_bat_delegates_to_run_webapp_module():
    """webapp.bat (voice-transcriber#174) carries no uvicorn flag list of
    its own — it shells out to scripts/run_webapp.py, which sources the
    argv from build_uvicorn_command (see
    test_run_webapp_delegates_to_shared_command_builder for the loop/proxy-
    headers wiring pin at that single source)."""
    src = (_REPO_ROOT / "webapp.bat").read_text(encoding="utf-8")
    assert "scripts\\run_webapp.py" in src
    assert "-m uvicorn" not in src


def test_run_webapp_delegates_to_shared_command_builder():
    """Pin the *wiring*, not a duplicated flag list: webapp.bat used to
    hand-write the uvicorn argv twice (HTTP/HTTPS branches) and had already
    drifted from WebappManager's spawn (missing --log-level warning) by the
    time this issue was filed (voice-transcriber#174) -- assert
    scripts/run_webapp.py now sources the command from the same place
    WebappManager and run_named_tunnel.py do, so a flag can't go missing
    again."""
    src = (_REPO_ROOT / "scripts" / "run_webapp.py").read_text(encoding="utf-8")
    assert "from app.webapp.manager import build_uvicorn_command, cert_paths" in src
    assert "build_uvicorn_command(" in src


def test_build_uvicorn_command_wires_proxy_headers_and_loop():
    """Regression pin for issue #117 (proxy headers) and #113 (the loop
    shim) at their single source: WebappManager._build_command and
    scripts/run_named_tunnel.py's _spawn_uvicorn both delegate to this
    function (voice-transcriber#160) instead of each carrying their own
    copy of the uvicorn flag list."""
    cmd = build_uvicorn_command("127.0.0.1", 18443, None)
    assert "--proxy-headers" in cmd
    assert "--forwarded-allow-ips" in cmd
    assert cmd[cmd.index("--forwarded-allow-ips") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--loop") + 1] == LOOP_FACTORY


def test_run_named_tunnel_spawn_delegates_to_shared_command_builder():
    """Pin the *wiring*, not a duplicated flag list: this used to be a
    hand-rolled second copy of the uvicorn command that had already
    drifted (missing --loop) by the time this issue was filed
    (voice-transcriber#160) -- assert it now sources the command from
    the same place WebappManager does, so a flag can't go missing again."""
    src = (_REPO_ROOT / "scripts" / "run_named_tunnel.py").read_text(encoding="utf-8")
    assert "from app.webapp.manager import build_uvicorn_command, cert_paths" in src
    assert "build_uvicorn_command(" in src


async def _noop_handler(reader, writer):
    writer.close()


def _abort_connect_sync(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
    try:
        s.settimeout(0.5)
        s.connect(("127.0.0.1", port))
    except OSError:
        pass
    finally:
        s.close()  # SO_LINGER(1, 0) forces an RST instead of a FIN


async def _still_accepting(port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=1.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def _bombard_with_aborts(rounds: int, burst: int) -> None:
    server = await asyncio.start_server(_noop_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        for _ in range(rounds):
            threads = [
                threading.Thread(target=_abort_connect_sync, args=(port,))
                for _ in range(burst)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            await asyncio.sleep(0.02)
            assert await _still_accepting(
                port
            ), "listener died on an aborted client connection (issue #113)"
    finally:
        server.close()
        await server.wait_closed()


def _run_bombard_in_clean_thread(*, rounds: int, burst: int, loop_factory) -> None:
    """Runs asyncio.run() on a dedicated thread so it gets a clean asyncio
    context regardless of what the main thread's event loop state looks like
    (issue #141 — a preceding Playwright sync-API e2e test in the same pytest
    process leaves the main thread's asyncio state dirty, tripping
    asyncio.run()'s running-loop guard)."""
    result: dict = {}

    def _target() -> None:
        try:
            asyncio.run(_bombard_with_aborts(rounds=rounds, burst=burst), loop_factory=loop_factory)
        except BaseException as exc:  # re-raised on the test thread below
            result["exc"] = exc

    t = threading.Thread(target=_target)
    t.start()
    t.join()
    if "exc" in result:
        raise result["exc"]


@pytest.mark.skipif(sys.platform != "win32", reason="proactor-loop bug is Windows-only")
def test_selector_loop_survives_aborted_connections():
    _run_bombard_in_clean_thread(rounds=10, burst=20, loop_factory=asyncio.SelectorEventLoop)


@pytest.mark.skipif(sys.platform != "win32", reason="proactor-loop bug is Windows-only")
def test_proactor_loop_dies_on_aborted_connections():
    """Documents the bug this issue fixes — the shim exists because this
    fails. If a future CPython/uvicorn release fixes the proactor loop
    itself, this test (not the shim) is what should be revisited."""
    with pytest.raises(AssertionError):
        _run_bombard_in_clean_thread(rounds=10, burst=20, loop_factory=asyncio.ProactorEventLoop)
