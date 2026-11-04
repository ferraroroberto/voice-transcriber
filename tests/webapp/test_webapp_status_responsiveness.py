"""A stuck /api/status probe must not hold up any other endpoint.

``/api/version`` and ``/api/analytics/summary`` were slow only because
``/api/status`` ran its blocking whisper/hub probes on the event loop. These
tests run them concurrently through one ASGI app on one event loop, with a
probe that blocks until the test releases it.
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from src.probe_cache import ProbeCache

_WHISPER_UP = {"running": True, "ownership": "external", "base_url": "http://stub:8090", "detail": "up"}


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _stuck_hub_cache(app, release: threading.Event, wait_s: float) -> ProbeCache:
    return ProbeCache(
        {
            "whisper": lambda: _WHISPER_UP,
            "llm_hub": lambda: release.wait(10) or True,
            "ffmpeg": lambda: True,
        },
        wait_s=wait_s,
    )


@pytest.mark.parametrize("path", ["/api/version", "/api/analytics/summary", "/api/config", "/healthz"])
def test_other_endpoints_answer_while_a_status_probe_is_stuck(webapp_client, path):
    _, app, overrides = webapp_client
    release = threading.Event()
    # The hub client blocks, as a busy hub does; the app's own status cache is kept.
    overrides["polish"].is_reachable.side_effect = lambda: release.wait(10) or True

    async def scenario():
        async with _client(app) as c:
            # Timed from before status starts: a blocked loop also delays the
            # sleep and this request, so the wait shows up in the total.
            started = time.monotonic()
            status_task = asyncio.create_task(c.get("/api/status"))
            await asyncio.sleep(0.05)  # status is now inside its probe
            r = await c.get(path)
            elapsed = time.monotonic() - started
            release.set()
            await status_task
            return r.status_code, elapsed

    try:
        code, elapsed = asyncio.run(scenario())
    finally:
        release.set()
    assert code == 200
    assert elapsed < 0.5, f"{path} waited {elapsed:.2f}s behind a stuck status probe"


def test_status_reports_a_stuck_probe_as_unknown_within_its_wait(webapp_client):
    _, app, _ = webapp_client
    release = threading.Event()
    app.state.status_cache = _stuck_hub_cache(app, release, wait_s=0.05)

    async def scenario():
        async with _client(app) as c:
            started = time.monotonic()
            r = await c.get("/api/status")
            return r.json(), time.monotonic() - started

    try:
        body, elapsed = asyncio.run(scenario())
    finally:
        release.set()
    assert elapsed < 1.0
    assert body["llm_hub"]["reachable"] is None  # not answered: unknown, neither up nor down
    assert body["whisper"]["running"] is True
    assert body["ffmpeg_present"] is True
    assert body["age_seconds"]["llm_hub"] is None
    assert body["age_seconds"]["whisper"] is not None


def test_status_whisper_that_never_answered_is_unknown(webapp_client):
    _, app, _ = webapp_client

    def broken():
        raise RuntimeError("probe broke")

    app.state.status_cache = ProbeCache(
        {"whisper": broken, "llm_hub": lambda: True, "ffmpeg": lambda: True},
        wait_s=0.05,
    )
    body = TestClient(app).get("/api/status").json()
    assert body["whisper"]["running"] is None
    assert body["whisper"]["detail"] == "not checked yet"
    assert body["whisper"]["base_url"] == "http://stub:8090"
    assert body["llm_hub"]["reachable"] is True
