"""Regression pin for issue #12 (commit 642550d) — backgrounding mid-record.

The bug class: a mobile browser suspends the PWA and revokes the mic the
moment the user switches apps or locks the screen. Before #12 the
in-flight take was simply lost. The fix wires ``finalizeForBackground()``
into both the ``visibilitychange`` (hidden) and ``pagehide`` handlers, so
the audio streamed so far is finalised — POSTed to ``/finish``,
transcribed, saved — instead of dropped.

These tests drive a real ``MediaRecorder`` (Chromium fake-media), then
simulate each backgrounding path and assert a ``/finish`` POST fires and
the transcript lands on screen. ``desktop_only`` — WebKit can't fake a
media stream.

To watch them fail meaningfully: on a throwaway branch, delete the
``finalizeForBackground()`` call from the handler under test — the
``/finish`` request never fires and ``expect_request`` times out.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import mock_session_apis, start_recording

pytestmark = [pytest.mark.smoke, pytest.mark.desktop_only]

_TRANSCRIPT = "regression take saved across the app switch"


def _finish_request(request) -> bool:
    return request.method == "POST" and "/finish" in request.url


def test_pagehide_mid_record_finalizes_the_take(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    mock_session_apis(page, _TRANSCRIPT)
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    start_recording(page)
    page.wait_for_timeout(1300)  # let a chunk or two stream to "disk"

    # pagehide can mean the page is being discarded outright — the take
    # must be finalised, not abandoned as loose chunks.
    with page.expect_request(_finish_request):
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")

    expect(page.locator("#transcript")).to_have_value(_TRANSCRIPT)
    # The "Saved while you were away" status is the discriminating signal:
    # it only renders when state.backgroundFinalized is set, which only
    # finalizeForBackground() does. (Releasing the mic stream also ends
    # the recorder, so a /finish alone doesn't prove the fix is wired.)
    expect(page.locator("#recordStatus")).to_contain_text(
        "Saved while you were away"
    )


def test_visibility_hidden_mid_record_finalizes_the_take(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    mock_session_apis(page, _TRANSCRIPT)
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    start_recording(page)
    page.wait_for_timeout(1300)

    # App switch / screen lock: visibilityState flips to 'hidden'. Force
    # the getter, then fire the event the handler listens for.
    with page.expect_request(_finish_request):
        page.evaluate(
            "Object.defineProperty(document, 'visibilityState', "
            "{configurable: true, get: () => 'hidden'});"
            "document.dispatchEvent(new Event('visibilitychange'));"
        )

    expect(page.locator("#transcript")).to_have_value(_TRANSCRIPT)
    expect(page.locator("#recordStatus")).to_contain_text(
        "Saved while you were away"
    )
