"""Fixtures for the voice-transcriber Playwright suite.

Two engine projections, both always on (issue #31):

* **Chromium-desktop** — the fast default projection.
* **WebKit + iPhone** — ``browser_context_args`` merges the
  ``iPhone 15 Pro Max`` device descriptor so the suite exercises an
  iPhone-shaped Mobile-Safari-engine target on Windows. WebKit is the
  same engine family as iOS Safari, so it catches the large majority of
  "Safari is unhappy" regressions before they reach a phone.

Both run against the live tray on https://127.0.0.1:8443 by default; set
``VT_E2E_BASE_URL`` to point the suite at any other instance (a throwaway
server, a staging tunnel). The autouse ``_require_live_tray`` fixture
skips the whole module with a clear message if /healthz isn't reachable.

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
import urllib3
from pathlib import Path
from typing import Iterator

import pytest
import requests
from playwright.sync_api import BrowserContext, Page

logger = logging.getLogger(__name__)

# Self-signed cert on 8443 — silence the urllib3 noise from /healthz.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEBAPP_CONFIG = _REPO_ROOT / "config" / "webapp_config.json"
_DEFAULT_BASE_URL = "https://127.0.0.1:8443"
_TOKEN_KEY = "vt_auth_token"  # must match TOKEN_KEY in app/webapp/static/state.js

# iPhone 15 Pro Max — the descriptor merged into the WebKit projection.
_IPHONE_DEVICE = "iPhone 15 Pro Max"


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
def base_url() -> str:
    return os.environ.get("VT_E2E_BASE_URL", "").strip() or _DEFAULT_BASE_URL


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


@pytest.fixture(scope="session", autouse=True)
def _require_live_tray(base_url: str) -> None:
    try:
        res = requests.get(f"{base_url}/healthz", timeout=2, verify=False)
        res.raise_for_status()
    except Exception as exc:
        pytest.skip(
            f"No webapp answering /healthz at {base_url} "
            f"({exc.__class__.__name__}) — start tray.bat (or set "
            "VT_E2E_BASE_URL), then re-run the suite."
        )


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
