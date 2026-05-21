"""Regression pin for issue #14 (commit c529d73) — ▶ Resume after backgrounding.

The ▶ Resume button is offered only after a take was finalised by
backgrounding (issue #12). Tapping it starts a fresh take that
force-appends onto the existing transcript regardless of the ➕ Append
toggle, so the seam across the app-switch is invisible.

This drives a real ``MediaRecorder`` (Chromium fake-media), backgrounds
it to produce a finalised take, then asserts the Resume button appears
and that clicking it restarts recording with the earlier transcript
still in place. ``desktop_only`` — WebKit can't fake a media stream.

To watch it fail meaningfully: on a throwaway branch, delete the
``showResumeButton()`` call from ``onRecorderStopped`` — the button
stays hidden and ``expect(...).to_be_visible()`` times out.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import mock_session_apis, start_recording

pytestmark = [pytest.mark.smoke, pytest.mark.desktop_only]

_TRANSCRIPT = "first half of the dictation, before the app switch"


def _finish_request(request) -> bool:
    return request.method == "POST" and "/finish" in request.url


def test_resume_button_continues_a_backgrounded_take(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    mock_session_apis(page, _TRANSCRIPT)
    page.goto(f"{base_url}/", wait_until="domcontentloaded")

    # Record, then background mid-take so it is finalised (issue #12).
    start_recording(page)
    page.wait_for_timeout(1300)
    with page.expect_request(_finish_request):
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    expect(page.locator("#transcript")).to_have_value(_TRANSCRIPT)

    # The ▶ Resume button is offered only because the take ended via
    # backgrounding and produced a transcript.
    resume = page.locator("#resumeBtn")
    expect(resume).to_be_visible()

    # Tapping Resume reattaches: a fresh take starts, the button hides,
    # and the earlier transcript stays on screen for the new take to
    # append onto.
    resume.click()
    page.wait_for_selector(
        "#recordBtn[aria-pressed='true']", state="attached", timeout=10_000
    )
    expect(resume).to_be_hidden()
    expect(page.locator("#transcript")).to_have_value(_TRANSCRIPT)
