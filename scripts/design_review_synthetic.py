"""Throwaway webapp for ``/design-review``'s synthetic and ``--e2e`` runs (#297).

``[design.review.synthetic]`` in ``.fleet.toml`` runs this with the repo's venv
python, from the repo root. It boots the same disposable webapp the e2e suite's
autoboot fixture does (a free loopback port, every runtime-data path off the
real folder) so the walk never touches the live tray on :8443, prints
``URL=<base>`` once ``/healthz`` answers, then waits until stdin closes and
stops the server.

Nothing is written outside a temporary directory, and no recording is made: the
walk only opens tabs and dialogs.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.webapp.event_loop import LOOP_FACTORY  # noqa: E402
from src.no_window import NO_WINDOW  # noqa: E402
from src.runtime_data import app_dir_env_var  # noqa: E402

logger = logging.getLogger("design_review_synthetic")
STARTUP_TIMEOUT_S = 60.0
STOP_GRACE_S = 5.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _spawn_env(state_dir: Path) -> dict[str, str]:
    """The child's environment, with every runtime-data path inside *state_dir*."""
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "VT_USAGE_DB_PATH": str(state_dir / "usage.sqlite3"),
        app_dir_env_var("voice-transcriber"): str(state_dir / "data"),
    }


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except OSError as exc:
            logger.debug("CTRL_BREAK_EVENT failed: %s", exc)
    proc.terminate()
    try:
        proc.wait(timeout=STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=STOP_GRACE_S)


def _wait_healthz(base: str, proc: subprocess.Popen) -> bool:
    deadline = time.time() + STARTUP_TIMEOUT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if requests.get(f"{base}/healthz", timeout=2).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.3)
    return False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    state_dir = Path(tempfile.mkdtemp(prefix="vt-synthetic-"))
    log_path = state_dir / "webapp.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW if sys.platform == "win32" else 0
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.webapp.server:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
             "--loop", LOOP_FACTORY],
            cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
            env=_spawn_env(state_dir), creationflags=flags,
        )
        try:
            if not _wait_healthz(base, proc):
                logger.error("❌ synthetic webapp not reachable; log: %s", log_path)
                return 1
            logger.info("✅ synthetic webapp up on %s (state %s)", base, state_dir)
            print(f"URL={base}", flush=True)
            # The walk closes our stdin when it is done; reading to EOF blocks until then.
            sys.stdin.read()
            return 0
        finally:
            _stop(proc)


if __name__ == "__main__":
    sys.exit(main())
