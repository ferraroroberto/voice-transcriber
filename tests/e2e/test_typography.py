"""Typography contract: the app's type (issue #226).

Form controls don't inherit the page font by themselves: the UA stylesheet
gives ``button``/``input``/``select``/``textarea`` their own face (Arial on
Windows Chrome). The vendored ``base`` layer pulls the body's font onto them,
so a nav tab, a button and an input all render in the same typeface as the
text around them.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.smoke

_FAMILY_JS = "(sel) => getComputedStyle(document.querySelector(sel)).fontFamily"


def test_form_controls_inherit_the_body_font(authed_page: Page, base_url: str) -> None:
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)

    body_family = authed_page.evaluate(_FAMILY_JS, "body")
    assert "system-ui" in body_family, f"body lost the fleet font stack: {body_family!r}"

    # A nav tab (the label that read larger on the desktop rail), a bare
    # button, a number input, a select and the polish-prompt preview (prose,
    # so no monospace override) each render in the body's face.
    for selector in (
        ".tabs .tab",
        "#recordBtn",
        "#gainBoostDb",
        "#polishModel",
        "#polishPromptPreview",
    ):
        family = authed_page.evaluate(_FAMILY_JS, selector)
        assert family == body_family, (
            f"{selector} computes font-family {family!r}, body is {body_family!r}"
        )
