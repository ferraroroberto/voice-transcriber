"""Password → bearer-token login.

Owns the dedicated ``vt.auth`` logger too: password attempts are written
to ``webapp/auth.log`` in addition to the normal stderr stream so failed
attempts are easy to find without scrolling through full server logs.
``ensure_log_handler()`` is called once from ``create_app()``.
"""

from __future__ import annotations

# Standard library imports
import hmac
import logging
from pathlib import Path
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, HTTPException, Request

from src.webapp_config import WebappConfig

from app.webapp.routers._helpers import PROJECT_ROOT, maybe_json

logger = logging.getLogger(__name__)

# Dedicated logger for password attempts — written to webapp/auth.log.
auth_logger = logging.getLogger("vt.auth")
_AUTH_LOG_PATH = PROJECT_ROOT / "webapp" / "auth.log"

router = APIRouter()


def ensure_log_handler() -> None:
    """Attach a file handler for ``webapp/auth.log`` to ``vt.auth`` once."""
    if any(
        isinstance(h, logging.FileHandler)
        and Path(h.baseFilename).resolve() == _AUTH_LOG_PATH.resolve()
        for h in auth_logger.handlers
    ):
        return
    try:
        _AUTH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(_AUTH_LOG_PATH, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        auth_logger.addHandler(fh)
        auth_logger.setLevel(logging.INFO)
    except OSError as exc:
        logger.warning(f"⚠️  Could not open {_AUTH_LOG_PATH}: {exc}")


@router.post("/api/login")
async def login(request: Request) -> Dict[str, Any]:
    """Swap a password for the bearer token.

    Used by the page when no token is in localStorage — typical on a
    fresh device or inside an iOS PWA whose storage is partitioned from
    Safari's. Failed attempts are logged with the client IP to
    webapp/auth.log so suspicious access is visible.
    """
    cfg: WebappConfig = request.app.state.webapp_config
    client_host = request.client.host if request.client else "?"
    if not cfg.auth_password:
        auth_logger.info(
            f"⚠️  Login attempt from {client_host} but no auth_password "
            "configured — password auth disabled"
        )
        raise HTTPException(
            status_code=503,
            detail="password auth not configured",
        )
    if not cfg.auth_token:
        auth_logger.info(
            f"⚠️  Login attempt from {client_host} but no auth_token "
            "configured — nothing to hand back"
        )
        raise HTTPException(
            status_code=503,
            detail="bearer token not configured",
        )
    body = await maybe_json(request)
    presented = str(body.get("password") or "")
    if not presented or not hmac.compare_digest(presented, cfg.auth_password):
        auth_logger.warning(
            f"🚨 Failed password attempt from {client_host} "
            f"(presented: {len(presented)} chars)"
        )
        raise HTTPException(status_code=401, detail="bad password")
    auth_logger.info(f"🔓 Password login from {client_host}")
    return {"token": cfg.auth_token}
