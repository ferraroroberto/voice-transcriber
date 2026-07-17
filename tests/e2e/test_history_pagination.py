"""E2E: History pagination — "Load more" reveals older takes and hides at
the end, driven by the server's ``has_more`` flag (issue #139).

Route-mocks ``/api/sessions`` so the pagination contract is exercised in a
real browser without seeding a large archive: page 0 reports
``has_more=true`` (button shows), the final page reports ``has_more=false``
(button hides). This guards the frontend switch from the old
``shown >= total`` gate to the incognito-accurate ``has_more`` signal.
"""

from __future__ import annotations

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
    expect(authed_page.locator("#historyCount")).to_have_text(
        f"{_PAGE}/{_TOTAL}"
    )

    load_more.click()
    expect(rows).to_have_count(_TOTAL)  # second page appended
    expect(load_more).to_be_hidden()  # has_more=false → button hidden
