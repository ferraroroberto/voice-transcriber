"""Static page, liveness probe, build identity, iOS CA profile.

The catch-all routes that aren't about config or sessions: the SPA
document itself, ``/healthz``, ``/api/version``, and the one-tap iOS
``.mobileconfig`` install.
"""

from __future__ import annotations

# Standard library imports
import hashlib
import logging
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from src.static_versioning import BuildInfo

from app.webapp.routers._helpers import STATIC_DIR

logger = logging.getLogger(__name__)

router = APIRouter()


_INDEX_CACHE_CONTROL = "no-cache, must-revalidate"


def _entry_etag(build_info: BuildInfo, html: str) -> str:
    """Weak validator for the stamped entry document.

    Weak because the bytes on the wire vary with ``Content-Encoding``.
    Derived from the stamped body *and* the build fingerprint, so any edit
    or new build changes it.
    """
    digest = hashlib.sha256(build_info.fingerprint().encode("utf-8"))
    digest.update(html.encode("utf-8"))
    return f'W/"{digest.hexdigest()[:20]}"'


def _if_none_match_hits(header: str, etag: str) -> bool:
    """RFC 9110 weak comparison of ``If-None-Match`` against ``etag``."""
    if not header:
        return False
    header = header.strip()
    if header == "*":
        return True
    ours = etag.removeprefix("W/")
    return any(
        candidate.strip().removeprefix("W/") == ours
        for candidate in header.split(",")
    )


@router.get("/")
async def index(request: Request) -> Response:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="index.html missing")
    # Stamp the asset URLs with their content hash and force the entry
    # document to revalidate, so a tray restart after an edit is always
    # picked up — no stale iOS PWA cache. The ETag lets that revalidation
    # answer 304 with no body when nothing changed.
    build_info = request.app.state.build_info
    html = build_info.stamp_html(index_path.read_text(encoding="utf-8"))
    headers = {
        "Cache-Control": _INDEX_CACHE_CONTROL,
        "ETag": _entry_etag(build_info, html),
    }
    if _if_none_match_hits(request.headers.get("if-none-match", ""), headers["ETag"]):
        return Response(status_code=304, headers=headers)
    return HTMLResponse(html, headers=headers)


@router.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "service": "voice-transcriber-webapp"}


@router.get("/api/version")
async def version(request: Request) -> Dict[str, str]:
    """Build identity so the phone (and tests) can confirm which build
    is loaded — see issue #13."""
    return request.app.state.build_info.as_dict()


@router.get("/install-ca")
async def install_ca() -> FileResponse:
    """Serve the iOS .mobileconfig for one-tap CA install (Phase 3)."""
    profile = STATIC_DIR / "voice-transcriber-ca.mobileconfig"
    if not profile.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "CA profile not generated yet. Run "
                "`scripts/gen_ssl_cert.py` from the project root."
            ),
        )
    return FileResponse(
        str(profile),
        media_type="application/x-apple-aspen-config",
        filename="voice-transcriber-ca.mobileconfig",
    )
