"""Shared adopt-or-spawn process-management primitives.

Both `src/whisper_server/manager.py` (`WhisperServerManager`, owns the
whisper.cpp subprocess) and `app/webapp/manager.py` (`WebappManager`, owns
the uvicorn subprocess) run the same adopt-or-spawn lifecycle: three-state
ownership (none / ours / external), a low-level TCP probe before HTTP is
listening, a CTRL_BREAK_EVENT -> terminate() -> kill() stop ladder, and a
"poll until reachable or dead or timed out" readiness wait, and one spawn
recipe (UTF-8 env, own process group, no console window). This module is
the single owner of those primitives, so a fix to one lands in both
managers instead of needing to be re-applied by hand.

Domain-specific bits stay in each manager: PID files and startup-log
draining are whisper-only; the cross-process start lock and SSL cert
lookup are webapp-only. Only the mechanics every managed subprocess shares
live here.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any, Callable, Dict, List, Mapping, Optional, Union

from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

OWNERSHIP_NONE = "none"          # not running
OWNERSHIP_OURS = "ours"          # we started it; we kill on exit
OWNERSHIP_EXTERNAL = "external"  # someone else started it; hands off


def ownership_of(proc: Optional[subprocess.Popen], *, reachable: bool) -> str:
    """The three-state ownership ladder both managers report: `OURS` when
    this process holds a live child and the service answers, `EXTERNAL`
    when the service answers but we did not start it, `NONE` otherwise.
    """
    running_here = proc is not None and proc.poll() is None
    if running_here and reachable:
        return OWNERSHIP_OURS
    if reachable:
        return OWNERSHIP_EXTERNAL
    return OWNERSHIP_NONE


def spawn_managed(
    cmd: List[str],
    *,
    name: str,
    cwd: Union[str, Path],
    stdout: Union[int, IO[Any]],
    stderr: Union[int, IO[Any]],
    not_found_message: Callable[[FileNotFoundError], str],
    extra_env: Optional[Mapping[str, str]] = None,
) -> subprocess.Popen:
    """Popen a managed child with the shared recipe: UTF-8 stdio env, its
    own process group (so CTRL_BREAK_EVENT reaches only it) and no console
    window on Windows. `extra_env` overlays the inherited environment. A
    missing executable raises `RuntimeError(not_found_message(exc))`; any
    other launch failure raises a `RuntimeError` naming `name`.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)

    popen_kwargs: Dict[str, Any] = dict(
        cwd=str(cwd), stdout=stdout, stderr=stderr, env=env,
    )
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW
    try:
        return subprocess.Popen(cmd, **popen_kwargs)
    except FileNotFoundError as exc:
        raise RuntimeError(not_found_message(exc)) from exc
    except Exception as exc:
        raise RuntimeError(f"❌ failed to launch {name}: {exc}") from exc


def is_port_in_use(host: str, port: int, timeout: float = 0.2) -> bool:
    """Low-level TCP connect probe — true even before the HTTP server
    inside is ready to answer requests. `0.0.0.0` (a bind host, not
    something you can connect *to*) is normalized to the loopback address.
    """
    probe_host = host if host != "0.0.0.0" else "127.0.0.1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((probe_host, port)) == 0


def stop_popen(
    proc: subprocess.Popen,
    *,
    name: str,
    terminate_timeout: float = 5.0,
    kill_timeout: float = 3.0,
) -> None:
    """CTRL_BREAK_EVENT (Windows) -> terminate() -> kill() stop ladder for
    a subprocess this manager spawned. `name` is only for the log lines.
    """
    logger.info(f"🛑 Stopping {name} (pid={proc.pid})")
    if sys.platform == "win32":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception as exc:
            logger.debug(f"CTRL_BREAK_EVENT failed for {name}: {exc}")
    proc.terminate()
    try:
        proc.wait(timeout=terminate_timeout)
    except subprocess.TimeoutExpired:
        logger.warning(f"⚠️  {name} didn't exit; killing")
        proc.kill()
        proc.wait(timeout=kill_timeout)


def stop_pid(
    pid: int,
    *,
    name: str,
    terminate_timeout: float = 5.0,
    kill_timeout: float = 3.0,
) -> None:
    """terminate() -> kill() stop ladder for a process this manager did not
    Popen (adopted from a PID file). Same timeout contract as `stop_popen`;
    an already-gone pid is a no-op.
    """
    import psutil  # local: only adopted-pid stops need it

    logger.info(f"🛑 Stopping {name} (pid={pid}, started earlier)")
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=terminate_timeout)
        except psutil.TimeoutExpired:
            logger.warning(f"⚠️  {name} didn't exit; killing")
            proc.kill()
            proc.wait(timeout=kill_timeout)
    except psutil.NoSuchProcess:
        pass


def wait_until_ready(
    *,
    still_alive: Callable[[], bool],
    is_reachable: Callable[[], bool],
    timeout_seconds: float,
    poll_interval_seconds: float,
    not_alive_message: Callable[[], str],
    timeout_message: str,
) -> None:
    """Poll until `is_reachable()` is true, `still_alive()` goes false, or
    `timeout_seconds` elapses — raising `RuntimeError` on either failure
    path. Domain-specific messages (a whisper-server log tail, a bare
    "webapp did not become ready") are supplied by the caller; this owns
    only the polling loop's control flow.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not still_alive():
            raise RuntimeError(not_alive_message())
        if is_reachable():
            return
        time.sleep(poll_interval_seconds)
    raise RuntimeError(timeout_message)
