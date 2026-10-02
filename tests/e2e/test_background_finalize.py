"""Regression pin for issue #12 (commit 642550d) — backgrounding mid-record.

The bug class: a mobile browser suspends the PWA and revokes the mic the
moment the user switches apps or locks the screen. Before #12 the
in-flight take was simply lost. The fix wires ``finalizeForBackground()``
into both the ``visibilitychange`` (hidden) and ``pagehide`` handlers, so
the audio streamed so far is finalised — POSTed to ``/finish``,
transcribed, saved — instead of dropped.

This test drives a real ``MediaRecorder`` (Chromium fake-media), then
simulates the ``visibilitychange`` backgrounding path and asserts a
``/finish`` POST fires and the transcript lands on screen. The ``pagehide``
path is pinned by ``test_resume_take.py``, which backgrounds that way.
``desktop_only`` — WebKit can't fake a media stream.

To watch it fail meaningfully: on a throwaway branch, delete the
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
