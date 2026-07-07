"""Shared helpers for the record-flow regression tests (issues #12, #14).

Not a test module — imported by ``test_background_finalize.py`` and
``test_resume_take.py``. Route-mocking the ``/api/sessions*`` family lets
those tests drive the full record → finalise flow deterministically,
with no microphone-fed whisper round-trip: the only thing left real is
the Chromium fake-media ``MediaRecorder``.
"""

from __future__ import annotations

from playwright.sync_api import Page, Route

_MOCK_SESSION_ID = "e2e-regression-sess"


def mock_session_apis(page: Page, transcript: str) -> None:
    """Route-mock every ``/api/sessions*`` call the record flow makes.

    ``/api/config``, ``/api/status``, ``/api/version`` are left alone so
    they hit the real server and the SPA boots normally.
    """

    def _handler(route: Route) -> None:
        req = route.request
        url = req.url.split("?", 1)[0]
        if url.endswith("/api/sessions") and req.method == "POST":
            route.fulfill(json={
                "session_id": _MOCK_SESSION_ID,
                "folder": "",
                "created_at": "2026-05-21T00:00:00Z",
                "incognito": False,
            })
        elif url.endswith("/api/sessions") and req.method == "GET":
            route.fulfill(json={
                "sessions": [], "total": 0, "offset": 0, "limit": 10,
            })
        elif url.endswith("/chunk"):
            route.fulfill(json={
                "session_id": _MOCK_SESSION_ID, "raw_bytes": 4096,
            })
        elif url.endswith("/finish"):
            route.fulfill(json={
                "session_id": _MOCK_SESSION_ID,
                "transcript": transcript,
                "language": "en",
            })
        elif url.endswith("/events"):
            # Open, valid SSE stream that never emits — the rolling
            # worker isn't under test here.
            route.fulfill(
                status=200,
                content_type="text/event-stream",
                body=":ok\n\n",
            )
        else:
            route.fulfill(json={})

    page.route("**/api/sessions**", _handler)


def start_recording(page: Page) -> None:
    """Tap Record and wait until the MediaRecorder is actually running.

    VAD auto-stop is disabled first: Chromium's fake-media stream can
    read as silence, and an auto-stop firing on its own would end the
    take independently of the backgrounding path under test. With the
    toggle off, the take ends only when the test ends it.
    """
    page.wait_for_selector("#recordBtn", state="visible", timeout=8_000)
    # The toggle is the fleet role="switch" button (issue #107); mirror the
    # vendored setSwitch() write path so isOn() reads it as off.
    page.evaluate(
        "var t = document.getElementById('vadAutoStopToggle');"
        "if (t) { t.classList.remove('on');"
        " t.setAttribute('aria-checked', 'false'); }"
    )
    page.click("#recordBtn")
    # aria-pressed flips to 'true' only once setMode('recording') has run.
    page.wait_for_selector(
        "#recordBtn[aria-pressed='true']", state="attached", timeout=10_000
    )
