"""The main page keeps the fleet's request budget on a real browser.

The probe (`tests/e2e/budget/_request_budget.py`) loads the page on a fake
clock and counts API requests: a visible minute within the `.fleet.toml`
budget, a hidden minute at zero, and at most one catch-up read per path when
the tab returns. Chromium only: the clock and the visibility switch are engine
independent, so a second projection would repeat the same count.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from tests.e2e.budget._request_budget import assert_request_budget, measure_request_budget, visible_budget

REPO_ROOT = Path(__file__).resolve().parents[2]

# The one request a hidden tab makes: the vendored usage log posts the taps it
# holds plus a session_end the moment the tab hides, so the visit's last taps
# are not lost. It fires once on the transition, never on a timer, so a second
# one (or any other path) during the hidden minute is still a breach.
_HIDE_FLUSH = "/api/usage/events"


@pytest.mark.smoke
def test_main_page_keeps_its_request_budget(authed_page: Page, base_url: str, browser_name: str) -> None:
    if browser_name != "chromium":
        pytest.skip("the request count is engine independent: measured once, on Chromium")
    reading = measure_request_budget(authed_page, f"{base_url}/")
    assert reading.hidden[_HIDE_FLUSH] <= 1, reading.summary()
    del reading.hidden[_HIDE_FLUSH]
    assert_request_budget(reading, visible_budget(REPO_ROOT))
