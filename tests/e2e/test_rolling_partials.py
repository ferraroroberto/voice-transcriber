"""Regression pin for issue #87 — live partials must survive a failed
``/api/config`` load.

The bug class: the live "rolling" transcript fills the box word-by-word
via an SSE subscription that ``openPartialStream()`` opens only when
``state.config.rolling_transcription_enabled`` is truthy. When the very
first ``GET /api/config`` fails — which the SPA boot explicitly
anticipates on a cold-waking Tailscale link — ``init()`` swallows the
error and calls ``applyConfigDefaults()``. That offline-defaults object
used to **omit** the rolling flag, so the gate read ``undefined`` and
early-returned: live partials silently vanished for the whole page
session while chunk upload + ``/finish`` (the final transcript) kept
working. The same failure is invisible over loopback, where
``/api/config`` never fails.

The fix subscribes unless the server *explicitly* reports rolling
disabled, and carries the rolling default in the fallback config — so a
transient config blip can no longer disable live partials.

To watch this fail meaningfully: revert the ``openPartialStream`` gate in
``partials.js`` to ``!state.config.rolling_transcription_enabled`` — the
``/events`` request never fires and the assertion below trips.

``desktop_only`` — WebKit can't fake a media stream.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from tests.e2e._helpers import mock_session_apis, start_recording

pytestmark = [pytest.mark.smoke, pytest.mark.desktop_only]


def test_partials_subscribe_when_config_load_failed(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page

    # Simulate the cold-Tailscale failure of the *first* /api/config so the
    # SPA falls back to client defaults (the exact condition that used to
    # silently disable live partials).
    page.route("**/api/config", lambda route: route.abort())
    mock_session_apis(page, "final transcript text")

    events_requests: list[str] = []
    page.on(
        "request",
        lambda r: events_requests.append(r.url) if "/events" in r.url else None,
    )

    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    start_recording(page)
    # openPartialStream() fires inside startRecording() before aria-pressed
    # flips (which start_recording already waited for); give the SSE request
    # a beat to leave the browser.
    page.wait_for_timeout(800)

    assert events_requests, (
        "live-partial SSE stream (/events) was never opened after a failed "
        "/api/config — openPartialStream() gated itself off on a fallback "
        "config (issue #87)"
    )
