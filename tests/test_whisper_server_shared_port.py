"""Who owns the shared ASR port: adopt, refuse, or spawn our own.

Real fake listeners on free loopback ports stand in for the hub's ASR backend,
an external whisper.cpp and an unrelated program. Only the process spawn is
faked, so no whisper binary, model or Foundry runtime is needed.
"""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator, Optional

import pytest

from src.process_supervisor import OWNERSHIP_EXTERNAL, OWNERSHIP_NONE
from src.whisper_server import (
    LISTENER_ASR,
    LISTENER_NONE,
    LISTENER_NOT_ASR,
    LISTENER_UNCONFIRMED,
)
from src.whisper_server.manager import ServerConfig, WhisperServerManager

_FOUNDRY_HEALTH = {
    "status": "ok",
    "engine": "foundry-local",
    "model": "nemotron-3.5-asr-streaming-0.6b-generic-cpu:3",
    "model_id": "nemotron_asr",
}


@contextmanager
def _http_listener(
    status: int = 200, body: object = None, server: Optional[str] = None
) -> Iterator[int]:
    """Answer every GET with `body`, optionally naming itself in `Server`."""
    payload = body if isinstance(body, bytes) else json.dumps(body).encode()

    class Handler(BaseHTTPRequestHandler):
        def version_string(self) -> str:
            return server or "uvicorn"

        def do_GET(self) -> None:  # noqa: N802 — http.server's hook name
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@contextmanager
def _silent_listener() -> Iterator[int]:
    """Holds the port and never answers."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        yield listener.getsockname()[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _manager(tmp_path: Path, port: int, mode: str = "external") -> WhisperServerManager:
    binary = tmp_path / "whisper-server"
    model = tmp_path / "ggml-large-v3-turbo.bin"
    binary.write_bytes(b"")
    model.write_bytes(b"")
    return WhisperServerManager(ServerConfig(
        host="127.0.0.1",
        bind_host="127.0.0.1",
        port=port,
        binary_path=binary,
        model_path=model,
        args=[],
        pid_file=tmp_path / ".whisper_server.pid",
        log_ring_size=100,
        startup_timeout_seconds=2,
        poll_interval_seconds=0.05,
        request_timeout_seconds=0.5,
        project_root=tmp_path,
        mode=mode,
    ))


@pytest.fixture
def spawn(mocker):
    """The one faked step: launching whisper.cpp."""
    fake = mocker.patch("src.whisper_server.manager.spawn_managed")
    fake.return_value.pid = 999_999
    fake.return_value.poll.return_value = None
    return fake


def test_hub_asr_on_the_port_is_adopted_with_its_identity(tmp_path, spawn):
    with _http_listener(body=_FOUNDRY_HEALTH) as port:
        manager = _manager(tmp_path, port)

        status = manager.start()

        spawn.assert_not_called()
        assert (status.running, status.ownership) == (True, OWNERSHIP_EXTERNAL)
        assert status.listener == LISTENER_ASR
        assert status.engine == "foundry-local"
        assert status.model == _FOUNDRY_HEALTH["model"]
        # The tray's model line names what serves, not the configured whisper.
        assert manager.describe(status=status).summary_line() == (
            f"foundry-local: {_FOUNDRY_HEALTH['model']}"
        )


@pytest.mark.parametrize("http_status", [200, 404])
def test_whisper_cpp_is_adopted_by_its_server_header(tmp_path, spawn, http_status):
    """Its /health names no engine or model, and an old build has no route."""
    with _http_listener(http_status, {"status": "ok"}, server="whisper.cpp") as port:
        status = _manager(tmp_path, port).start()

    spawn.assert_not_called()
    assert (status.running, status.ownership) == (True, OWNERSHIP_EXTERNAL)
    assert (status.engine, status.model) == ("whisper.cpp", None)


def test_nothing_on_the_port_starts_our_own_whisper(tmp_path, spawn):
    manager = _manager(tmp_path, _free_port())
    assert manager.status().listener == LISTENER_NONE

    manager.start(wait=False)

    spawn.assert_called_once()


@pytest.mark.parametrize(
    ("http_status", "body"),
    [
        (200, {"status": "ok"}),
        (200, {"status": "ok", "model": "something"}),
        (404, {"detail": "Not Found"}),
        (200, b"<html>hello</html>"),
        (200, ["ok"]),
    ],
    ids=["bare-ok", "no-engine", "no-health-route", "html", "not-an-object"],
)
@pytest.mark.parametrize("mode", ["external", "local"])
def test_non_asr_listener_is_reported_and_nothing_starts(
    tmp_path, spawn, mode, http_status, body
):
    with _http_listener(http_status, body) as port:
        manager = _manager(tmp_path, port, mode=mode)

        status = manager.status()
        assert (status.running, status.ownership) == (False, OWNERSHIP_NONE)
        assert status.listener == LISTENER_NOT_ASR
        assert "not speech recognition" in status.detail
        with pytest.raises(RuntimeError, match="not speech recognition"):
            manager.start()

    spawn.assert_not_called()


@pytest.mark.parametrize(
    "listener",
    [
        _silent_listener,
        lambda: _http_listener(503, {"status": "loading"}),
        lambda: _http_listener(body={**_FOUNDRY_HEALTH, "status": "down"}),
    ],
    ids=["no-answer", "503", "reports-down"],
)
def test_unidentified_listener_is_not_claimed_either_way(tmp_path, spawn, listener):
    with listener() as port:
        manager = _manager(tmp_path, port)

        status = manager.status()
        assert (status.running, status.listener) == (False, LISTENER_UNCONFIRMED)
        with pytest.raises(RuntimeError, match="could not be identified"):
            manager.start()
        # Someone else's listener is never stopped.
        assert manager.stop().listener == LISTENER_UNCONFIRMED
        assert manager.is_port_in_use()

    spawn.assert_not_called()


@pytest.mark.parametrize(
    ("body", "running", "engine", "model"),
    [
        (_FOUNDRY_HEALTH, True, "foundry-local", _FOUNDRY_HEALTH["model"]),
        ({"status": "ok"}, False, None, None),
    ],
    ids=["hub-asr", "non-asr"],
)
def test_api_status_reports_what_holds_the_port(
    tmp_path, webapp_client, body, running, engine, model
):
    client, app, _ = webapp_client
    with _http_listener(body=body) as port:
        app.state.server_manager = _manager(tmp_path, port)
        # Wait for the real probe rather than race the route's short bound.
        app.state.status_cache._wait_s = 5

        whisper = client.get("/api/status").json()["whisper"]

    assert (whisper["running"], whisper["engine"], whisper["model"]) == (running, engine, model)
