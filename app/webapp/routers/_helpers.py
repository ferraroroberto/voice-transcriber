"""Cross-router helpers + shared paths.

No router imports another router; anything two routers both need lives
here instead.
"""

from __future__ import annotations

# Standard library imports
from pathlib import Path
from typing import Any, Dict

# Third-party imports
from fastapi import FastAPI, Request

from src.webapp_config import (
    WebappConfig, config_mtime, reload_if_changed,
)

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


def adopt_webapp_config(app: FastAPI, cfg: WebappConfig) -> None:
    """Make ``cfg`` the served config and note the file version it matches."""
    app.state.webapp_config = cfg
    app.state.webapp_config_mtime = config_mtime()


def current_webapp_config(app: FastAPI) -> WebappConfig:
    """The served config, re-read first if the tk window rewrote the file.

    The webapp is its own process, so settings the tk window saves (gain
    boost, polish defaults) reach it only through the shared file.
    """
    cfg, mtime = reload_if_changed(
        app.state.webapp_config, getattr(app.state, "webapp_config_mtime", 0.0),
    )
    app.state.webapp_config = cfg
    app.state.webapp_config_mtime = mtime
    return cfg
