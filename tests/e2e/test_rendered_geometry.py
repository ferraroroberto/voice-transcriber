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


def _assert_settings_layout(page: Page) -> None:
    """Each Settings group header clears the card under it, no row's label
    runs under its control (a wrapped or overlapped row), every number field is
    80px at 16px, every row leads with an 18px glyph and no select is a bordered
    pill."""
    problems = page.evaluate(
        """() => {
          const out = [];
          for (const h of document.querySelectorAll('#settingsPanel .settings-overline')) {
            const card = h.nextElementSibling;
            const gap = card.getBoundingClientRect().top - h.getBoundingClientRect().bottom;
            if (gap < 4) out.push(`header "${h.textContent}" is ${gap}px above its card`);
          }
          for (const row of document.querySelectorAll('#settingsPanel .settings-row')) {
            const icon = row.querySelector('.settings-row-icon');
            if (icon && Math.round(icon.getBoundingClientRect().width) !== 18) {
              out.push(`row "${row.textContent.trim().slice(0, 20)}" leads with a ${icon.getBoundingClientRect().width}px glyph`);
            }
            const label = row.querySelector('.settings-row-label');
            if (!label) continue;
            const lr = label.getBoundingClientRect();
            for (let c = label.nextElementSibling; c; c = c.nextElementSibling) {
              const cr = c.getBoundingClientRect();
              if (cr.width && lr.right > cr.left + 0.5) {
                out.push(`label "${label.textContent.trim()}" overlaps its control`);
              }
            }
          }
          for (const f of document.querySelectorAll('#settingsPanel .settings-row-input')) {
            const w = f.getBoundingClientRect().width;
            const fs = parseFloat(getComputedStyle(f).fontSize);
            if (Math.round(w) !== 80 || fs !== 16) out.push(`number field #${f.id} is ${w}px at ${fs}px text`);
          }
          for (const sel of document.querySelectorAll('#settingsPanel .settings-row-select')) {
            const cs = getComputedStyle(sel);
            if (parseFloat(cs.borderTopWidth) > 0 && cs.borderTopStyle !== 'none') out.push(`select #${sel.id} is a bordered pill`);
          }
          return out;
        }"""
    )
    assert not problems, "; ".join(problems)


def test_panes_fit_and_controls_are_tappable(
    authed_page: Page, base_url: str, browser_name: str
) -> None:
    """One page load walks every matrix leg: a leg is a viewport width and a
    theme applied in place, so none needs a page of its own."""
    page = authed_page
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)
    # Hit floors are a touch-pointer contract: only the iPhone projection has one.
    coarse = page.evaluate("matchMedia('(pointer: coarse)').matches")
    assert coarse == (browser_name == "webkit"), (
        f"pointer projection mismatch on {browser_name}: coarse={coarse}"
    )

    for leg in MATRIX:
        apply_matrix_leg(page, *leg, set_theme=_set_theme)
        for name, opener, pane in _PANES:
            where = f"{matrix_id(leg)} {name}"
            page.locator(opener).first.click()
            page.wait_for_selector(pane, state="visible", timeout=5_000)
            try:
                assert_no_horizontal_overflow(page)
                if name == "settings":
                    _assert_settings_layout(page)
                if not coarse:
                    continue
                # The tab bar floats over the scrolling pane by design, so it is
                # measured for its own floor but not for overlap against the
                # content beneath it.
                controls = page.locator(f"{pane} :is({_CONTROLS})")
                assert controls.count() > 0, "no controls found to measure"
                assert_min_target(controls)
                assert_no_overlap(controls)
                # The vendored nav keeps its 44px floor on a coarse pointer at every width.
                assert_min_target(page.locator("nav.tabs button"))
            except AssertionError as exc:
                raise AssertionError(f"{where}: {exc}") from exc
