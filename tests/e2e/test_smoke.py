"""Smoke tests for the voice-transcriber webapp.

Tight by design: ~5 checks that catch the bugs we actually hit on the
SPA (JS exceptions on boot, empty select dropdowns, broken settings
panel, missing login overlay). Expand iteratively if regressions slip
through — do NOT turn this file into a regression net for every feature.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._surface import surface

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
    """The recorder is one untitled card (#262): an accent "Record" circle in
    sentence case, then Append and Incognito as labelled switch pills (no
    glyph-only toggle). Reset is the Text card's Clear since #263.

    We do NOT click Record — that would start real mic capture.
    """
    _navigate_collecting_errors(authed_page, base_url)
    recorder = authed_page.locator(".recorder-card")
    expect(recorder).to_have_class("card recorder-card")
    expect(recorder.locator(".card-title")).to_have_count(0)
    for control_id, text in (("appendToggle", "Append"), ("incognitoToggle", "Incognito")):
        expect(recorder.locator(f".record-modes #{control_id}")).to_have_text(text)
    for switch_id in ("appendToggle", "incognitoToggle"):
        expect(recorder.locator(f"#{switch_id}")).to_have_attribute("role", "switch")

    record_btn = recorder.locator("#recordBtn")
    expect(record_btn).to_be_visible()
    expect(recorder.locator("#recordLabel")).to_have_text("Record")
    fill, accent = authed_page.evaluate("""() => {
        const probe = document.createElement('div');
        probe.style.background = 'var(--accent-fill)';
        document.body.appendChild(probe);
        const want = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return [getComputedStyle(document.getElementById('recordBtn')).backgroundColor, want];
    }""")
    assert fill == accent, (fill, accent)
    expect(authed_page.locator("#modelRoute")).to_have_count(0)
    surface(authed_page, "record.idle")


def test_text_card_holds_both_versions(authed_page: Page, base_url: str) -> None:
    """One Text card (#263): no Polish card; the verbs appear with text;
    Save only for text History doesn't hold; Polish is one tap away and the
    Polished segment exists only once a polish does; Copy follows the view.
    """
    page = authed_page
    page.route("**/api/status", lambda route: route.fulfill(json={
        "whisper": {"running": True}, "llm_hub": {"reachable": True}, "ffmpeg_present": True,
    }))
    page.route("**/api/polish-text", lambda route: route.fulfill(json={
        "session_id": "text-1", "polished": "A tidy invented sentence.",
    }))
    page.route("**/api/sessions?*", lambda route: route.fulfill(json={
        "sessions": [], "total": 0, "has_more": False,
    }))
    _navigate_collecting_errors(page, base_url)
    expect(page.locator(".text-card")).to_have_count(1)
    expect(page.locator(".polish-card")).to_have_count(0)
    title = page.locator(".text-card .card-title")
    expect(title).to_have_text("Text")
    expect(title.locator("use")).to_have_attribute("href", "#i-file-text")
    expect(page.locator("#textVerbs")).to_be_hidden()
    expect(page.locator("#textView")).to_be_hidden()

    page.fill("#transcript", "uh so an invented sentence")
    expect(page.locator("#textVerbs")).to_be_visible()
    expect(page.locator("#saveTranscript")).to_be_visible()
    expect(page.locator("#textMeta")).to_have_text("5 words")
    surface(page, "record.text")

    page.click("#polishBtn")
    expect(page.locator("#textView")).to_be_visible()
    expect(page.locator("#polished")).to_be_visible()
    expect(page.locator("#polished")).to_have_value("A tidy invented sentence.")
    expect(page.locator("#transcript")).to_be_hidden()
    expect(page.locator("#saveTranscript")).to_be_hidden()  # the polish made a session
    expect(page.locator("#polishBtn")).to_have_text("Polish again")
    copied = page.evaluate("""() => {
        const shown = document.querySelector('#textView [aria-pressed="true"]').dataset.view;
        return shown;
    }""")
    assert copied == "polished"
    surface(page, "record.text-polished")

    page.click('#textView .segmented-item[data-view="transcript"]')
    expect(page.locator("#transcript")).to_have_value("uh so an invented sentence")
    expect(page.locator("#polished")).to_be_hidden()

    page.click("#resetBtn")
    expect(page.locator("#transcript")).to_have_value("")
    expect(page.locator("#textVerbs")).to_be_hidden()
    expect(page.locator("#textView")).to_be_hidden()


def test_settings_gear_opens_settings(authed_page: Page, base_url: str) -> None:
    """Settings is the header gear, never a tab (fleet NAV-03, issue #226):
    hidden by default, revealed by the gear, with the Record pane yielding;
    choosing a tab leaves it again. Catches a missing pane and broken gear
    or nav wiring in one shot."""
    _navigate_collecting_errors(authed_page, base_url)
    panel = authed_page.locator("#settingsPanel")
    expect(panel).to_be_attached()
    expect(panel).to_be_hidden()
    authed_page.click("#paneRecord .home-settings")
    expect(panel).to_be_visible()
    expect(authed_page.locator("#paneRecord")).to_be_hidden()
    surface(authed_page, "settings.pane")
    authed_page.click("#tabRecord")
    expect(panel).to_be_hidden()
    expect(authed_page.locator("#paneRecord")).to_be_visible()


def test_settings_save_as_they_change(authed_page: Page, base_url: str) -> None:
    """Settings (#265): inset groups with no Save button; each control posts
    only its own field the moment it changes, and a refused save puts the
    control back with an error toast. Delete all takes is a sheet, Services
    shows the probes and About the build (the per-tab footer is gone)."""
    import json

    page = authed_page
    posted: list[dict] = []

    def _config(route):
        if route.request.method != "POST":
            route.continue_()
            return
        body = json.loads(route.request.post_data or "{}")
        posted.append(body)
        if "history_retention_days" in body:
            route.fulfill(status=400, json={"detail": "invented refusal"})
        else:
            route.fulfill(json={"ok": True, "config": body})

    page.route("**/api/config", _config)
    page.route("**/api/status", lambda route: route.fulfill(json={
        "whisper": {"running": False}, "llm_hub": {"reachable": True}, "ffmpeg_present": True,
    }))
    _navigate_collecting_errors(page, base_url)
    page.click("#paneRecord .home-settings")
    expect(page.locator("#saveSettings")).to_have_count(0)
    expect(page.locator("#settingsPanel .settings-group-title")).to_have_count(7)

    page.click("#gainBoostToggle")
    page.wait_for_timeout(300)
    assert len(posted) == 1 and set(posted[0]) == {"gain_boost_enabled"}, posted

    retention = page.locator("#retentionDays")
    before = retention.input_value()
    retention.fill("7")
    retention.press("Enter")
    retention.blur()
    expect(page.locator("#toast")).to_contain_text("Couldn't save")
    expect(retention).to_have_value(before)

    expect(page.locator("#svcFallbackValue")).to_have_text("Not running")
    expect(page.locator("#svcFallback")).to_have_attribute("data-badge", "down")
    expect(page.locator("#svcPolish")).to_have_attribute("data-badge", "alive")
    expect(page.locator("#settingsHeadLine")).to_have_text("Local fallback down")
    expect(page.locator("#buildInfo")).not_to_have_text("…")
    expect(page.locator(".page-foot")).to_have_count(0)

    deletes: list[str] = []
    page.on("request", lambda r: deletes.append(r.url) if r.method == "DELETE" else None)
    page.click("#cleanAll")
    sheet = page.locator("#deleteAllSheet")
    expect(sheet).to_be_visible()
    surface(page, "settings.delete-all-sheet")
    sheet.locator(".detail-close").click()
    expect(sheet).to_be_hidden()
    assert deletes == []


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
    surface(authed_page, "login.dialog")
    # Esc is suppressed (api.js cancel handler): a closed gate != unlocked app.
    authed_page.keyboard.press("Escape")
    expect(dialog).to_be_visible()
    authed_page.evaluate("document.getElementById('loginOverlay').close()")
    expect(dialog).to_be_hidden()


def test_polish_prompt_sheet_opens_from_settings(authed_page: Page, base_url: str) -> None:
    """Settings › Polish › Instructions shows the prompt a Polish sends in a
    sheet (#265): it names the chosen style and closes from its x."""
    _navigate_collecting_errors(authed_page, base_url)
    authed_page.wait_for_selector("#polishStyle option", state="attached", timeout=5_000)
    authed_page.click("#paneRecord .home-settings")
    authed_page.click("#openPromptSheet")
    sheet = authed_page.locator("#promptSheet")
    expect(sheet).to_be_visible()
    expect(authed_page.locator("#promptSheetNote")).to_contain_text("Style:")
    surface(authed_page, "settings.prompt-sheet")
    sheet.locator(".detail-close").click()
    expect(sheet).to_be_hidden()
