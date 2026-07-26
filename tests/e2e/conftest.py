"""Fixtures for the voice-transcriber Playwright suite.

Two engine projections, both always on (issue #31):

* **Chromium-desktop** — the fast default projection.
* **WebKit + iPhone** — ``browser_context_args`` merges the
  ``iPhone 15 Pro Max`` device descriptor so the suite exercises an
  iPhone-shaped Mobile-Safari-engine target on Windows. WebKit is the
  same engine family as iOS Safari, so it catches the large majority of
  "Safari is unhappy" regressions before they reach a phone.

Three run modes:

* **Default (bare ``pytest tests/e2e``).** Guarded by the vendor-verbatim
  ``tests/e2e/_e2e_live_guard.py`` (project-scaffolding issue #191/#194;
  same module every fleet adopter copies byte-identical). If the live
  tray's port is occupied and ``VT_E2E_LIVE`` isn't set, it refuses via
  ``pytest.exit`` naming the flag — an accidental bare run must not
  silently load-test the tray the phone is using. If the port is free,
  the suite falls back to booting its own disposable instance, same as
  autoboot below. See issue #158.
* **Live (explicit opt-in).** ``VT_E2E_LIVE=1`` (``scripts/run-e2e.ps1``
  sets it — the deliberate dev-loop entry point) means the caller has
  chosen to *act* on the already-running tray at
  https://127.0.0.1:8443. What "act" means is caller-owned and
  repo-specific per the guard's own contract — the two legitimate
  meanings are read-only assertions against the live instance (the
  ``app-launcher`` ``LAUNCHER_E2E_LIVE`` precedent), or, only where a
  repo genuinely needs to reclaim the port, that repo's own canonical
  restart recipe — never a by-hand kill. This repo's choice is the
  former: read-only smoke checks. The tray holds live sessions and a
  whisper connection, and this repo's own restart discipline already
  forbids hand-rolling a kill of it (the canonical path is ``tray.bat
  --restart``, reserved for an actual restart need, not a test opt-in).
  ``VT_E2E_BASE_URL`` points the suite at any other instance and
  bypasses the guard (already an explicit, deliberate choice of
  target).
* **Autoboot (pre-ship gate).** Enabled with ``--e2e-autoboot`` or
  ``VT_E2E_AUTOBOOT=1``. ``_autoboot_server`` spawns a disposable webapp
  on a free port (HTTPS, reusing ``webapp/certificates/``) and yields
  its URL. In this mode a failure to boot is a hard *failure*, never a
  skip — the whole point of the gate is that a missing server can't
  silently pass. See issue #17.

Chromium is additionally launched with fake-media-stream flags so the
record-flow regression tests (issues #12 / #14) can drive a real
``MediaRecorder`` without a microphone. WebKit cannot fake media
streams, so those tests carry the ``desktop_only`` marker and skip under
the iPhone projection.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib3
from pathlib import Path
from typing import IO, Iterator, Optional

import pytest
import requests
from playwright.sync_api import BrowserContext, Page

from app.webapp.event_loop import LOOP_FACTORY
from tests.e2e._e2e_live_guard import require_disposable_instance

logger = logging.getLogger(__name__)

# Self-signed cert on 8443 — silence the urllib3 noise from /healthz.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEBAPP_CONFIG = _REPO_ROOT / "config" / "webapp_config.json"
_DEFAULT_BASE_URL = "https://127.0.0.1:8443"
_TOKEN_KEY = "vt_auth_token"  # must match TOKEN_KEY in app/webapp/static/state.js

# iPhone 15 Pro Max — the descriptor merged into the WebKit projection.
_IPHONE_DEVICE = "iPhone 15 Pro Max"

_AUTOBOOT_ENV = "VT_E2E_AUTOBOOT"
# Explicit opt-in for adopting the LIVE tray on :8443 (issue #158) — the
# flag name passed to the vendored _e2e_live_guard.require_disposable_instance.
# Without it a bare `pytest tests/e2e` must not silently load-test whatever
# instance is currently listening — that instance may be the one the phone
# is using.
_LIVE_ENV = "VT_E2E_LIVE"
_LIVE_PORT = 8443

# Bounded default Playwright timeout (issue #69).  A stuck auto-waiting
# action (click / goto / wait_for_selector with no explicit timeout=) now
# raises TimeoutError naming the locator at ~15 s instead of inheriting
# Playwright's opaque 30 s default and stacking into a black-box CI hang.
# Override at CI level: E2E_DEFAULT_TIMEOUT_MS=20000 for slower runners.
_DEFAULT_TIMEOUT_MS = int(os.environ.get("E2E_DEFAULT_TIMEOUT_MS", "15000"))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--e2e-autoboot",
        action="store_true",
        default=False,
        help="Boot a disposable webapp on a free port instead of "
        "requiring a live tray. Equivalent to VT_E2E_AUTOBOOT=1.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "desktop_only: needs the Chromium-desktop projection (fake media "
        "streams) — skipped under the WebKit/iPhone projection.",
    )
    # Default the e2e suite to both projections when --browser wasn't
    # passed, so WebKit coverage is impossible to forget (issue #31). A
    # dev can still pin one engine with `--browser chromium` for speed;
    # pytest-playwright treats --browser as append-style.
    selected = config.option.browser
    if not selected:
        selected.extend(["chromium", "webkit"])


def _autoboot_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("--e2e-autoboot")) or (
        os.environ.get(_AUTOBOOT_ENV, "") == "1"
    )


# --------------------------------------------------------- autoboot helpers


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _cert_paths() -> Optional[tuple[Path, Path]]:
    """The ``(cert, key)`` pair if both exist — autoboot serves HTTPS to
    mirror the real phone path. Returns ``None`` for a cert-less checkout
    (autoboot then falls back to plain HTTP so the gate still runs)."""
    certs = _REPO_ROOT / "webapp" / "certificates"
    cert, key = certs / "cert.pem", certs / "key.pem"
    return (cert, key) if cert.exists() and key.exists() else None


def _spawn(cmd: list[str], log: IO[str]) -> subprocess.Popen:
    kwargs: dict = dict(
        cwd=str(_REPO_ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    if sys.platform == "win32":
        # New process group so we can deliver CTRL_BREAK for a clean stop;
        # no window so the test run doesn't flash a console.
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    return subprocess.Popen(cmd, **kwargs)


def _terminate(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("CTRL_BREAK_EVENT failed: %s", exc)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("⚠️  autoboot: process teardown failed: %s", exc)


def _wait_healthz(base: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            res = requests.get(f"{base}/healthz", timeout=2, verify=False)
            if res.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.4)
    return False


@pytest.fixture(scope="session")
def _autoboot_server() -> Iterator[str]:
    """Spawn a disposable webapp on a free port and yield its base URL.

    A hard failure (``pytest.fail``) — never a skip — if it doesn't come
    up: under the pre-ship gate a missing server must not pass silently.
    """
    logs_dir = _REPO_ROOT / "webapp"  # gitignored runtime dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    log = (logs_dir / "e2e-autoboot-webapp.log").open(
        "w", encoding="utf-8", errors="replace"
    )
    proc: Optional[subprocess.Popen] = None
    try:
        port = _free_tcp_port()
        certs = _cert_paths()
        scheme = "https" if certs else "http"
        cmd = [
            sys.executable, "-m", "uvicorn", "app.webapp.server:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning",
            "--loop",
            LOOP_FACTORY,
        ]
        if certs:
            cert, key = certs
            cmd += ["--ssl-keyfile", str(key), "--ssl-certfile", str(cert)]
        proc = _spawn(cmd, log)

        base = f"{scheme}://127.0.0.1:{port}"
        if not _wait_healthz(base, timeout=20):
            _terminate(proc)
            pytest.fail(
                f"autoboot: webapp did not answer /healthz at {base} "
                "within 20s — see webapp/e2e-autoboot-webapp.log"
            )
        logger.info("✅ autoboot: webapp ready at %s", base)
        yield base
    finally:
        _terminate(proc)
        try:
            log.close()
        except Exception:  # pragma: no cover
            pass


@pytest.fixture(autouse=True)
def _bound_default_timeouts(context: BrowserContext) -> None:
    """Cap the default action + navigation timeout (issue #69).

    Sets the bounded default on the context so pages created via
    context.new_page() (e.g. authed_page) inherit the cap automatically.
    Explicit per-call timeout= overrides still take precedence; expect()
    web-first assertions keep their own 5 s default.
    """
    context.set_default_timeout(_DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(_DEFAULT_TIMEOUT_MS)


@pytest.fixture(autouse=True)
def _skip_desktop_only_on_webkit(
    request: pytest.FixtureRequest, browser_name: str
) -> None:
    """Honour the ``desktop_only`` marker — those tests need Chromium's
    fake-media-stream flags, which WebKit has no equivalent for."""
    if browser_name != "chromium" and request.node.get_closest_marker(
        "desktop_only"
    ):
        pytest.skip(
            "desktop_only: needs the Chromium fake-media projection"
        )


@pytest.fixture(scope="session")
def base_url(request: pytest.FixtureRequest) -> str:
    # Precedence: an explicit URL wins (already a deliberate, explicit
    # choice of target — bypasses the live-tray guard entirely); then
    # autoboot's disposable server; then the guarded live-tray path.
    explicit = os.environ.get("VT_E2E_BASE_URL", "").strip()
    if explicit:
        return explicit
    if _autoboot_enabled(request.config):
        return request.getfixturevalue("_autoboot_server")

    # Vendored guard (issue #158): refuses via pytest.exit if the live
    # port is occupied and VT_E2E_LIVE isn't set. This repo's caller-side
    # choice on an opt-in hit is to *adopt* the live tray for read-only
    # smoke checks, never kill it — see the module docstring above.
    live_opt_in = require_disposable_instance(_LIVE_PORT, _LIVE_ENV)
    if live_opt_in and _wait_healthz(_DEFAULT_BASE_URL, timeout=2):
        logger.info("✅ %s=1 — adopting live tray at %s", _LIVE_ENV, _DEFAULT_BASE_URL)
        return _DEFAULT_BASE_URL
    return request.getfixturevalue("_autoboot_server")


@pytest.fixture(scope="session")
def webapp_config() -> dict:
    if not _WEBAPP_CONFIG.exists():
        pytest.skip(f"{_WEBAPP_CONFIG} missing — copy webapp_config.sample.json first")
    return json.loads(_WEBAPP_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def auth_token(webapp_config: dict) -> str:
    # Loopback bypasses the bearer middleware, so an empty token is fine
    # for these local tests. We still seed it when present so the SPA boot
    # path mirrors a real phone session.
    return (webapp_config.get("auth_token") or "").strip()


@pytest.fixture(scope="session")
def browser_context_args(
    browser_context_args: dict, browser_name: str, playwright
) -> dict:
    # Self-signed cert on 8443 — the SPA + service-worker won't load otherwise.
    args = {**browser_context_args, "ignore_https_errors": True}
    if browser_name == "webkit":
        # Project the WebKit engine onto an iPhone — viewport, user_agent,
        # has_touch, is_mobile, device_scale_factor — so the suite
        # exercises an iPhone-shaped target on Windows (issue #31).
        args = {**args, **playwright.devices[_IPHONE_DEVICE]}
    return args


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict, browser_name: str
) -> dict:
    # Chromium gets fake-media flags so the record-flow regression tests
    # can run a real MediaRecorder with no microphone, and so getUserMedia
    # is granted without a permission prompt.
    if browser_name == "chromium":
        return {
            **browser_type_launch_args,
            "args": [
                *browser_type_launch_args.get("args", []),
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ],
        }
    return browser_type_launch_args


def _seed_token_init_script(token: str) -> str:
    # Seeded *before* the first navigation so the SPA reads it on boot
    # rather than going through the ?token=… URL strip dance.
    safe = json.dumps(token)
    safe_key = json.dumps(_TOKEN_KEY)
    return f"window.localStorage.setItem({safe_key}, {safe});"


@pytest.fixture
def authed_page(
    context: BrowserContext, base_url: str, auth_token: str
) -> Iterator[Page]:
    if auth_token:
        context.add_init_script(_seed_token_init_script(auth_token))
    page = context.new_page()
    try:
        yield page
    finally:
        page.close()
