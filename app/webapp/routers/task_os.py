"""Send a saved take to task-os.

``POST /api/sessions/{session_id}/task-os`` is what a take's menu row calls.
The server makes the call rather than the phone: task-os trusts this PC as
its owner, and the phone may be outside the tailnet behind the tunnel.
"""

from __future__ import annotations

# Standard library imports
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, HTTPException, Request

from app.webapp.route_pools import SERVICE_CALLS, run_on
from app.webapp.routers._helpers import current_webapp_config
from src.task_os import TaskOsError, send_to_task_os, session_external_id

router = APIRouter()


@router.post("/api/sessions/{session_id}/task-os")
async def send_take_to_task_os(session_id: str, request: Request) -> Dict[str, Any]:
    """Make an Inbox task in task-os from the take's polished text (else its transcript).

    409 while the setting is off, 404 for an unknown take; task-os failures
    keep their own status (see :func:`src.task_os.send_to_task_os`).
    """
    cfg = current_webapp_config(request.app)
    if not cfg.task_os_enabled:
        raise HTTPException(status_code=409, detail="Send to task-os is off in Settings")
    session = request.app.state.archive.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    text = session.read_polished() or session.read_transcript() or ""
    try:
        result = await run_on(
            SERVICE_CALLS, send_to_task_os, cfg.task_os_url, text, session_external_id(session_id),
        )
    except TaskOsError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {"ok": True, "outcome": result.outcome, "task_id": result.task_id, "title": result.title}
