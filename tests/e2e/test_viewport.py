"""Sanity-check that the WebKit projection actually applies the iPhone descriptor.

Confirms ``browser_context_args`` in conftest.py merged in
``playwright.devices["iPhone 15 Pro Max"]`` — without this, the WebKit
run would silently use a desktop viewport and the smoke suite wouldn't
be exercising an iPhone-shaped target at all (issue #31).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.smoke

# Matches playwright.devices["iPhone 15 Pro Max"]["viewport"]["width"].
_IPHONE_15_PRO_MAX_WIDTH = 430


def test_iphone_viewport_active_on_webkit(
    authed_page: Page, base_url: str, browser_name: str
) -> None:
    if browser_name != "webkit":
        pytest.skip("iPhone projection only applies to the WebKit browser")
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    width = authed_page.evaluate("window.innerWidth")
    assert width == _IPHONE_15_PRO_MAX_WIDTH, (
        f"expected the iPhone 15 Pro Max viewport width "
        f"{_IPHONE_15_PRO_MAX_WIDTH}, got {width} — the WebKit projection "
        "did not apply the device descriptor"
    )
