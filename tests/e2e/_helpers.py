"""Shared helpers for the e2e tests.

Not a test module. Route-mocking the ``/api/sessions*`` family lets the
record-flow tests drive the full record → finalise flow deterministically,
with no microphone-fed whisper round-trip: the only thing left real is
the Chromium fake-media ``MediaRecorder``.
"""

from __future__ import annotations

import re

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


def _channels(css_color: str) -> tuple[float, ...]:
    """RGB on 0-255 whatever the serialisation: ``rgb(...)`` is 0-255, but a
    ``color-mix()`` result comes back as ``color(srgb 0.1 0.5 0.2 / 0.9)``."""
    nums = tuple(float(n) for n in re.findall(r"[\d.]+", css_color)[:3])
    return tuple(n * 255 for n in nums) if css_color.startswith("color(") else nums


_TOAST_JS = """() => {
  const el = document.getElementById('toast');
  return {
    cls: el.className,
    live: el.getAttribute('aria-live'),
    background: getComputedStyle(el).backgroundColor,
  };
}"""


def assert_toast_paint(page: Page, *, error: bool) -> None:
    """The toast contract: only a real error tints, and only it is assertive.

    A success or info toast keeps the nav bar's glass (white or near-black,
    never a hue) and announces politely; an error is danger-tinted (red well
    above green and blue) and announces assertively.
    """
    toast = page.evaluate(_TOAST_JS)
    r, g, b = _channels(toast["background"])
    assert ("error" in toast["cls"].split()) == error, toast
    if error:
        assert toast["live"] == "assertive", toast
        assert r > g + 40 and r > b + 40, f"error toast is not danger-tinted: {toast}"
    else:
        assert toast["live"] == "polite", toast
        assert max(r, g, b) - min(r, g, b) < 24, f"toast is tinted: {toast}"
