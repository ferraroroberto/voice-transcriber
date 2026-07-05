"""Read-only API over the persistent activity log (:mod:`src.activity_log`).

``GET /api/activity`` — the durable event trail survives the archive's
30-day retention sweep, so this is where to look for what happened to a
session (or why a transcription/polish call failed) after its
``archive/`` folder is long gone. Events are written by the session
routes in ``sessions.py``, not here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter

from src import activity_log

logger = logging.getLogger(__name__)

router = APIRouter()

# Cap a single page so a typo'd query can't pull the whole year of events.
_MAX_LIMIT = 500


@router.get("/api/activity")
async def get_activity(
    event_type: Optional[str] = None,
    since: Optional[int] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return recent activity-log events, newest first.

    ``event_type`` (e.g. ``transcribed`` / ``polish_failed`` /
    ``session_created``) is exact-match; ``since`` is an epoch-second
    lower bound; ``limit`` is clamped to ``[1, 500]``.
    """
    safe_limit = max(1, min(int(limit or 100), _MAX_LIMIT))
    events = activity_log.read_events(
        event_type=event_type, since=since, limit=safe_limit,
    )
    return {"events": events, "count": len(events)}
