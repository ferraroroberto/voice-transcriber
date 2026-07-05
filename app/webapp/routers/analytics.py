"""Usage analytics API — ``GET /api/analytics/summary`` (issue #95).

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
async def get_analytics_summary() -> Dict[str, Any]:
    return asdict(compute_usage_summary())
