"""End-to-end smoke test: launch uvicorn on a free ephemeral port,
hit /healthz and /api/config over real HTTP, then shut down cleanly.

Marked ``smoke`` so quick runs (``pytest -m "not smoke"``) can skip it
on every save. The slow bits — process startup, port binding, real
TCP — are the whole point: integration tests catch things unit tests
can't (uvicorn config typos, lifespan crashes, importable-module
regressions).
"""

from __future__ import annotations

# Standard library imports
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

# Third-party imports
import pytest
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def _free_port() -> int:
    """Ask the OS for an ephemeral port and immediately release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def uvicorn_proc(tmp_path: Path) -> Iterator[tuple[subprocess.Popen, int]]:
    """Spawn a real uvicorn process bound to a free port. Tear down via
    SIGTERM on the way out. Skips when the project venv is missing
    (e.g. a fresh checkout where setup hasn't been run yet)."""
    if not PYTHON.exists():
        pytest.skip(f"venv python not found at {PYTHON}")

    port = _free_port()
    cmd = [
        str(PYTHON),
        "-m", "uvicorn", "app.webapp.server:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "error",
    ]
    # A subprocess inherits none of pytest's monkeypatching, so conftest's
    # activity-log isolation cannot reach it — this boot would write into the
    # real store. `VT_ACTIVITY_DB_PATH` is the highest-precedence override
    # exactly for this case (see src/runtime_data.py).
    env = {**os.environ, "VT_ACTIVITY_DB_PATH": str(tmp_path / "activity.sqlite3")}
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Poll the health endpoint until the server is ready or we give up.
    deadline = time.time() + 20.0
    last_err: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"uvicorn exited early with code {proc.returncode}")
        try:
            r = requests.get(f"http://127.0.0.1:{port}/healthz", timeout=1.0)
            if r.status_code == 200:
                break
        except requests.RequestException as exc:
            last_err = exc
        time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail(f"uvicorn did not come up within 20s ({last_err})")

    try:
        yield proc, port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


@pytest.mark.smoke
class TestWebappSmoke:
    def test_healthz_responds_ok(self, uvicorn_proc):
        _, port = uvicorn_proc
        r = requests.get(f"http://127.0.0.1:{port}/healthz", timeout=2.0)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_api_config_returns_polish_model_list(
        self, uvicorn_proc, sample_polish_payload
    ):
        _, port = uvicorn_proc
        r = requests.get(f"http://127.0.0.1:{port}/api/config", timeout=2.0)
        assert r.status_code == 200
        body = r.json()
        # The webapp boots with whatever is in webapp_config.json on disk;
        # at minimum, every alias in the sample must be present in the
        # served list (the user's config may have added more).
        for alias in sample_polish_payload["polish_models_available"]:
            assert alias in body["polish_models_available"], (
                f"alias {alias!r} from sample.json missing from "
                f"running webapp's polish_models_available"
            )

    def test_api_status_returns_expected_shape(self, uvicorn_proc):
        _, port = uvicorn_proc
        # Generous read timeout: /api/status synchronously probes whisper
        # (request_timeout_seconds≈1.5) and the LLM hub (timeout≈2.0). When
        # both backends are absent — as on the CI runner — those probes time
        # out sequentially (~3.7s) before the handler responds. A dev box
        # usually has the hub/whisper running, so the endpoint answers fast
        # there; the wider budget keeps this shape assertion green on a
        # backend-less runner without slowing local runs. This test asserts
        # shape, not latency.
        r = requests.get(f"http://127.0.0.1:{port}/api/status", timeout=10.0)
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) >= {"whisper", "llm_hub", "ffmpeg_present"}

    def test_index_html_served(self, uvicorn_proc):
        _, port = uvicorn_proc
        r = requests.get(f"http://127.0.0.1:{port}/", timeout=2.0)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
