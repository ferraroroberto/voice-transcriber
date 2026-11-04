"""Usage analytics API — ``GET /api/analytics/summary``.

A single aggregate endpoint backing the compact analytics line in the
webapp's History card: today's take count, words/min, and estimated time
saved vs. typing. See :mod:`src.analytics` for the computation, which reads
from the persistent activity log rather than the archive.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Dict

from fastapi import APIRouter

from src.analytics import compute_usage_summary

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/analytics/summary")
def get_analytics_summary() -> Dict[str, Any]:
    # A plain ``def`` runs on a worker thread: the SQLite read must not block
    # the event loop that every other request shares.
    return asdict(compute_usage_summary())
