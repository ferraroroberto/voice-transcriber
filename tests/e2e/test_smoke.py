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
    """The complete Speak step is one card, followed by Transcript.

    We do NOT click Record — that would start real mic capture.
    """
    _navigate_collecting_errors(authed_page, base_url)
    speak_card = authed_page.locator(".speak-card")
    expect(speak_card).to_have_class("card speak-card")
    expect(speak_card.locator(".card-title")).to_contain_text("Speak")
    for control_id in ("appendToggle", "resetBtn", "incognitoToggle", "themeToggle"):
        expect(speak_card.locator(f"#{control_id}")).to_be_attached()

    record_btn = speak_card.locator("#recordBtn")
    expect(record_btn).to_be_visible()
    label = speak_card.locator("#recordLabel")
    expect(label).to_be_visible()
    expect(label).to_contain_text("RECORD")

    transcript_title = authed_page.locator(".transcript-card .card-title")
    expect(transcript_title).to_contain_text("Transcript")
    expect(transcript_title.locator("use")).to_have_attribute("href", "#i-file-text")


def test_settings_tab_activates(authed_page: Page, base_url: str) -> None:
    """Settings lives on its own tab (fleet nav contract, issue #107):
    hidden by default, revealed by activating the Settings tab, with the
    Record pane yielding. Catches a missing pane and broken nav wiring
    in one shot."""
    _navigate_collecting_errors(authed_page, base_url)
    panel = authed_page.locator("#settingsPanel")
    expect(panel).to_be_attached()
    expect(panel).to_be_hidden()
    authed_page.click("#tabSettings")
    expect(panel).to_be_visible()
    expect(authed_page.locator("#paneRecord")).to_be_hidden()
    expect(authed_page.locator("#tabSettings")).to_have_attribute(
        "aria-selected", "true"
    )


def test_login_dialog_dom_present(authed_page: Page, base_url: str) -> None:
    """The login gate is a native <dialog> wired for showModal().

    We exercise the DOM directly rather than triggering a real 401: the
    bearer middleware bypasses loopback, so a bad token from 127.0.0.1
    won't surface the dialog. This still catches the regression we care
    about — dialog element + password input missing or renamed — plus the
    auth-gate contract that Esc must not dismiss it.
    """
    _navigate_collecting_errors(authed_page, base_url)
    dialog = authed_page.locator("#loginOverlay")
    expect(dialog).to_be_hidden()
    authed_page.evaluate("document.getElementById('loginOverlay').showModal()")
    expect(dialog).to_be_visible()
    pw = authed_page.locator("#loginPassword")
    expect(pw).to_be_editable()
    pw.fill("dummy")
    expect(pw).to_have_value("dummy")
    # Esc is suppressed (api.js cancel handler): a closed gate != unlocked app.
    authed_page.keyboard.press("Escape")
    expect(dialog).to_be_visible()
    authed_page.evaluate("document.getElementById('loginOverlay').close()")
    expect(dialog).to_be_hidden()
