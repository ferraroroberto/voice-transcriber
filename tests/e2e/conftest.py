"""Fixtures for the voice-transcriber Playwright smoke suite.

Same pattern as app-launcher/tests/e2e/conftest.py: run against the live
tray on https://127.0.0.1:8443 instead of booting our own server. The
autouse ``_require_live_tray`` fixture skips the whole module with a
clear message if /healthz isn't reachable, so a forgotten tray fails
fast instead of hanging in browser.goto.
"""

from __future__ import annotations

import json
import logging
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
_BASE_URL = "https://127.0.0.1:8443"
_TOKEN_KEY = "vt_auth_token"  # must match TOKEN_KEY in app/webapp/static/app.js


@pytest.fixture(scope="session")
def base_url() -> str:
    return _BASE_URL


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
            f"Tray not running on 8443 ({exc.__class__.__name__}) — "
            "start tray.bat first, then re-run the suite."
        )


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    # Self-signed cert on 8443 — the SPA + service-worker won't load otherwise.
    return {**browser_context_args, "ignore_https_errors": True}


def _seed_token_init_script(token: str) -> str:
    # Seeded *before* the first navigation so app.js reads it on boot
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
