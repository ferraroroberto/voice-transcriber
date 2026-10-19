"""Regression pin for #283 — a failed session create must not kill Record.

``startRecording()`` sets the mode to ``'starting'`` and then awaits
``POST /api/sessions``. A network failure (a tunnel blip, Safari's
"Load failed") rejects that fetch; before the fix nothing caught it, so
the mode stuck on ``'starting'`` and ``onRecordToggle`` — which only acts
on ``'idle'``/``'recording'`` — ignored every later tap until a reload.

Here the first create is aborted at the network layer, then the route is
swapped for the working mock and Record is tapped again: it must start a
real take. ``desktop_only`` — WebKit can't fake a media stream.

To watch it fail: revert the try/catch around the session create in
``recorder.js`` — the toast never shows and the second tap never records.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import mock_session_apis, start_recording
from tests.e2e._surface import surface

pytestmark = [pytest.mark.smoke, pytest.mark.desktop_only]


def test_failed_session_create_leaves_record_usable(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    page.route("**/api/sessions", lambda route: (
        route.abort() if route.request.method == "POST" else route.fallback()
    ))
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="visible", timeout=8_000)
    page.click("#recordBtn")

    expect(page.locator("#toast")).to_contain_text("Could not create session")
    expect(page.locator("#recordBtn")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#recordStatus")).to_have_text("")
    surface(page, "record.start-failure")

    # The network is back: the very same button must record again.
    page.unroute("**/api/sessions")
    mock_session_apis(page, "recovered after the failed start")
    start_recording(page)
