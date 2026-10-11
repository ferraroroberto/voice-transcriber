"""Viewport projections: the WebKit run is an iPhone, the Chromium run a
desktop window.

On WebKit, confirms ``browser_context_args`` in conftest.py merged in
``playwright.devices["iPhone 15 Pro Max"]`` — without this, the WebKit
run would silently use a desktop viewport and the smoke suite wouldn't
be exercising an iPhone-shaped target at all (issue #31).

On Chromium (a 1280px fine-pointer window), checks the wide layout (#266):
the nav is the left rail, Record puts the recorder beside its text,
History shows the picked take beside the list, and below the breakpoint
the panes stack again.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.smoke

# Matches playwright.devices["iPhone 15 Pro Max"]["viewport"]["width"].
_IPHONE_15_PRO_MAX_WIDTH = 430


def _box(page: Page, selector: str) -> dict:
    box = page.locator(selector).bounding_box()
    assert box is not None, f"{selector} has no layout box"
    return box


def _wide_layout(page: Page, base_url: str) -> None:
    page.route("**/api/sessions?*", lambda route: route.fulfill(json={
        "sessions": [
            {"session_id": f"wide-{i}", "created_at": f"2026-05-10T1{i}:00:00",
             "transcript_preview": f"invented take {i}", "word_count": 3}
            for i in range(3)
        ],
        "total": 3, "has_more": False,
    }))
    page.route("**/api/sessions/*/text", lambda route: route.fulfill(
        json={"transcript": "the full invented text"}))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="visible")

    rail = _box(page, "nav.tabs")
    assert rail["x"] == 0 and rail["width"] <= 81, rail
    recorder, text = _box(page, ".recorder-card"), _box(page, ".text-card")
    assert text["x"] > recorder["x"] + recorder["width"] - 1, (recorder, text)
    assert abs(text["y"] - recorder["y"]) < 2, (recorder, text)
    assert recorder["x"] >= rail["width"], (rail, recorder)

    page.click("#tabHistory")
    detail = page.locator("#takeDetail")
    expect(detail).to_be_visible()
    expect(detail.locator("#takeSheetText")).to_have_text("the full invented text")
    expect(page.locator("#takeSheet")).to_be_hidden()  # in place, not a modal
    lst = _box(page, ".history-list-pane")
    assert _box(page, "#takeDetail")["x"] > lst["x"] + lst["width"] - 1
    page.locator("#historyGroups .take-row").first.locator(".history-more").click()
    expect(page.locator("#historyGroups .take-row.open")).to_have_count(1)
    for control in ("#takeCopy", "#takeRedo", "#takeDelete"):
        expect(detail.locator(control)).to_be_visible()

    page.set_viewport_size({"width": 1000, "height": 900})
    expect(detail).to_be_hidden()
    page.click("#tabRecord")
    recorder, text = _box(page, ".recorder-card"), _box(page, ".text-card")
    assert text["y"] >= recorder["y"] + recorder["height"], (recorder, text)


def test_viewport_projection(
    authed_page: Page, base_url: str, browser_name: str
) -> None:
    if browser_name != "webkit":
        _wide_layout(authed_page, base_url)
        return
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    width = authed_page.evaluate("window.innerWidth")
    assert width == _IPHONE_15_PRO_MAX_WIDTH, (
        f"expected the iPhone 15 Pro Max viewport width "
        f"{_IPHONE_15_PRO_MAX_WIDTH}, got {width} — the WebKit projection "
        "did not apply the device descriptor"
    )
