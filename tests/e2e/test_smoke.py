"""Smoke tests for the voice-transcriber webapp.

Tight by design: ~5 checks that catch the bugs we actually hit on the
SPA (JS exceptions on boot, empty select dropdowns, broken settings
panel, missing login overlay). Expand iteratively if regressions slip
through — do NOT turn this file into a regression net for every feature.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.smoke


def _navigate_collecting_errors(page: Page, base_url: str) -> list[str]:
    """Open the SPA and capture any uncaught JS errors during boot."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    # #recordBtn is rendered server-side in index.html; waiting for it
    # confirms the static document parsed without an early script crash.
    page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)
    return errors


def test_page_loads_without_console_errors(authed_page: Page, base_url: str) -> None:
    errors = _navigate_collecting_errors(authed_page, base_url)
    # Give the boot script a beat to settle: fetchConfig, history poll,
    # mic enumeration. Anything thrown during that fans out as pageerror.
    authed_page.wait_for_timeout(500)
    assert errors == [], "JS errors during boot:\n  - " + "\n  - ".join(errors)


def test_polish_options_populated(authed_page: Page, base_url: str) -> None:
    _navigate_collecting_errors(authed_page, base_url)
    # renderPolishOptions runs after /api/config resolves. Both selects
    # must end up with at least one <option> or the dropdowns are empty.
    # state="attached" not "visible" — <option> inside a collapsed <select>
    # has no layout box so the default visible state never resolves.
    authed_page.wait_for_selector("#polishModel option", state="attached", timeout=5_000)
    authed_page.wait_for_selector("#polishStyle option", state="attached", timeout=5_000)
    model_count = authed_page.locator("#polishModel option").count()
    style_count = authed_page.locator("#polishStyle option").count()
    assert model_count >= 1, f"#polishModel rendered no options (got {model_count})"
    assert style_count >= 1, f"#polishStyle rendered no options (got {style_count})"


def test_record_zone_renders(authed_page: Page, base_url: str) -> None:
    """The record button is the headline UI; if it's gone, the app is
    useless. We do NOT click it — that would start real mic capture."""
    _navigate_collecting_errors(authed_page, base_url)
    record_btn = authed_page.locator("#recordBtn")
    expect(record_btn).to_be_visible()
    label = authed_page.locator("#recordLabel")
    expect(label).to_be_visible()
    expect(label).to_contain_text("RECORD")


def test_settings_panel_toggles(authed_page: Page, base_url: str) -> None:
    """The settings panel is a <details> element collapsed by default;
    clicking the summary expands it. Catches both a missing element and
    a broken summary handler in one shot."""
    _navigate_collecting_errors(authed_page, base_url)
    panel = authed_page.locator("#settingsPanel")
    expect(panel).to_be_attached()
    # <details> exposes its open state as a boolean attribute.
    assert panel.evaluate("el => el.open") is False
    panel.locator("summary").first.click()
    expect(panel).to_have_attribute("open", "")


def test_login_overlay_dom_present(authed_page: Page, base_url: str) -> None:
    """The login overlay markup is wired so showLogin() can reveal it.

    We exercise the DOM directly rather than triggering a real 401: the
    bearer middleware bypasses loopback, so a bad token from 127.0.0.1
    won't surface the overlay. This still catches the regression we care
    about — overlay element + password input missing or renamed.
    """
    _navigate_collecting_errors(authed_page, base_url)
    overlay = authed_page.locator("#loginOverlay")
    expect(overlay).to_be_hidden()
    authed_page.evaluate(
        "document.getElementById('loginOverlay').hidden = false"
    )
    expect(overlay).to_be_visible()
    pw = authed_page.locator("#loginPassword")
    expect(pw).to_be_editable()
    pw.fill("dummy")
    expect(pw).to_have_value("dummy")
