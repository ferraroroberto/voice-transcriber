"""Send a take to task-os as an Inbox task.

One seam, :func:`send_to_task_os`, over task-os's ``POST /api/tasks``. Both
the webapp's take menu and the tk window's transcript box call it, so the
payload shape and the outcome mapping live in one place.

task-os treats a loopback caller as its owner, so a same-PC task-os needs no
token. Its certificate is issued for the machine's tailnet name, not
``127.0.0.1``, so a loopback URL skips certificate verification: the request
never leaves this PC. Any other host is verified as usual.

Every send carries an ``external_id``; task-os makes the task once and hands
the same task back on a replay, so a double tap or a retry after a timeout
never makes a second task.
"""

from __future__ import annotations

# Standard library imports
import hashlib
import logging
import warnings
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

# Third-party imports
import requests
from urllib3.exceptions import InsecureRequestWarning

from .pooled_http import pooled_request

logger = logging.getLogger(__name__)

# task-os stores this as the task's creator, so a sent take is attributable.
ACTOR = "voice-transcriber"

# A task row has room for one line; the full take goes in the description.
TITLE_MAX = 120

# A same-PC POST answers in milliseconds; this only bounds a wedged task-os.
DEFAULT_TIMEOUT_S = 6.0

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class TaskOsError(Exception):
    """task-os refused the task or could not be reached.

    ``status`` is the HTTP status the webapp answers with, so each failure
    reaches the phone as its own message.
    """

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class TaskOsResult:
    """A task task-os holds for the take: ``created`` now, or ``existing``."""

    outcome: str
    task_id: Optional[int]
    title: str


def build_title(text: str) -> str:
    """The take's first line, whitespace collapsed, capped at ``TITLE_MAX``."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    title = " ".join(first.split())
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1].rstrip() + "…"
    return title


def build_payload(text: str, external_id: str) -> Dict[str, Any]:
    """The ``POST /api/tasks`` body; the description only when the title cut the take."""
    body = text.strip()
    title = build_title(body)
    payload: Dict[str, Any] = {"title": title, "external_id": external_id, "actor": ACTOR}
    if body != title:
        payload["description"] = body
    return payload


def session_external_id(session_id: str) -> str:
    """The idempotency key for a saved take."""
    return f"{ACTOR}:take:{session_id}"


def text_external_id(text: str) -> str:
    """The idempotency key for text with no saved take behind it (the tk box)."""
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]
    return f"{ACTOR}:text:{digest}"


def is_loopback_url(url: str) -> bool:
    return (urlparse(url).hostname or "") in _LOOPBACK_HOSTS


def send_to_task_os(
    base_url: str,
    text: str,
    external_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> TaskOsResult:
    """POST ``text`` to task-os as a task and report what task-os holds now.

    Raises :class:`TaskOsError` with status 400 for empty text, 503 when
    task-os does not answer, 410 when the task made from this take was
    deleted in task-os, and 502 for any other refusal.
    """
    if not text.strip():
        raise TaskOsError("This take has no text", status=400)
    url = f"{base_url.rstrip('/')}/api/tasks"
    payload = build_payload(text, external_id)
    verify = not is_loopback_url(url)
    try:
        with warnings.catch_warnings():
            # The unverified request is loopback-only; its warning says nothing new.
            warnings.simplefilter("ignore", InsecureRequestWarning)
            resp = pooled_request("POST", url, timeout=timeout, json=payload, verify=verify)
    except requests.Timeout as exc:
        logger.warning(f"⚠️  task-os did not answer within {timeout:g}s at {url}: {exc}")
        raise TaskOsError(f"task-os did not answer within {timeout:g}s", status=503) from exc
    except requests.RequestException as exc:
        logger.warning(f"⚠️  task-os is not reachable at {url}: {exc}")
        raise TaskOsError("task-os is not reachable", status=503) from exc

    if resp.status_code == 410:
        raise TaskOsError("This take's task was deleted in task-os", status=410)
    if resp.status_code not in (200, 201):
        logger.warning(f"⚠️  task-os refused the task: HTTP {resp.status_code} {resp.text[:300]}")
        raise TaskOsError(f"task-os refused the task (HTTP {resp.status_code})", status=502)
    try:
        task = resp.json()
    except ValueError:
        task = {}
    outcome = "created" if resp.status_code == 201 else "existing"
    task_id = task.get("id") if isinstance(task, dict) else None
    logger.info(f"✅ task-os {outcome} task {task_id} for {external_id}")
    return TaskOsResult(outcome=outcome, task_id=task_id, title=payload["title"])
