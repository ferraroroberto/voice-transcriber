"""Toast contract (issue #226, fleet COLOR-05): the toast is neutral.

A success or info message never takes a colour; only a real error tints.
The test drives the app's own ``showToast`` (the stamped ``ui.js`` the page
already loaded, so it is the same module instance) and reads the painted
result.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.smoke

_SHOW_JS = """async ([message, kind]) => {
  const url = performance.getEntriesByType('resource')
    .map((r) => r.name)
    .find((n) => /\\/static\\/ui\\.js(\\?|$)/.test(n));
  const { showToast } = await import(url);
  showToast(message, kind);
  const el = document.getElementById('toast');
  const style = getComputedStyle(el);
  return {
    cls: el.className,
    live: el.getAttribute('aria-live'),
    background: style.backgroundColor,
    visible: !el.hidden,
  };
}"""


def _channels(css_color: str) -> tuple[float, ...]:
    """RGB on 0-255 whatever the serialisation: ``rgb(...)`` is 0-255, but a
    ``color-mix()`` result comes back as ``color(srgb 0.1 0.5 0.2 / 0.9)``."""
    nums = tuple(float(n) for n in re.findall(r"[\d.]+", css_color)[:3])
    return tuple(n * 255 for n in nums) if css_color.startswith("color(") else nums


def _spread(css_color: str) -> float:
    r, g, b = _channels(css_color)
    return max(r, g, b) - min(r, g, b)


def test_success_toast_is_neutral_and_only_an_error_tints(
    authed_page: Page, base_url: str
) -> None:
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.wait_for_selector("#recordBtn", state="attached", timeout=5_000)
    authed_page.wait_for_load_state("networkidle")

    ok = authed_page.evaluate(_SHOW_JS, ["Settings saved", "success"])
    assert ok["visible"]
    # The nav bar's glass: white or near-black, never a hue.
    assert _spread(ok["background"]) < 24, f"success toast is tinted: {ok['background']}"
    assert "error" not in ok["cls"].split()
    assert ok["live"] == "polite"

    bad = authed_page.evaluate(_SHOW_JS, ["Save failed", "error"])
    assert bad["live"] == "assertive"
    assert "error" in bad["cls"].split()
    r, g, b = _channels(bad["background"])
    assert r > g + 40 and r > b + 40, f"error toast is not danger-tinted: {bad['background']}"
