"""Password → bearer-token login.

Owns the dedicated ``vt.auth`` logger too: password attempts are written
to ``webapp/auth.log`` in addition to the normal stderr stream so failed
attempts are easy to find without scrolling through full server logs.
``ensure_log_handler()`` is called once from ``create_app()``.

This route is deliberately outside the bearer gate — a device with no
token has to be able to reach it — so it is the one endpoint a caller can
exercise repeatedly without presenting a credential. The vendored
``LoginLockout`` bounds that: past a small free allowance, each further
rejected attempt from the same client has to wait out a doubling delay,
and the owner is alerted when someone is guessing.
"""

from __future__ import annotations

# Standard library imports
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Third-party imports
from fastapi import APIRouter, HTTPException, Request

from app.webapp.middleware import is_pc_itself
from app.webapp.routers._helpers import current_webapp_config, maybe_json
from src import password
from src.login_lockout import LockoutAlert, LoginLockout, retry_after_header
from src.notify import Notifier, NotifierError, TelegramConfig, build_notifier
from src.webapp_config import WebappConfig

logger = logging.getLogger(__name__)

# Dedicated logger for password attempts — written to webapp/auth.log.
auth_logger = logging.getLogger("vt.auth")
_AUTH_LOG_PATH = Path(__file__).resolve().parents[3] / "webapp" / "auth.log"

APP_NAME = "voice-transcriber"

router = APIRouter()


def make_login_alert(
    get_config: Callable[[], WebappConfig],
    *,
    build: Callable[..., Optional[Notifier]] = build_notifier,
) -> Callable[[LockoutAlert], None]:
    """Build the ``on_alert`` callback that tells the owner about guessing.

    The send runs on a daemon thread so the login request never waits on
    Telegram. The notifier is built per alert from the live config, so a
    token or chat id changed on disk applies without a restart. The text
    holds the app name, the client address and the failure count only.
    """

    def alert_owner(alert: LockoutAlert) -> None:
        cfg = get_config()
        notifier = build(
            cfg.notifier,
            TelegramConfig(cfg.telegram_bot_token, cfg.telegram_alert_chat_id),
        )
        if notifier is None:
            return
        held = f" (+{alert.suppressed} more held back)" if alert.suppressed else ""
        text = (
            f"🚨 {APP_NAME}: {alert.failures} wrong passwords in a row "
            f"from {alert.key}{held}"
        )

        def send() -> None:
            try:
                notifier.send_text(text)
            except NotifierError as exc:
                logger.warning(f"⚠️  login alert not delivered: {type(exc).__name__}")

        threading.Thread(target=send, name="login-alert", daemon=True).start()

    return alert_owner


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
    cfg: WebappConfig = current_webapp_config(request.app)
    client_host = request.client.host if request.client else "?"
    lockout: LoginLockout = request.app.state.login_lockout
    exempt = is_pc_itself(request)
    wait = 0.0 if exempt else lockout.retry_after(client_host)
    if wait > 0:
        auth_logger.warning(
            f"🚨 Throttled attempt from {client_host} "
            f"({wait:.0f}s remaining)"
        )
        raise HTTPException(
            status_code=429,
            detail="too many attempts — try again later",
            headers=retry_after_header(wait),
        )
    if not password.is_configured(legacy=cfg.auth_password):
        auth_logger.info(
            f"⚠️  Login attempt from {client_host} but no password "
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
    if not password.check_password(presented, legacy=cfg.auth_password):
        if not exempt:
            lockout.record_failure(client_host)
        auth_logger.warning(
            f"🚨 Failed password attempt from {client_host} "
            f"(presented: {len(presented)} chars)"
        )
        raise HTTPException(status_code=401, detail="bad password")
    lockout.reset(client_host)
    auth_logger.info(f"🔓 Password login from {client_host}")
    return {"token": cfg.auth_token}
