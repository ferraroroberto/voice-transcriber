"""Page header contract (issue #226, fleet NAV-03): Settings is never a tab.

Every pane opens with the vendored ``home-head`` row, carrying the theme
toggle and the Settings gear on every tab, and the nav lists only the real
destinations.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.smoke

_PANES = (("#paneRecord", "Record"), ("#paneHistory", "History"), ("#paneSettings", "Settings"))


def _open(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)


def test_settings_is_never_a_tab_and_every_pane_has_the_gear(
    authed_page: Page, base_url: str
) -> None:
    _open(authed_page, base_url)
    expect(authed_page.locator('.tabs .tab[data-tab="settings"]')).to_have_count(0)
    expect(authed_page.locator(".tabs .tab")).to_have_count(2)

    for pane, title in _PANES:
        head = authed_page.locator(f"{pane} > .card.home-head")
        expect(head).to_have_count(1)
        expect(head.locator(".home-title")).to_contain_text(title)
        expect(head.locator(".theme-toggle")).to_have_count(1)
        expect(head.locator(".home-settings")).to_have_count(1)


def test_gear_and_theme_toggle_work_from_any_tab(authed_page: Page, base_url: str) -> None:
    _open(authed_page, base_url)
    authed_page.click("#tabHistory")
    authed_page.click("#paneHistory .theme-toggle")
    first = authed_page.evaluate("document.documentElement.dataset.theme")
    authed_page.click("#paneHistory .theme-toggle")
    second = authed_page.evaluate("document.documentElement.dataset.theme")
    assert {first, second} == {"light", "dark"}, (first, second)

    authed_page.click("#paneHistory .home-settings")
    expect(authed_page.locator("#paneSettings")).to_be_visible()
    expect(authed_page.locator("#paneHistory")).to_be_hidden()
    authed_page.click("#tabHistory")
    expect(authed_page.locator("#paneSettings")).to_be_hidden()
    expect(authed_page.locator("#paneHistory")).to_be_visible()
