"""Build footer contract: the vendored ``page-foot`` ends every tab.

The footer is the last child of ``<main class="app">``, so it shows under
Record, History and Settings. On the phone pill the nav reserves a bottom
gap sized to clear its fade, so scrolled to the end the footer must sit
wholly above the fade's top edge (never dimmed) and above the pill.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.smoke

_PHONE_PILL = "window.matchMedia('(pointer: coarse) and (max-width: 520px)').matches"
# Mirrors the nav's --nav-fade-tail fallback: how far the bottom fade reaches above the pill.
_FADE_TAIL_PX = 24


def _open_tab(page: Page, tab: str) -> None:
    if tab == "settings":
        page.click("#paneRecord .home-settings")
        expect(page.locator("#paneSettings")).to_be_visible()
    else:
        page.click(f"#tab{tab.capitalize()}")
        expect(page.locator(f"#pane{tab.capitalize()}")).to_be_visible()


@pytest.mark.parametrize("tab", ["record", "history", "settings"])
def test_build_footer_is_visible_and_unfaded_at_scroll_end(
    authed_page: Page, base_url: str, tab: str
) -> None:
    page = authed_page
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)
    if tab != "record":
        _open_tab(page, tab)

    foot = page.locator(".page-foot")
    readout = page.locator("#buildReadout")
    expect(readout).to_contain_text("Build: ")
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    expect(foot).to_be_in_viewport()

    box = foot.bounding_box()
    assert box is not None
    if page.evaluate(_PHONE_PILL):
        pill = page.locator(".tabs").bounding_box()
        assert pill is not None
        fade_top = pill["y"] - _FADE_TAIL_PX
        assert box["y"] + box["height"] <= fade_top, (
            f"{tab}: footer ends at {box['y'] + box['height']:.0f}px, "
            f"inside the bottom fade that starts at {fade_top:.0f}px"
        )
