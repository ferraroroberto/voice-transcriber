"""Control boundaries use the ``control-border`` token (issue #210).

WCAG 1.4.11 asks a control's boundary to reach 3:1 against its surface. The
hairline ``--line`` / ``--line-muted`` borders sit near 1.4:1, so the icon-only
incognito chip and the text areas draw their edge in ``--control-border``.
Computed style is read straight off the elements, so the check holds in both
themes without opening the hidden Settings pane.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.smoke

_BORDER_JS = "(sel) => getComputedStyle(document.querySelector(sel)).borderTopColor"
# The token's own resolved colour, via a throwaway probe so the test follows the
# stylesheet instead of hard-coding a hex.
_TOKEN_JS = """() => {
  const probe = document.createElement('div');
  probe.style.borderTop = '1px solid var(--control-border)';
  document.body.appendChild(probe);
  const color = getComputedStyle(probe).borderTopColor;
  probe.remove();
  return color;
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_icon_chip_and_text_areas_draw_the_control_border(
    authed_page: Page, base_url: str, scheme: str
) -> None:
    authed_page.emulate_media(color_scheme=scheme)
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)

    token = authed_page.evaluate(_TOKEN_JS)
    for selector in ("#incognitoToggle", "#transcript", "#polished", "#polishPromptPreview"):
        border = authed_page.evaluate(_BORDER_JS, selector)
        assert border == token, (
            f"{selector} ({scheme}) borders in {border!r}, control-border is {token!r}"
        )
