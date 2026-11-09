"""Smoke tests for the voice-transcriber webapp.

One test per user-visible flow of the SPA's first screens: boot and the Record
pane's rendered contracts, dictating and polishing text, changing a setting,
and signing in. A new check joins the flow it belongs to rather than getting a
test of its own.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import assert_toast_paint
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


_FAMILY_JS = "(sel) => getComputedStyle(document.querySelector(sel)).fontFamily"
_BORDER_JS = "(sel) => getComputedStyle(document.querySelector(sel)).borderTopColor"
# The token's own resolved colour, via a throwaway probe so the check follows
# the stylesheet instead of hard-coding a hex.
_CONTROL_BORDER_JS = """() => {
  const probe = document.createElement('div');
  probe.style.borderTop = '1px solid var(--control-border)';
  document.body.appendChild(probe);
  const color = getComputedStyle(probe).borderTopColor;
  probe.remove();
  return color;
}"""
_PAINT_JS = """els => els.map(el => {
    const s = getComputedStyle(el);
    return [s.backgroundColor, s.borderTopWidth];
})"""


def test_record_pane_boots_and_renders_its_contracts(
    authed_page: Page, base_url: str
) -> None:
    """The recorder is one untitled card (#262): an accent "Record" circle in
    sentence case, then Append and Incognito as labelled switch pills (no
    glyph-only toggle). Reset is the Text card's Clear since #263.

    The page boots without a script error, with both Polish dropdowns filled
    and no sign-in sheet for a device the server accepts, and the pane draws
    to the design contracts: one type face, 3:1 control borders in both
    themes, glyph-only header toggles. We do NOT click Record: that would
    start real mic capture.
    """
    page = authed_page
    errors = _navigate_collecting_errors(page, base_url)

    # renderPolishOptions runs after /api/config resolves. Both selects must
    # end up with an <option> or the dropdowns are empty. state="attached" not
    # "visible": an <option> in a collapsed <select> has no layout box.
    page.wait_for_selector("#polishModel option", state="attached", timeout=5_000)
    page.wait_for_selector("#polishStyle option", state="attached", timeout=5_000)
    # Give the boot script a beat to settle (fetchConfig, history poll, mic
    # enumeration): anything thrown during that fans out as pageerror.
    page.wait_for_timeout(500)
    assert errors == [], "JS errors during boot:\n  - " + "\n  - ".join(errors)
    expect(page.locator("dialog.sign-in")).to_have_count(0)

    recorder = page.locator(".recorder-card")
    expect(recorder).to_have_class("card recorder-card")
    expect(recorder.locator(".card-title")).to_have_count(0)
    for control_id, text in (("appendToggle", "Append"), ("incognitoToggle", "Incognito")):
        expect(recorder.locator(f".record-modes #{control_id}")).to_have_text(text)
        expect(recorder.locator(f"#{control_id}")).to_have_attribute("role", "switch")
    expect(recorder.locator("#recordBtn")).to_be_visible()
    expect(recorder.locator("#recordLabel")).to_have_text("Record")
    fill, accent = page.evaluate("""() => {
        const probe = document.createElement('div');
        probe.style.background = 'var(--accent-fill)';
        document.body.appendChild(probe);
        const want = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return [getComputedStyle(document.getElementById('recordBtn')).backgroundColor, want];
    }""")
    assert fill == accent, (fill, accent)
    expect(page.locator("#modelRoute")).to_have_count(0)

    # Form controls don't inherit the page font by themselves; the vendored
    # base layer pulls the body's face onto them.
    body_family = page.evaluate(_FAMILY_JS, "body")
    assert "system-ui" in body_family, f"body lost the fleet font stack: {body_family!r}"
    for selector in (".tabs .tab", "#recordBtn", "#gainBoostDb", "#polishModel", "#polishPromptPreview"):
        family = page.evaluate(_FAMILY_JS, selector)
        assert family == body_family, f"{selector} renders in {family!r}, body in {body_family!r}"

    # The header toggles and a dialog's close are .icon-button's: a glyph on
    # nothing at rest (project-scaffolding#339).
    toggles = page.locator("#paneRecord > .card.home-head .home-toggle")
    closes = page.locator(".detail-close")
    assert toggles.count() == 2 and closes.count() >= 1
    for paint in toggles.evaluate_all(_PAINT_JS) + closes.evaluate_all(_PAINT_JS):
        assert paint == ["rgba(0, 0, 0, 0)", "0px"]

    # WCAG 1.4.11: the icon-only incognito chip and the text areas draw their
    # edge in --control-border, whichever theme is up. Both themes must really
    # resolve to different tokens, or the second pass proves nothing.
    # Transitions are off so a colour still easing toward the new theme cannot
    # read as the old one.
    page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")
    tokens = set()
    for scheme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", scheme)
        token = page.evaluate(_CONTROL_BORDER_JS)
        tokens.add(token)
        for selector in ("#incognitoToggle", "#transcript", "#polished", "#polishPromptPreview"):
            border = page.evaluate(_BORDER_JS, selector)
            assert border == token, (
                f"{selector} ({scheme}) borders in {border!r}, control-border is {token!r}"
            )
    assert len(tokens) == 2, f"the themes resolved one control-border token: {tokens}"
    page.evaluate("() => { delete document.documentElement.dataset.theme; }")
    surface(page, "record.idle")


def test_text_card_holds_both_versions(authed_page: Page, base_url: str) -> None:
    """One Text card (#263): no Polish card; the verbs appear with text;
    Save only for text History doesn't hold; Polish is one tap away and the
    Polished segment exists only once a polish does; Copy follows the view.
    Polish's success toast is neutral: only an error tints.
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
    expect(page.locator("#toast")).to_contain_text("Polish done")
    assert_toast_paint(page, error=False)
    expect(page.locator("#textView")).to_be_visible()
    expect(page.locator("#polished")).to_be_visible()
    expect(page.locator("#polished")).to_have_value("A tidy invented sentence.")
    expect(page.locator("#transcript")).to_be_hidden()
    expect(page.locator("#saveTranscript")).to_be_hidden()  # the polish made a session
    expect(page.locator("#polishBtn")).to_have_text("Polish again")
    shown = page.evaluate(
        "document.querySelector('#textView [aria-pressed=\"true\"]').dataset.view"
    )
    assert shown == "polished"
    surface(page, "record.text-polished")

    page.click('#textView .segmented-item[data-view="transcript"]')
    expect(page.locator("#transcript")).to_have_value("uh so an invented sentence")
    expect(page.locator("#polished")).to_be_hidden()

    page.click("#resetBtn")
    expect(page.locator("#transcript")).to_have_value("")
    expect(page.locator("#textVerbs")).to_be_hidden()
    expect(page.locator("#textView")).to_be_hidden()


# step -> root font-size in px (93.75% / 100% / 112.5% of the 16px default)
_TEXT_STEPS = {"small": 15.0, "default": 16.0, "large": 18.0}
_ROOT_PX = "parseFloat(getComputedStyle(document.documentElement).fontSize)"
_BODY_PX = "parseFloat(getComputedStyle(document.body).fontSize)"
_TITLE_PX = "parseFloat(getComputedStyle(document.querySelector('#paneSettings .home-title')).fontSize)"


def test_settings_save_as_they_change(authed_page: Page, base_url: str) -> None:
    """Settings (#265): inset groups with no Save button; each control posts
    only its own field the moment it changes, and a refused save puts the
    control back with an error toast. Delete all takes and the Polish prompt
    are sheets, Services shows the probes and About the build (also the page
    footer's readout). The text size scales the root font and survives a
    reload (fleet text-size setting)."""
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
    page.wait_for_selector("#polishStyle option", state="attached", timeout=5_000)
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
    assert_toast_paint(page, error=True)
    expect(retention).to_have_value(before)

    expect(page.locator("#svcFallbackValue")).to_have_text("Not running")
    expect(page.locator("#svcFallback")).to_have_attribute("data-badge", "down")
    expect(page.locator("#svcPolish")).to_have_attribute("data-badge", "alive")
    expect(page.locator("#settingsHeadLine")).to_have_text("Local fallback down")
    expect(page.locator("#buildInfo")).not_to_have_text("…")
    expect(page.locator("#buildReadout")).to_contain_text("Build: ")

    deletes: list[str] = []
    page.on("request", lambda r: deletes.append(r.url) if r.method == "DELETE" else None)
    page.click("#cleanAll")
    sheet = page.locator("#deleteAllSheet")
    expect(sheet).to_be_visible()
    surface(page, "settings.delete-all-sheet")
    sheet.locator(".detail-close").click()
    expect(sheet).to_be_hidden()
    assert deletes == []

    # Settings › Polish › Instructions shows the prompt a Polish sends in a
    # sheet: it names the chosen style and closes from its x.
    page.click("#openPromptSheet")
    prompt = page.locator("#promptSheet")
    expect(prompt).to_be_visible()
    expect(page.locator("#promptSheetNote")).to_contain_text("Style:")
    surface(page, "settings.prompt-sheet")
    prompt.locator(".detail-close").click()
    expect(prompt).to_be_hidden()

    # Every rem-based type role scales with the root: body text and a heading.
    for step, px in _TEXT_STEPS.items():
        page.click(f'#textSizeControl [data-textsize="{step}"]')
        assert page.evaluate("document.documentElement.dataset.textsize") == step
        assert page.evaluate(_ROOT_PX) == px, step
        assert page.evaluate(_BODY_PX) == px, step
        assert page.evaluate(_TITLE_PX) == px, step
        expect(
            page.locator(f'#textSizeControl [data-textsize="{step}"]')
        ).to_have_attribute("aria-pressed", "true")
    page.click('#textSizeControl [data-textsize="large"]')
    assert page.evaluate("localStorage.getItem('voice-transcriber.textsize')") == "large"
    page.reload(wait_until="commit")
    # The inline head script stamps the attribute as the document is parsed,
    # so it is already there at domcontentloaded, well before any module runs.
    page.wait_for_load_state("domcontentloaded")
    assert page.evaluate("document.documentElement.dataset.textsize") == "large"
    assert page.evaluate(_ROOT_PX) == _TEXT_STEPS["large"]


def test_sign_in_sheet_wrong_then_correct_password(
    authed_page: Page, base_url: str
) -> None:
    """A 401 from /api/config opens the vendored `dialog.sign-in`; a wrong
    password keeps it open, the right one closes it and writes this app's
    existing token key.

    The bearer middleware bypasses loopback, so a real 401 cannot be provoked
    from 127.0.0.1: /api/login and the first /api/config are stubbed in the
    browser instead. The sheet is reached through the app's own 401 path.
    """
    good_token = "tok-e2e-good"

    def config_route(route) -> None:
        if route.request.headers.get("authorization") == f"Bearer {good_token}":
            route.continue_()
        else:
            route.fulfill(status=401, json={"detail": "unauthorized"})

    def login_route(route) -> None:
        if '"right"' in (route.request.post_data or ""):
            route.fulfill(status=200, json={"token": good_token})
        else:
            route.fulfill(status=401, json={"detail": "bad password"})

    authed_page.route("**/api/config", config_route)
    authed_page.route("**/api/login", login_route)
    _navigate_collecting_errors(authed_page, base_url)

    sheet = authed_page.locator("dialog.sign-in")
    expect(sheet).to_be_visible()
    pw = sheet.locator("input[type=password]")
    expect(pw).to_be_focused()
    # Esc must not dismiss it: a closed gate is not an unlocked app.
    authed_page.keyboard.press("Escape")
    expect(sheet).to_be_visible()

    pw.fill("wrong")
    sheet.locator("button[type=submit]").click()
    expect(sheet.locator(".sign-in-error")).to_have_text("Wrong password")
    expect(sheet).to_be_visible()
    assert authed_page.evaluate("localStorage.getItem('vt_auth_token')") != good_token

    pw.fill("right")
    surface(authed_page, "login.dialog")
    sheet.locator("button[type=submit]").click()
    expect(sheet).to_be_hidden()
    assert authed_page.evaluate("localStorage.getItem('vt_auth_token')") == good_token
