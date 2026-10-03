"""Text-size contract (issue #226, fleet text-size setting).

The viewport is zoom-locked, so Settings carries the escape: a persisted
Small / Default / Large control that scales the root font-size. Every
rem-based type role scales with it; the choice is stamped before first paint.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.smoke

# step -> root font-size in px (93.75% / 100% / 112.5% of the 16px default)
_STEPS = {"small": 15.0, "default": 16.0, "large": 18.0}

_ROOT_PX = "parseFloat(getComputedStyle(document.documentElement).fontSize)"
_BODY_PX = "parseFloat(getComputedStyle(document.body).fontSize)"
_TITLE_PX = "parseFloat(getComputedStyle(document.querySelector('#paneSettings .home-title')).fontSize)"


def _open_settings(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)
    page.click("#paneRecord .home-settings")
    expect(page.locator("#textSizeControl")).to_be_visible()


def test_each_step_changes_the_root_font_size(authed_page: Page, base_url: str) -> None:
    _open_settings(authed_page, base_url)
    for step, px in _STEPS.items():
        authed_page.click(f'#textSizeControl [data-textsize="{step}"]')
        assert authed_page.evaluate("document.documentElement.dataset.textsize") == step
        assert authed_page.evaluate(_ROOT_PX) == px, step
        # rem-based type follows the root: body text and a heading both scale.
        assert authed_page.evaluate(_BODY_PX) == px, step
        assert authed_page.evaluate(_TITLE_PX) == px, step
        expect(
            authed_page.locator(f'#textSizeControl [data-textsize="{step}"]')
        ).to_have_attribute("aria-pressed", "true")


def test_the_chosen_step_survives_a_reload_before_first_paint(
    authed_page: Page, base_url: str
) -> None:
    _open_settings(authed_page, base_url)
    authed_page.click('#textSizeControl [data-textsize="large"]')
    assert authed_page.evaluate("localStorage.getItem('voice-transcriber.textsize')") == "large"

    authed_page.reload(wait_until="commit")
    # The inline head script stamps the attribute as the document is parsed,
    # so it is already there at domcontentloaded, well before any module runs.
    authed_page.wait_for_load_state("domcontentloaded")
    assert authed_page.evaluate("document.documentElement.dataset.textsize") == "large"
    assert authed_page.evaluate(_ROOT_PX) == _STEPS["large"]
