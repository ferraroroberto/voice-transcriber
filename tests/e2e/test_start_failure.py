"""Record survives a flaky connection: a failed config load and a failed start.

Two transient network failures the SPA boot and the first tap explicitly
anticipate (a cold-waking Tailscale link, a tunnel blip, Safari's "Load
failed"), each of which used to kill Record for the rest of the page session:

* ``startRecording()`` sets the mode to ``'starting'`` and then awaits
  ``POST /api/sessions``. A rejected fetch left the mode stuck on
  ``'starting'``, and ``onRecordToggle`` — which only acts on
  ``'idle'``/``'recording'`` — ignored every later tap until a reload.
* The live "rolling" transcript fills the box word-by-word via an SSE
  subscription that ``openPartialStream()`` opens only when the config says
  rolling is enabled. When the very first ``GET /api/config`` failed, the
  offline-defaults object omitted the flag, so live partials silently vanished
  while chunk upload and ``/finish`` kept working. The failure is invisible
  over loopback, where ``/api/config`` never fails.

Here the first config load and the first session create are aborted at the
network layer, then the session route is swapped for the working mock and
Record is tapped again: it must start a real take and subscribe to partials.
``desktop_only`` — WebKit can't fake a media stream.

To watch it fail: revert the try/catch around the session create in
``recorder.js`` (the toast never shows and the second tap never records), or
revert the ``openPartialStream`` gate in ``partials.js`` to
``!state.config.rolling_transcription_enabled`` (``/events`` never fires).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import assert_toast_paint, mock_session_apis, start_recording
from tests.e2e._surface import surface

pytestmark = [pytest.mark.smoke, pytest.mark.desktop_only]


def test_record_survives_a_failed_config_load_and_a_failed_start(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    # The first /api/config fails, so the SPA falls back to client defaults.
    page.route("**/api/config", lambda route: route.abort())
    page.route("**/api/sessions", lambda route: (
        route.abort() if route.request.method == "POST" else route.fallback()
    ))
    events_requests: list[str] = []
    page.on(
        "request",
        lambda r: events_requests.append(r.url) if "/events" in r.url else None,
    )
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#recordBtn", state="visible", timeout=8_000)
    page.click("#recordBtn")

    expect(page.locator("#toast")).to_contain_text("Could not create session")
    assert_toast_paint(page, error=True)
    expect(page.locator("#recordBtn")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#recordStatus")).to_have_text("")
    surface(page, "record.start-failure")
    assert events_requests == []

    # The network is back: the very same button must record again, and the
    # stream emits one partial, which lands in the Text card merged after the
    # existing text because Append is on (#263).
    page.unroute("**/api/sessions")
    mock_session_apis(page, "final transcript text")
    page.route("**/api/sessions/*/events**", lambda route: route.fulfill(
        status=200,
        content_type="text/event-stream",
        body='event: partial\ndata: {"version": 1, "transcript": "live words"}\n\n',
    ))
    page.fill("#transcript", "earlier text")
    page.click("#appendToggle")
    start_recording(page)
    # openPartialStream() fires inside startRecording() before aria-pressed
    # flips (which start_recording already waited for); give the SSE request
    # a beat to leave the browser.
    page.wait_for_timeout(800)

    assert events_requests, (
        "live-partial SSE stream (/events) was never opened after a failed "
        "/api/config — openPartialStream() gated itself off on a fallback config"
    )
    expect(page.locator("#transcript")).to_have_value("earlier text\n\nlive words")
    expect(page.locator("#textMeta")).to_have_text("Live text · refined when you stop")
    surface(page, "record.live-partial")
