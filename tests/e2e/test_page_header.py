"""Page header contract (fleet NAV-03): Settings is never a tab.

Every pane opens with the vendored ``home-head`` row, carrying the theme
toggle and the Settings gear on every tab, and the nav lists only the real
destinations. The vendored ``page-foot`` ends every tab, and each pane's
context line names only exceptions.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._surface import surface

pytestmark = pytest.mark.smoke

_PANES = (("#paneRecord", "Record"), ("#paneHistory", "History"), ("#paneSettings", "Settings"))
_PHONE_PILL = "window.matchMedia('(pointer: coarse) and (max-width: 520px)').matches"
# Mirrors the nav's --nav-fade-tail fallback: how far the bottom fade reaches above the pill.
_FADE_TAIL_PX = 24


def _open(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)


def _assert_footer_clear_of_the_fade(page: Page, where: str) -> None:
    """Scrolled to the end, the footer sits wholly above the pill's fade."""
    foot = page.locator(".page-foot")
    expect(page.locator("#buildReadout")).to_contain_text("Build: ")
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    expect(foot).to_be_in_viewport()
    box = foot.bounding_box()
    assert box is not None
    if page.evaluate(_PHONE_PILL):
        pill = page.locator(".tabs").bounding_box()
        assert pill is not None
        fade_top = pill["y"] - _FADE_TAIL_PX
        assert box["y"] + box["height"] <= fade_top, (
            f"{where}: footer ends at {box['y'] + box['height']:.0f}px, "
            f"inside the bottom fade that starts at {fade_top:.0f}px"
        )


def test_every_pane_has_the_header_and_footer_and_settings_is_the_gear(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    _open(page, base_url)
    expect(page.locator('.tabs .tab[data-tab="settings"]')).to_have_count(0)
    expect(page.locator(".tabs .tab")).to_have_count(2)
    for pane, title in _PANES:
        head = page.locator(f"{pane} > .card.home-head")
        expect(head).to_have_count(1)
        expect(head.locator(".home-title")).to_contain_text(title)
        expect(head.locator(".theme-toggle")).to_have_count(1)
        expect(head.locator(".home-settings")).to_have_count(1)

    # Record -> Settings: the gear reveals the panel and the Record pane yields.
    panel = page.locator("#settingsPanel")
    expect(panel).to_be_attached()
    expect(panel).to_be_hidden()
    _assert_footer_clear_of_the_fade(page, "record")
    page.click("#paneRecord .home-settings")
    expect(panel).to_be_visible()
    expect(page.locator("#paneRecord")).to_be_hidden()
    _assert_footer_clear_of_the_fade(page, "settings")
    surface(page, "settings.pane")
    page.click("#tabRecord")
    expect(panel).to_be_hidden()
    expect(page.locator("#paneRecord")).to_be_visible()

    # History: the theme toggle flips the theme and the gear still reaches
    # Settings from here; choosing a tab leaves Settings again.
    page.click("#tabHistory")
    _assert_footer_clear_of_the_fade(page, "history")
    page.click("#paneHistory .theme-toggle")
    first = page.evaluate("document.documentElement.dataset.theme")
    page.click("#paneHistory .theme-toggle")
    second = page.evaluate("document.documentElement.dataset.theme")
    assert {first, second} == {"light", "dark"}, (first, second)
    page.click("#paneHistory .home-settings")
    expect(page.locator("#paneSettings")).to_be_visible()
    expect(page.locator("#paneHistory")).to_be_hidden()
    page.click("#tabHistory")
    expect(page.locator("#paneSettings")).to_be_hidden()
    expect(page.locator("#paneHistory")).to_be_visible()


def _assert_one_row(page: Page, pane: str) -> None:
    box = page.locator(f"{pane} > .card.home-head").bounding_box()
    assert box is not None and box["height"] <= 52.5, (pane, box)


_UP = {"whisper": {"running": True}, "llm_hub": {"reachable": True}, "ffmpeg_present": True}
_DOWN = {"whisper": {"running": False}, "llm_hub": {"reachable": False}, "ffmpeg_present": True}


def test_header_line_names_only_exceptions_and_never_wraps(
    authed_page: Page, base_url: str
) -> None:
    """Every pane's context line (#261): a plain fact by default, one
    exception in its tone while it applies, and back again when it clears.
    The header row stays one 52px line whatever the line says."""
    status = {"reply": _UP}
    authed_page.route("**/api/status", lambda route: route.fulfill(json=status["reply"]))
    _open(authed_page, base_url)
    line = authed_page.locator("#recordHeadLine")
    authed_page.wait_for_function("document.getElementById('languageSelect').options.length > 0")
    language = authed_page.evaluate(
        "document.getElementById('languageSelect').selectedOptions[0].textContent"
    )
    expect(line).to_have_text(language)
    assert line.get_attribute("data-tone") is None

    authed_page.click("#incognitoToggle")
    expect(line).to_have_text("Incognito: not saved")
    expect(line).to_have_attribute("data-tone", "neutral")
    _assert_one_row(authed_page, "#paneRecord")
    # Not clipped to an ellipsis (#275): the text fits its box at phone width.
    assert line.evaluate("el => el.scrollWidth <= el.clientWidth")
    authed_page.click("#incognitoToggle")
    expect(line).to_have_text(language)

    authed_page.click("#paneRecord .home-settings")
    expect(authed_page.locator("#settingsHeadLine")).to_have_text("All services up")
    authed_page.click("#translateToggle")
    authed_page.click("#tabRecord")
    expect(line).to_have_text(f"{language} to English")
    expect(line).to_have_attribute("data-tone", "attention")
    _assert_one_row(authed_page, "#paneRecord")
    assert line.evaluate("el => el.scrollWidth <= el.clientWidth")

    status["reply"] = _DOWN
    authed_page.click("#paneRecord .home-settings")
    settings_line = authed_page.locator("#settingsHeadLine")
    expect(settings_line).to_have_text("Transcription offline")
    expect(settings_line).to_have_attribute("data-tone", "danger")
    _assert_one_row(authed_page, "#paneSettings")
    authed_page.click("#tabRecord")
    expect(line).to_have_text("Transcription offline")
    expect(line).to_have_attribute("data-tone", "danger")
