"""The webapp serves responses through the vendored stream-safe gzip middleware.

``tests/webapp/test_webapp_api_basics.py::TestCompression`` pins gzip on the
shell and the main script; this file pins the wiring and that the one streaming
route (the session SSE feed) still delivers its first chunk at once.
"""

from __future__ import annotations

# Standard library imports
import asyncio

# Third-party imports
from starlette.middleware.gzip import GZipMiddleware

# Local imports
from src.compression import StreamSafeGZipMiddleware


class TestMiddlewareWiring:
    def test_stream_safe_gzip_is_the_outermost_layer(self, webapp_client):
        _, app, _ = webapp_client
        # ``user_middleware`` lists the outermost layer first.
        assert app.user_middleware[0].cls is StreamSafeGZipMiddleware

    def test_no_other_gzip_middleware(self, webapp_client):
        _, app, _ = webapp_client
        assert all(m.cls is not GZipMiddleware for m in app.user_middleware)

    def test_auth_failure_passes_through_uncompressed(self, webapp_client):
        client, app, _ = webapp_client
        app.state.webapp_config.auth_token = "secret"
        # TestClient is a loopback caller, which bypasses the gate; a
        # non-loopback client address is what meets the 401.
        from fastapi.testclient import TestClient

        remote = TestClient(app, client=("203.0.113.9", 50000))
        resp = remote.get("/api/config", headers={"Accept-Encoding": "gzip"})
        assert resp.status_code == 401
        assert "content-encoding" not in resp.headers


class TestSessionEventsStream:
    def test_first_sse_chunk_arrives_at_once_and_uncompressed(self, webapp_client):
        """Drive the ASGI app directly: ``TestClient`` buffers a response to
        completion, and this stream never completes."""
        _, app, _ = webapp_client
        session = app.state.archive.new_session()

        async def run():
            sent: list[dict] = []
            first_body = asyncio.Event()
            disconnect = asyncio.Event()

            async def receive():
                await disconnect.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)
                if message["type"] == "http.response.body" and message.get("body"):
                    first_body.set()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": f"/api/sessions/{session.session_id}/events",
                "raw_path": f"/api/sessions/{session.session_id}/events".encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [(b"accept-encoding", b"gzip")],
                "client": ("127.0.0.1", 50000),
                "server": ("127.0.0.1", 8443),
                "state": {},
            }
            task = asyncio.create_task(app(scope, receive, send))
            try:
                await asyncio.wait_for(first_body.wait(), timeout=5)
            finally:
                disconnect.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            return sent

        sent = asyncio.run(run())
        start = next(m for m in sent if m["type"] == "http.response.start")
        headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
        assert start["status"] == 200
        assert headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in headers
        first = next(m for m in sent if m["type"] == "http.response.body" and m.get("body"))
        assert first["body"] == b":ok\n\n"
