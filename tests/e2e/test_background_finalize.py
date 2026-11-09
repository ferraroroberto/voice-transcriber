"""Backgrounding mid-record: the take is finalised, and Resume continues it.

The bug class: a mobile browser suspends the PWA and revokes the mic the
moment the user switches apps or locks the screen. Without a handler the
in-flight take is simply lost. ``finalizeForBackground()`` is wired into both
the ``visibilitychange`` (hidden) and ``pagehide`` handlers, so the audio
streamed so far is finalised (POSTed to ``/finish``, transcribed, saved)
instead of dropped. The "Continue this text" button is offered only after a
take was finalised that way; tapping it starts a fresh take that
force-appends onto the existing transcript regardless of the Append toggle,
so the seam across the app switch is invisible.

This drives a real ``MediaRecorder`` (Chromium fake-media): background it via
``visibilitychange``, resume, then background the resumed take via
``pagehide``. ``desktop_only`` — WebKit can't fake a media stream.

To watch it fail meaningfully: on a throwaway branch, delete the
``finalizeForBackground()`` call from the handler under test — the
``/finish`` request never fires and ``expect_request`` times out; delete
``showResumeButton()`` from ``onRecorderStopped`` and the button stays hidden.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import mock_session_apis, start_recording
from tests.e2e._surface import surface

pytestmark = [pytest.mark.smoke, pytest.mark.desktop_only]

_FIRST = "first half of the dictation, before the app switch"
_SECOND = "second half, after resuming"


def _finish_request(request) -> bool:
    return request.method == "POST" and "/finish" in request.url


def test_backgrounding_finalizes_the_take_and_resume_continues_it(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    mock_session_apis(page, _FIRST)
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    start_recording(page)
    page.wait_for_timeout(1300)
    surface(page, "record.recording")

    # App switch / screen lock: visibilityState flips to 'hidden'. Force
    # the getter, then fire the event the handler listens for.
    with page.expect_request(_finish_request):
        page.evaluate(
            "Object.defineProperty(document, 'visibilityState', "
            "{configurable: true, get: () => 'hidden'});"
            "document.dispatchEvent(new Event('visibilitychange'));"
        )
    expect(page.locator("#transcript")).to_have_value(_FIRST)
    # The "Saved while you were away" status is the discriminating signal: it
    # only renders when state.backgroundFinalized is set, which only
    # finalizeForBackground() does. (Releasing the mic stream also ends the
    # recorder, so a /finish alone doesn't prove the handler is wired.)
    expect(page.locator("#recordStatus")).to_contain_text("Saved while you were away")
    # The circle leaves its live (danger) state for the idle "Record" (#262).
    expect(page.locator("#recordBtn")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#recordLabel")).to_have_text("Record")

    resume = page.locator("#resumeBtn")
    expect(resume).to_be_visible()
    expect(resume).to_have_text("Continue this text")
    surface(page, "record.resume")

    # The user is back: Resume reattaches. A fresh take starts, the button
    # hides, and the earlier transcript stays for the new take to append onto.
    page.evaluate("delete document.visibilityState")
    resume.click()
    page.wait_for_selector("#recordBtn[aria-pressed='true']", state="attached", timeout=10_000)
    expect(page.locator("#recordLabel")).to_have_text("Stop")
    expect(resume).to_be_hidden()
    expect(page.locator("#transcript")).to_have_value(_FIRST)

    # pagehide can mean the page is being discarded outright: the resumed take
    # must be finalised too, not abandoned as loose chunks.
    mock_session_apis(page, _SECOND)
    page.wait_for_timeout(1300)
    with page.expect_request(_finish_request):
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    expect(page.locator("#transcript")).to_have_value(f"{_FIRST}\n\n{_SECOND}")
    expect(page.locator("#recordStatus")).to_contain_text("Saved while you were away")
