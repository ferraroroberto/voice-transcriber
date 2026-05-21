"""Cross-router helpers + shared paths.

No router imports another router; anything two routers both need lives
here instead.
"""

from __future__ import annotations

# Standard library imports
from pathlib import Path
from typing import Any, Dict

# Third-party imports
from fastapi import Request

# routers/_helpers.py → routers → webapp → app → repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


async def maybe_json(request: Request) -> Dict[str, Any]:
    """Parse a JSON body, tolerating a missing/empty/non-JSON request.

    Returns an empty dict rather than raising so handlers can treat the
    body as optional (``POST /api/sessions`` with no payload, etc.).
    """
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}
