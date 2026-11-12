"""Unit tests for `src/process_supervisor.py`.

Extracted from `src/whisper_server/manager.py` and `app/webapp/manager.py`
(voice-transcriber#160) so the adopt-or-spawn primitives — the TCP probe,
the stop ladder, and the readiness poll — live in exactly one place. Both
managers only exercise these indirectly (via real subprocess spawns in the
e2e suite / manual whisper-server use), so this module is the fast,
subprocess-light regression net for the shared mechanics themselves.
"""

from __future__ import annotations

import ast
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW
from src.process_supervisor import (
    OWNERSHIP_EXTERNAL,
    OWNERSHIP_NONE,
    OWNERSHIP_OURS,
    is_port_in_use,
    ownership_of,
    spawn_managed,
    stop_pid,
    stop_popen,
    wait_until_ready,
)

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
            creationflags=NO_WINDOW,
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
        proc = subprocess.Popen([str(PYTHON), "-c", "pass"], creationflags=NO_WINDOW)
        proc.wait(timeout=5)
        assert proc.poll() is not None
        stop_popen(proc, name="test-proc", terminate_timeout=5, kill_timeout=3)


class TestStopPid:
    def test_stops_a_process_we_did_not_popen(self):
        proc = subprocess.Popen(
            [str(PYTHON), "-c", "import time; time.sleep(60)"],
            creationflags=NO_WINDOW,
        )
        try:
            stop_pid(proc.pid, name="test-proc", terminate_timeout=5, kill_timeout=3)
            assert proc.wait(timeout=5) is not None  # reaped
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_is_a_noop_for_a_pid_that_is_already_gone(self):
        proc = subprocess.Popen([str(PYTHON), "-c", "pass"], creationflags=NO_WINDOW)
        proc.wait(timeout=5)
        stop_pid(proc.pid, name="test-proc", terminate_timeout=5, kill_timeout=3)


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


class TestOwnershipOf:
    @staticmethod
    def _proc(alive: bool):
        class _P:
            def poll(self):
                return None if alive else 0

        return _P()

    def test_ours_needs_a_live_child_and_a_reachable_service(self):
        assert ownership_of(self._proc(True), reachable=True) == OWNERSHIP_OURS

    def test_a_reachable_service_without_a_live_child_is_external(self):
        assert ownership_of(None, reachable=True) == OWNERSHIP_EXTERNAL
        assert ownership_of(self._proc(False), reachable=True) == OWNERSHIP_EXTERNAL

    def test_unreachable_is_none_even_with_a_live_child(self):
        assert ownership_of(self._proc(True), reachable=False) == OWNERSHIP_NONE
        assert ownership_of(None, reachable=False) == OWNERSHIP_NONE


class TestSpawnManaged:
    def test_child_gets_utf8_env_and_the_extra_overlay(self, tmp_path):
        out = tmp_path / "env.txt"
        script = (
            "import os, sys; "
            "open(sys.argv[1], 'w').write("
            "os.environ['PYTHONUTF8'] + os.environ['PYTHONIOENCODING'] + os.environ['PROBE'])"
        )
        proc = spawn_managed(
            [str(PYTHON), "-c", script, str(out)],
            name="probe",
            cwd=tmp_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            extra_env={"PROBE": "x"},
            not_found_message=lambda exc: "unused",
        )
        assert proc.wait(timeout=10) == 0
        assert out.read_text() == "1utf-8x"

    def test_missing_executable_raises_the_callers_message(self, tmp_path):
        with pytest.raises(RuntimeError, match="no such launcher"):
            spawn_managed(
                [str(tmp_path / "does-not-exist.exe")],
                name="probe",
                cwd=tmp_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                not_found_message=lambda exc: "no such launcher",
            )

    def test_other_launch_failures_name_the_service(self, tmp_path):
        with pytest.raises(RuntimeError, match="failed to launch probe"):
            spawn_managed(
                [123],  # type: ignore[list-item]  # a non-path argv entry: TypeError, not FileNotFoundError
                name="probe",
                cwd=tmp_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                not_found_message=lambda exc: "unused",
            )


MANAGER_SOURCES = (
    Path("src/whisper_server/manager.py"),
    Path("app/webapp/manager.py"),
)


@pytest.mark.parametrize("path", MANAGER_SOURCES, ids=str)
def test_managers_spawn_only_through_the_supervisor(path):
    """The structural guard for the duplication class: the spawn recipe and
    the ownership ladder live in `process_supervisor`, so a manager calling
    `subprocess.Popen` itself has re-forked the lifecycle."""
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    popen_calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    ]
    assert popen_calls == [], f"{path} calls Popen directly at lines {popen_calls}"
