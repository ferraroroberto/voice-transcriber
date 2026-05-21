"""Static page, liveness probe, build identity, iOS CA profile.

The catch-all routes that aren't about config or sessions: the SPA
document itself, ``/healthz``, ``/api/version``, and the one-tap iOS
``.mobileconfig`` install.
"""

from __future__ import annotations

# Standard library imports
import logging
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from app.webapp.routers._helpers import STATIC_DIR

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def index(request: Request) -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="index.html missing")
    # Stamp the asset URLs with their content hash and force the entry
    # document to revalidate, so a tray restart after an edit is always
    # picked up — no stale iOS PWA cache.
    build_info = request.app.state.build_info
    html = build_info.stamp_html(index_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


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
