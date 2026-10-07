"""WhisperServerManager across process boundaries (#244).

`server.bat` is a fresh process per call, so `stop` / `logs` / `status` must
work off what the spawning process left on disk (PID file + log file), not
off in-memory state. A real throwaway listener stands in for whisper-server:
the interpreter binary is the configured "binary" and it binds the port.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.process_supervisor import OWNERSHIP_EXTERNAL, OWNERSHIP_NONE
from src.whisper_server.manager import ServerConfig, WhisperServerManager

_LISTENER = (
    "import socket, sys, time\n"
    "s = socket.socket(); s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen()\n"
    "print('whisper_backend_init: using TEST backend', flush=True)\n"
    "time.sleep(60)\n"
)


def _real_interpreter() -> Path:
    """The actual interpreter binary — a venv's python.exe is a launcher shim
    whose child (not the shim) would be the process holding the port."""
    if sys.platform == "win32":
        return Path(sys.base_prefix) / "python.exe"
    return Path(sys.base_prefix) / "bin" / f"python{sys.version_info.major}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _config(tmp_path: Path, binary: Path, port: int) -> ServerConfig:
    return ServerConfig(
        host="127.0.0.1",
        bind_host="127.0.0.1",
        port=port,
        binary_path=binary,
        model_path=tmp_path / "model.bin",
        args=[],
        pid_file=tmp_path / ".whisper_server.pid",
        log_ring_size=1000,
        startup_timeout_seconds=5,
        poll_interval_seconds=0.1,
        request_timeout_seconds=0.3,
        project_root=tmp_path,
    )


@pytest.fixture
def earlier_server(tmp_path):
    """A listener 'started by an earlier process': PID + log files on disk,
    no Popen handle in the manager under test."""
    binary = _real_interpreter()
    port = _free_port()
    cfg = _config(tmp_path, binary, port)
    log = cfg.log_file.open("wb")
    proc = subprocess.Popen(
        [str(binary), "-c", _LISTENER, str(port)], stdout=log, stderr=subprocess.STDOUT,
    )
    log.close()
    cfg.pid_file.write_text(str(proc.pid), encoding="utf-8")
    deadline = time.time() + 10
    while time.time() < deadline and not WhisperServerManager(cfg).is_port_in_use():
        time.sleep(0.05)
    assert WhisperServerManager(cfg).is_port_in_use(), "listener never came up"
    yield cfg, proc
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


def test_stop_ends_server_started_by_earlier_process(earlier_server):
    cfg, proc = earlier_server
    manager = WhisperServerManager(cfg)
    assert manager.status().ownership == OWNERSHIP_EXTERNAL  # fresh process: no Popen

    result = manager.stop()

    assert result.ownership == OWNERSHIP_NONE
    assert proc.wait(timeout=5) is not None
    assert not cfg.pid_file.exists()
    assert not manager.is_port_in_use()


def test_logs_and_describe_read_earlier_servers_log(earlier_server):
    cfg, _ = earlier_server
    manager = WhisperServerManager(cfg)

    assert any("using TEST backend" in line for line in manager.log_lines())
    assert manager.describe().runtime_info["backend"] == "using TEST backend"


def test_stop_leaves_foreign_listener_alone(tmp_path):
    """PID file names a live process, but it isn't our binary → hands off."""
    port = _free_port()
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen()
    try:
        # Configured binary differs from this test process's interpreter.
        cfg = _config(tmp_path, tmp_path / "not-the-binary.exe", port)
        cfg.pid_file.write_text(str(_own_pid()), encoding="utf-8")
        manager = WhisperServerManager(cfg)

        result = manager.stop()

        assert result.ownership == OWNERSHIP_EXTERNAL
        assert cfg.pid_file.exists()
        assert manager.log_lines() == []
    finally:
        listener.close()


def test_stale_log_not_reported_when_nothing_of_ours_runs(tmp_path):
    cfg = _config(tmp_path, tmp_path / "binary.exe", _free_port())
    cfg.log_file.write_text("whisper_backend_init: using OLD backend\n", encoding="utf-8")
    assert WhisperServerManager(cfg).log_lines() == []


def _own_pid() -> int:
    return os.getpid()
