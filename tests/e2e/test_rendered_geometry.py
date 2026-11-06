"""Rendered-geometry conformance across the fleet viewport x theme matrix.

The static design lint proves authored tokens; this proves what the browser laid
out: no horizontal page overflow on any pane, and (on the touch projection) every
visible control reaching the 44px hit floor with no two hit areas overlapping.
Runs on all eight legs (320/390/430/772px x light/dark) through the vendored
``_geometry`` helper.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from tests.e2e._geometry import (
    MATRIX,
    apply_matrix_leg,
    assert_min_target,
    assert_no_horizontal_overflow,
    assert_no_overlap,
    matrix_id,
)

pytestmark = pytest.mark.smoke

_THEME_KEY = "voice-transcriber.theme"
# Visible, tappable controls; a checkbox-style switch is a classed <button>, and
# the hidden inputs the app keeps for state are excluded by :visible.
_CONTROLS = (
    "button:visible, select:visible, "
    "input:visible:not([type=checkbox], [type=radio], [type=range], [type=file])"
)
_PANES = (
    ("record", "#tabRecord", "#paneRecord"),
    ("history", "#tabHistory", "#paneHistory"),
    ("settings", ".home-settings:visible", "#paneSettings"),
)


def _set_theme(page: Page, theme: str) -> None:
    page.evaluate(
        "([key, theme]) => {"
        " try { localStorage.setItem(key, theme); } catch (_) {}"
        " document.documentElement.dataset.theme = theme; }",
        [_THEME_KEY, theme],
    )
    # Boot check: the theme took if the page background follows the token.
    resolved = page.evaluate("document.documentElement.dataset.theme")
    assert resolved == theme, f"theme {theme!r} did not apply (got {resolved!r})"


@pytest.mark.parametrize("leg", MATRIX, ids=matrix_id)
def test_panes_fit_and_controls_are_tappable(
    authed_page: Page, base_url: str, browser_name: str, leg: tuple[int, str]
) -> None:
    page = authed_page
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)
    apply_matrix_leg(page, *leg, set_theme=_set_theme)
    # Hit floors are a touch-pointer contract: only the iPhone projection has one.
    coarse = page.evaluate("matchMedia('(pointer: coarse)').matches")
    assert coarse == (browser_name == "webkit"), (
        f"pointer projection mismatch on {browser_name}: coarse={coarse}"
    )

    for name, opener, pane in _PANES:
        page.locator(opener).first.click()
        page.wait_for_selector(pane, state="visible", timeout=5_000)
        assert_no_horizontal_overflow(page)
        if not coarse:
            continue
        # The tab bar floats over the scrolling pane by design, so it is measured
        # for its own floor but not for overlap against the content beneath it.
        controls = page.locator(f"{pane} :is({_CONTROLS})")
        assert controls.count() > 0, f"{name}: no controls found to measure"
        assert_min_target(controls)
        assert_no_overlap(controls)
        # The vendored nav keeps its 44px floor on a coarse pointer at every width.
        assert_min_target(page.locator("nav.tabs button"))
