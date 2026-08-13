"""Password → bearer-token login.

Owns the dedicated ``vt.auth`` logger too: password attempts are written
to ``webapp/auth.log`` in addition to the normal stderr stream so failed
attempts are easy to find without scrolling through full server logs.
``ensure_log_handler()`` is called once from ``create_app()``.

This route is deliberately outside the bearer gate — a device with no
token has to be able to reach it — so it is the one endpoint a caller can
exercise repeatedly without presenting a credential. ``AttemptLimiter``
below bounds that: past a small free allowance, each further rejected
attempt from the same client has to wait out a doubling delay.
"""

from __future__ import annotations

# Standard library imports
import hmac
import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Third-party imports
from fastapi import APIRouter, HTTPException, Request

from src.webapp_config import WebappConfig

from app.webapp.routers._helpers import PROJECT_ROOT, maybe_json

logger = logging.getLogger(__name__)

# Dedicated logger for password attempts — written to webapp/auth.log.
auth_logger = logging.getLogger("vt.auth")
_AUTH_LOG_PATH = PROJECT_ROOT / "webapp" / "auth.log"

# Attempts allowed per client before the delay starts, the first delay,
# and its ceiling. Five covers a fat-fingered human on a phone keyboard;
# the doubling past that turns an unbounded attempt rate into a handful
# per hour without ever locking the owner out permanently.
FREE_ATTEMPTS = 5
BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 300.0
# Cap on tracked clients so a caller rotating source addresses can't grow
# the table without bound; expired entries are dropped when it fills.
MAX_TRACKED_CLIENTS = 1024

router = APIRouter()


class AttemptLimiter:
    """Per-client rejected-attempt counter with exponential backoff.

    ``retry_after`` returns the seconds a client still has to wait (0 when
    it may proceed), ``record_failure`` books a rejection, and ``reset``
    clears a client's history once it succeeds. ``now`` is injectable so
    the backoff schedule can be tested without sleeping.
    """

    def __init__(
        self,
        *,
        free_attempts: int = FREE_ATTEMPTS,
        base_delay: float = BASE_DELAY_SECONDS,
        max_delay: float = MAX_DELAY_SECONDS,
    ) -> None:
        self._free = free_attempts
        self._base = base_delay
        self._max = max_delay
        # client key -> (failure count, monotonic deadline it may retry at)
        self._state: Dict[str, Tuple[int, float]] = {}

    def retry_after(self, key: str, *, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        _, deadline = self._state.get(key, (0, 0.0))
        return max(0.0, deadline - now)

    def record_failure(self, key: str, *, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        failures = self._state.get(key, (0, 0.0))[0] + 1
        if failures <= self._free:
            delay = 0.0
        else:
            delay = min(self._base * 2 ** (failures - self._free - 1), self._max)
        if len(self._state) >= MAX_TRACKED_CLIENTS and key not in self._state:
            self._evict_expired(now)
        self._state[key] = (failures, now + delay)

    def reset(self, key: str) -> None:
        self._state.pop(key, None)

    def _evict_expired(self, now: float) -> None:
        for k, (_, deadline) in list(self._state.items()):
            if deadline <= now:
                del self._state[k]


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
    limiter: AttemptLimiter = request.app.state.login_limiter
    wait = limiter.retry_after(client_host)
    if wait > 0:
        auth_logger.warning(
            f"🚨 Throttled attempt from {client_host} "
            f"({wait:.0f}s remaining)"
        )
        raise HTTPException(
            status_code=429,
            detail="too many attempts — try again later",
            headers={"Retry-After": str(int(math.ceil(wait)))},
        )
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
        limiter.record_failure(client_host)
        auth_logger.warning(
            f"🚨 Failed password attempt from {client_host} "
            f"(presented: {len(presented)} chars)"
        )
        raise HTTPException(status_code=401, detail="bad password")
    limiter.reset(client_host)
    auth_logger.info(f"🔓 Password login from {client_host}")
    return {"token": cfg.auth_token}
