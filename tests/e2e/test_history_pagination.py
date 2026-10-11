"""E2E: History pagination — "Load more" reveals older takes and hides at
the end, driven by the server's ``has_more`` flag (issue #139).

Route-mocks ``/api/sessions`` so the pagination contract is exercised in a
real browser without seeding a large archive: page 0 reports
``has_more=true`` (button shows), the final page reports ``has_more=false``
(button hides). This guards the frontend switch from the old
``shown >= total`` gate to the incognito-accurate ``has_more`` signal.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Route, expect

_TOTAL = 15
_PAGE = 10


def _mock_sessions(page: Page) -> None:
    """Serve ``sessions[offset:offset+limit]`` + a ``has_more`` flag."""

    def _handler(route: Route) -> None:
        req = route.request
        if req.method != "GET":
            route.fulfill(json={})
            return
        qs = parse_qs(urlparse(req.url).query)
        offset = int(qs.get("offset", ["0"])[0])
        limit = int(qs.get("limit", ["10"])[0])
        chunk = [
            {
                "session_id": f"sess-{i:02d}",
                "created_at": f"2026-05-{(i % 28) + 1:02d}T00:00:00",
                "transcript_preview": f"take number {i}",
                # The newest take carries the served-by stamp (#261); older
                # ones predate it.
                **({"served_model": "Speech Model A", "served_host": "Box One",
                    "duration_seconds": 27} if i == 0 else {}),
            }
            for i in range(offset, min(offset + limit, _TOTAL))
        ]
        route.fulfill(json={
            "sessions": chunk,
            "total": _TOTAL,
            "has_more": offset + limit < _TOTAL,
            "offset": offset,
            "limit": limit,
        })

    page.route("**/api/sessions**", _handler)


def test_load_more_paginates_and_hides_at_end(
    authed_page: Page, base_url: str
) -> None:
    _mock_sessions(authed_page)
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    # History loads on boot (app.js init → refreshHistory). Reveal the pane
    # so the Load-more button has a layout box to click.
    authed_page.click("#tabHistory")

    rows = authed_page.locator("#historyList > li")
    expect(rows).to_have_count(_PAGE)  # first page rendered
    load_more = authed_page.locator("#loadMoreHistory")
    expect(load_more).to_be_visible()  # has_more=true → button shown
    # The header line counts the list's own `total` (#261), not the pages shown.
    expect(authed_page.locator("#historyHeadLine")).to_have_text(
        re.compile(rf"^{_TOTAL} takes · kept \d+ days$")
    )

    load_more.click()
    expect(rows).to_have_count(_TOTAL)  # second page appended
    expect(load_more).to_be_hidden()  # has_more=false → button hidden


def test_row_taps_copy_and_kebab_opens_the_take_sheet(
    authed_page: Page, base_url: str
) -> None:
    """A take is an action-row (#213): tapping the body fetches the full text
    to copy, and the one trailing kebab opens the take sheet (#261): full
    text, Copy, Redo, the facts with Served by, and Delete last, which needs
    a second tap and never raises the browser's own confirm()."""
    _mock_sessions(authed_page)
    full = "take number 0, and the rest of it that the preview cut"
    authed_page.route(
        "**/api/sessions/sess-00/text",
        lambda route: route.fulfill(json={"transcript": full}),
    )
    native_dialogs: list[str] = []
    authed_page.on("dialog", lambda d: (native_dialogs.append(d.message), d.dismiss()))
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.click("#tabHistory")

    rows = authed_page.locator("#historyList > li")
    first = rows.first
    with authed_page.expect_request("**/api/sessions/sess-00/text"):
        first.locator(".history-main").click()

    sheet = authed_page.locator("#takeSheet")
    first.locator(".history-more").click()
    expect(sheet).to_be_visible()
    expect(sheet.locator("#takeSheetText")).to_have_text(full)
    expect(sheet.locator("#takeServedBy")).to_have_text("Speech Model A · Box One")
    expect(sheet.locator("#takeLength")).to_have_text("12 words · 0:27")
    for control in ("#takeCopy", "#takeRedo", "#takeDelete"):
        expect(sheet.locator(control)).to_be_visible()

    delete = sheet.locator("#takeDelete")
    deletes: list[str] = []
    authed_page.on("request", lambda r: deletes.append(r.url) if r.method == "DELETE" else None)
    delete.click()
    expect(delete).to_have_text("Tap again to delete")
    assert deletes == []
    with authed_page.expect_request(lambda r: r.method == "DELETE" and r.url.endswith("/api/sessions/sess-00")):
        delete.click()
    expect(sheet).to_be_hidden()

    # An older take predates the stamp; closing disarms the delete row.
    rows.nth(1).locator(".history-more").click()
    expect(sheet.locator("#takeServedBy")).to_have_text("Not recorded")
    expect(delete).to_have_text("Delete take")
    sheet.locator(".detail-close").click()
    expect(sheet).to_be_hidden()
    assert native_dialogs == []
