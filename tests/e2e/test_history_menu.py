"""E2E: History's kebabs open a menu (Latest take card and every day row)
with Refresh, and the picked row's tint sits a card padding in from the border.

Both kebabs once did nothing visible on a wide window: the detail pane is
always open there, so a click only moved the selection. Route-mocked takes,
all text invented. Runs in both projections: the iPhone sheet and the wide
pane share the menu.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e._surface import surface

pytestmark = pytest.mark.smoke

_TAKES = [
    {"session_id": f"menu-{i}", "created_at": f"2026-05-10T1{9 - i}:00:00",
     "transcript_preview": f"invented take {i}", "word_count": 3}
    for i in range(4)
]


def _mock_history(page: Page) -> list[str]:
    """Serve the takes; return the list the page's list requests append to."""
    list_requests: list[str] = []

    def _sessions(route: Route) -> None:
        list_requests.append(route.request.url)
        route.fulfill(json={
            "sessions": _TAKES, "total": len(_TAKES), "has_more": False,
        })

    page.route("**/api/sessions?*", _sessions)
    page.route("**/api/sessions/*/text", lambda route: route.fulfill(
        json={"transcript": "the full invented text"}))
    return list_requests


def _open_history(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="visible")
    page.click("#tabHistory")
    expect(page.locator("#latestMore")).to_be_visible()


def test_each_kebab_opens_a_menu_and_refresh_reloads_in_place(
    authed_page: Page, base_url: str
) -> None:
    _mock_history(authed_page)
    _open_history(authed_page, base_url)
    authed_page.evaluate("window.__sameDocument = true")
    menu = authed_page.get_by_role("menu")

    authed_page.locator("#latestMore").click()
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem")).to_have_text(["Open take", "Refresh"])
    surface(authed_page, "history.take-menu")
    authed_page.keyboard.press("Escape")
    expect(menu).to_have_count(0)

    authed_page.locator("#historyGroups .take-row .history-more").nth(1).click()
    expect(menu).to_be_visible()
    with authed_page.expect_request("**/api/sessions?*offset=0*"):
        menu.get_by_role("menuitem", name="Refresh").click()
    expect(menu).to_have_count(0)
    assert authed_page.evaluate("window.__sameDocument") is True  # no page reload
    expect(authed_page.locator("#historyGroups .take-row")).to_have_count(len(_TAKES) - 1)


_INSET_JS = """() => {
  const row = document.querySelector('#historyGroups .take-row.open');
  const card = row.closest('.card');
  const latest = getComputedStyle(document.getElementById('latestCard'));
  const c = getComputedStyle(card), r = row.getBoundingClientRect(), b = card.getBoundingClientRect();
  return {
    left: r.left - b.left - parseFloat(c.borderLeftWidth),
    right: b.right - r.right - parseFloat(c.borderRightWidth),
    latestPadding: parseFloat(latest.paddingLeft),
    tint: getComputedStyle(row).backgroundColor,
  };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_picked_row_tint_is_inset_by_the_card_padding(
    authed_page: Page, base_url: str, browser_name: str, scheme: str
) -> None:
    """The tint fills the day card's content box: as far in from each border
    as the Latest take card's own padding, not edge to edge."""
    if browser_name != "chromium":
        pytest.skip("the picked row is a wide-window state; the iPhone opens a sheet")
    authed_page.emulate_media(color_scheme=scheme)
    _mock_history(authed_page)
    authed_page.set_viewport_size({"width": 1440, "height": 900})
    _open_history(authed_page, base_url)
    authed_page.locator("#historyGroups .take-row .history-more").nth(1).click()
    authed_page.get_by_role("menuitem", name="Open take").click()
    expect(authed_page.locator("#historyGroups .take-row.open")).to_have_count(1)
    geometry = authed_page.evaluate(_INSET_JS)
    assert geometry["left"] == pytest.approx(geometry["latestPadding"], abs=0.6), geometry
    assert geometry["right"] == pytest.approx(geometry["latestPadding"], abs=0.6), geometry
    assert geometry["tint"] != "rgba(0, 0, 0, 0)", geometry
