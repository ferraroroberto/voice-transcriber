"""E2E: History — the Latest take card, day-grouped rows paged by the
server's ``has_more`` flag (issue #139), Select mode, and the take sheet.

Route-mocks ``/api/sessions`` so the contract is exercised in a real
browser without seeding a large archive: page 0 reports ``has_more=true``
("Show older takes" shows), the final page ``has_more=false`` (it hides).
Four takes share each day, so one day straddles the page boundary and must
stay one group (#264). All text is invented.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Route, expect

_TOTAL = 15
_PAGE = 10


def _take(i: int) -> dict:
    return {
        "session_id": f"sess-{i:02d}",
        # Newest first, four takes a day: 0-3 share a day, 8-11 straddle
        # the page boundary at 10.
        "created_at": f"2026-05-{28 - i // 4:02d}T{20 - i % 4:02d}:15:00",
        "transcript_preview": f"take number {i}",
        "word_count": 10 + i,
        "source": "webapp" if i % 2 else "api",
        "language": "es" if i == 2 else "en",
        # The newest take carries the served-by stamp (#261); older ones
        # predate it.
        **({"served_model": "Speech Model A", "served_host": "Box One",
            "duration_seconds": 27} if i == 0 else {}),
    }


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
        route.fulfill(json={
            "sessions": [_take(i) for i in range(offset, min(offset + limit, _TOTAL))],
            "total": _TOTAL,
            "has_more": offset + limit < _TOTAL,
            "offset": offset,
            "limit": limit,
        })

    page.route("**/api/sessions**", _handler)


def _texts(page: Page, ids: list[str]) -> None:
    for sid in ids:
        page.route(
            f"**/api/sessions/{sid}/text",
            lambda route, sid=sid: route.fulfill(json={"transcript": f"full text of {sid}"}),
        )


def test_latest_card_day_groups_and_paging(
    authed_page: Page, base_url: str
) -> None:
    """History opens on the Latest take card (one-tap Copy of the newest
    take), then one-line rows under a heading per day; "Show older takes"
    extends the day it continues instead of repeating its heading."""
    _mock_sessions(authed_page)
    _texts(authed_page, ["sess-00"])
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.click("#tabHistory")

    latest = authed_page.locator("#latestCard")
    expect(latest).to_be_visible()
    expect(latest.locator("#latestText")).to_have_text("take number 0")
    # The generic "api" source names the transport, so it is not shown.
    expect(latest.locator("#latestMeta")).to_have_text("10 words · 0:27")
    with authed_page.expect_request("**/api/sessions/sess-00/text"):
        latest.locator("#latestCopy").click()

    rows = authed_page.locator("#historyGroups .take-row")
    expect(rows).to_have_count(_PAGE - 1)  # the newest is the card
    second = rows.first
    expect(second.locator(".take-time")).to_have_text(re.compile(r"^(19:15|0?7:15\s?PM)$"))
    expect(second.locator(".meta-text")).to_have_text("11 words · This app")
    expect(rows.nth(1).locator(".lang-chip")).to_have_text("ES")
    expect(rows.first.locator(".lang-chip")).to_have_count(0)

    load_more = authed_page.locator("#loadMoreHistory")
    expect(load_more).to_be_visible()  # has_more=true → button shown
    # The header line counts the list's own `total` (#261), not the pages shown.
    expect(authed_page.locator("#historyHeadLine")).to_have_text(
        re.compile(rf"^{_TOTAL} takes · kept \d+ days$")
    )
    heads = authed_page.locator("#historyGroups .day-head")
    expect(heads).to_have_count(3)  # days of takes 1-3, 4-7, 8-9

    load_more.click()
    expect(rows).to_have_count(_TOTAL - 1)  # second page appended
    expect(load_more).to_be_hidden()  # has_more=false → button hidden
    expect(heads).to_have_count(4)  # 8-11 is still one day: no repeated heading
    counts = [int(t) for t in heads.locator(".day-count").all_inner_texts()]
    assert counts == [3, 4, 4, 3], counts
    expect(authed_page.locator("#cleanAll")).to_be_visible()


def test_select_mode_copies_oldest_first(authed_page: Page, base_url: str) -> None:
    """Select mode replaces the checkbox column: rows tick instead of
    copying, the bar copies oldest first, and leaving restores the rows."""
    _mock_sessions(authed_page)
    _texts(authed_page, ["sess-00", "sess-02"])
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.click("#tabHistory")
    rows = authed_page.locator("#historyGroups .take-row")
    expect(rows).to_have_count(_PAGE - 1)
    expect(authed_page.locator(".take-row .take-tick:visible")).to_have_count(0)

    authed_page.click("#selectTakes")
    expect(authed_page.locator("#latestCard")).to_be_hidden()
    expect(rows).to_have_count(_PAGE)  # the newest is a row while selecting
    expect(authed_page.locator("#historyGroups .history-more")).to_have_count(0)
    rows.nth(0).locator(".history-main").click()
    rows.nth(2).locator(".history-main").click()
    expect(authed_page.locator("#selectCount")).to_have_text("2 takes · oldest first")
    expect(authed_page.locator("#historyHeadLine")).to_have_text("2 selected")

    fetched: list[str] = []
    authed_page.on(
        "request",
        lambda r: fetched.append(r.url.rsplit("/", 2)[-2]) if r.url.endswith("/text") else None,
    )
    authed_page.click("#copySelection")
    expect(authed_page.locator("#selectBar")).to_be_hidden()
    assert fetched == ["sess-02", "sess-00"], fetched  # oldest first
    expect(authed_page.locator("#latestCard")).to_be_visible()
    expect(rows).to_have_count(_PAGE - 1)

    authed_page.click("#selectTakes")
    authed_page.click("#selectCancel")
    expect(authed_page.locator("#selectBar")).to_be_hidden()
    expect(authed_page.locator("#historyGroups .history-more")).to_have_count(_PAGE - 1)


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
    _texts(authed_page, ["sess-01"])
    native_dialogs: list[str] = []
    authed_page.on("dialog", lambda d: (native_dialogs.append(d.message), d.dismiss()))
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.click("#tabHistory")

    rows = authed_page.locator("#historyGroups .take-row")
    with authed_page.expect_request("**/api/sessions/sess-01/text"):
        rows.first.locator(".history-main").click()

    sheet = authed_page.locator("#takeSheet")
    authed_page.locator("#latestMore").click()
    expect(sheet).to_be_visible()
    expect(sheet.locator("#takeSheetText")).to_have_text(full)
    expect(sheet.locator("#takeServedBy")).to_have_text("Speech Model A · Box One")
    expect(sheet.locator("#takeLength")).to_have_text("12 words · 0:27")
    expect(sheet.locator("#takeSource")).to_have_text("api")  # verbatim in the sheet
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
    rows.first.locator(".history-more").click()
    expect(sheet.locator("#takeServedBy")).to_have_text("Not recorded")
    expect(delete).to_have_text("Delete take")
    sheet.locator(".detail-close").click()
    expect(sheet).to_be_hidden()
    assert native_dialogs == []
