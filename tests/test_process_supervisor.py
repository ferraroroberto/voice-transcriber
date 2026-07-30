"""Unit tests for `src/process_supervisor.py`.

Extracted from `src/whisper_server/manager.py` and `app/webapp/manager.py`
(voice-transcriber#160) so the adopt-or-spawn primitives — the TCP probe,
the stop ladder, and the readiness poll — live in exactly one place. Both
managers only exercise these indirectly (via real subprocess spawns in the
e2e suite / manual whisper-server use), so this module is the fast,
subprocess-light regression net for the shared mechanics themselves.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.process_supervisor import is_port_in_use, stop_popen, wait_until_ready

PYTHON = Path(sys.executable)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestIsPortInUse:
    def test_false_on_a_free_port(self):
        assert is_port_in_use("127.0.0.1", _free_port()) is False

    def test_true_on_a_listening_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            assert is_port_in_use("127.0.0.1", port) is True

    def test_bind_host_0_0_0_0_is_normalized_to_loopback(self):
        """`0.0.0.0` is a bind host, not a connect target — probing it
        should mean 'is something listening on this box's loopback',
        exactly what WebappManager relies on for its config.host=0.0.0.0
        default."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            assert is_port_in_use("0.0.0.0", port) is True


class TestStopPopen:
    def test_stops_a_running_process(self):
        proc = subprocess.Popen(
            [str(PYTHON), "-c", "import time; time.sleep(60)"],
        )
        try:
            assert proc.poll() is None  # still running
            stop_popen(proc, name="test-proc", terminate_timeout=5, kill_timeout=3)
            assert proc.poll() is not None  # stopped
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_is_a_noop_reporting_a_failure_on_an_already_exited_process(self):
        """`wait()` on an already-exited process returns immediately with
        its exit code rather than raising — the ladder should complete
        without hanging."""
        proc = subprocess.Popen([str(PYTHON), "-c", "pass"])
        proc.wait(timeout=5)
        assert proc.poll() is not None
        stop_popen(proc, name="test-proc", terminate_timeout=5, kill_timeout=3)


class TestWaitUntilReady:
    def test_returns_once_reachable(self):
        calls = []

        def _is_reachable():
            calls.append(1)
            return len(calls) >= 3

        wait_until_ready(
            still_alive=lambda: True,
            is_reachable=_is_reachable,
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
            not_alive_message=lambda: "not alive",
            timeout_message="timed out",
        )
        assert len(calls) == 3

    def test_raises_not_alive_message_when_process_dies(self):
        with pytest.raises(RuntimeError, match="it died"):
            wait_until_ready(
                still_alive=lambda: False,
                is_reachable=lambda: False,
                timeout_seconds=5.0,
                poll_interval_seconds=0.01,
                not_alive_message=lambda: "it died",
                timeout_message="timed out",
            )

    def test_raises_timeout_message_when_never_reachable(self):
        with pytest.raises(RuntimeError, match="timed out"):
            wait_until_ready(
                still_alive=lambda: True,
                is_reachable=lambda: False,
                timeout_seconds=0.05,
                poll_interval_seconds=0.01,
                not_alive_message=lambda: "not alive",
                timeout_message="timed out",
            )
